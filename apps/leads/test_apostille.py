from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections, transaction
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.test import SimpleTestCase, TestCase, TransactionTestCase, skipUnlessDBFeature

from apps.accounts.models import User
from apps.leads.models import Apostille, Lead
from apps.performance.apostille import calculate_apostille_points
from apps.performance.models import PointsEntry
from apps.performance.services import award


class ApostillePointTierTests(SimpleTestCase):
    def test_every_boundary(self):
        cases = [(0, 0), (1499, 0), (1500, 10), (2999, 10), (3000, 20), (4999, 20),
                 (5000, 30), (9999, 30), (10000, 50), (19999, 50), (20000, 100),
                 (29999, 100), (30000, 200), (39999, 200), (40000, 300), (49999, 300),
                 (50000, 500), (99999, 500), (100000, 1000), (999999, 1000)]
        for amount, points in cases:
            with self.subTest(amount=amount):
                self.assertEqual(calculate_apostille_points(amount), points)
                self.assertEqual(calculate_apostille_points(Decimal(amount) + Decimal('.99')), points)

    def test_invalid_amounts(self):
        for amount in ('-1', 'NaN', 'Infinity', 'invalid', None):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                calculate_apostille_points(amount)


class ApostilleModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.caller = User.objects.create_user(username='apostille-caller', role='CALLER')
        cls.other = User.objects.create_user(username='apostille-other', role='CALLER')
        cls.admin = User.objects.create_user(username='apostille-admin', role='ADMIN')
        cls.lead = Lead.objects.create(name='Existing Student', phone='9876543210', assigned_caller=cls.caller)

    def make(self, **changes):
        values = dict(name='External Candidate', phone='+91 98765 43210', country='India',
                      number_of_documents=2, amount_received='2000', caller=self.caller, created_by=self.admin)
        values.update(changes)
        return Apostille.objects.create(**values)

    def totals(self):
        return dict(PointsEntry.objects.filter(event='APOSTILLE').order_by().values('caller_id')
                    .annotate(total=Sum('points')).values_list('caller_id', 'total'))

    def test_external_model_and_caller(self):
        record = self.make()
        self.assertIsNone(record.lead_id)
        self.assertEqual(record.candidate_name, 'External Candidate')
        self.assertEqual(record.candidate_phone, '919876543210')
        self.assertEqual(record.caller, self.caller)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(record.points, 10)
        self.assertEqual(self.totals(), {self.caller.pk: 10})

    def test_existing_lead_leaves_lead_and_assignment_unchanged(self):
        before = Lead.objects.values().get(pk=self.lead.pk)
        record = self.make(candidate_type='EXISTING_LEAD', lead=self.lead, name='', phone='')
        self.assertEqual(record.candidate_name, self.lead.name)
        self.assertEqual(record.candidate_phone, self.lead.phone)
        self.assertEqual(before, Lead.objects.values().get(pk=self.lead.pk))
        self.assertEqual(record.points_entries.get().lead, self.lead)

    def test_invalid_candidate_data(self):
        for changes in ({'name': ''}, {'name': '  '}, {'phone': ''}, {'phone': '123'},
                        {'phone': '1' * 16}, {'country': '  '}, {'country': ''},
                        {'candidate_type': 'WRONG'}, {'lead': self.lead},
                        {'candidate_type': 'EXISTING_LEAD', 'name': '', 'phone': ''},
                        {'candidate_type': 'EXISTING_LEAD', 'lead': self.lead},
                        {'candidate_type': 'EXISTING_LEAD', 'lead_id': 999999, 'name': '', 'phone': ''},
                        {'caller': None}, {'caller': self.admin}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make(**changes)
        self.assertFalse(Apostille.objects.exists())
        self.assertFalse(PointsEntry.objects.filter(event='APOSTILLE').exists())

    def test_invalid_documents_and_amount(self):
        for field, values in [('number_of_documents', [0, -1, 1.5, '1.5', None, True]),
                              ('amount_received', [-1, 'NaN', 'Infinity', '0.001', '10000000000', None])]:
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    self.make(**{field: value})

    def test_invalid_existing_lead_details(self):
        self.lead.phone = '123'
        self.lead.save()
        with self.assertRaises(ValidationError):
            self.make(candidate_type='EXISTING_LEAD', lead=self.lead, name='', phone='')

    def test_zero_award(self):
        self.make(amount_received='1499.99')
        self.assertFalse(PointsEntry.objects.filter(event='APOSTILLE').exists())

    def test_amount_increase_decrease_and_zero(self):
        record = self.make()
        for amount, expected in [('5000', 30), ('1500', 10), ('0', 0), ('100000', 1000)]:
            record.amount_received = amount
            record.save(actor=self.admin)
            self.assertEqual(self.totals(), {self.caller.pk: expected})
        self.assertEqual(list(record.points_entries.order_by('pk').values_list('points', flat=True)), [10, 20, -20, -10, 1000])
        self.assertEqual(record.points_entries.latest('pk').recorded_by, self.admin)

    def test_caller_transfer(self):
        record = self.make(amount_received=5000)
        record.caller = self.other
        record.save()
        self.assertEqual(self.totals(), {self.caller.pk: 0, self.other.pk: 30})
        self.assertEqual(record.points_entries.count(), 3)

    def test_caller_and_amount_transfer(self):
        record = self.make()
        record.caller = self.other
        record.amount_received = 5000
        record.save()
        self.assertEqual(self.totals(), {self.caller.pk: 0, self.other.pk: 30})

    def test_unchanged_and_same_tier_saves_do_not_duplicate(self):
        record = self.make()
        record.save()
        record.country = 'Canada'
        record.amount_received = 2999
        record.save()
        self.assertEqual(record.points_entries.count(), 1)

    def test_update_fields_scores_only_persisted_values(self):
        record = self.make()
        record.amount_received = 5000
        record.country = 'Canada'
        record.save(update_fields=['country'])
        record.refresh_from_db()
        self.assertEqual(record.amount_received, Decimal('2000'))
        self.assertEqual(self.totals(), {self.caller.pk: 10})

    def test_delete_retains_history_and_reverses_current_contribution(self):
        record = self.make()
        key = str(record.ledger_key)
        original = record.points_entries.get()
        record.caller = self.other
        record.amount_received = 5000
        record.save()
        record.delete()
        self.assertFalse(Apostille.objects.exists())
        self.assertEqual(self.totals(), {self.caller.pk: 0, self.other.pk: 0})
        entries = PointsEntry.objects.filter(event='APOSTILLE')
        self.assertEqual(entries.count(), 4)
        self.assertFalse(entries.exclude(apostille=None).exists())
        self.assertTrue(all(key in row.event_key for row in entries))
        original.refresh_from_db()
        self.assertEqual(original.points, 10)

    def test_queryset_delete_also_reverses(self):
        self.make()
        self.make(amount_received=5000)
        Apostille.objects.all().delete()
        self.assertEqual(self.totals(), {self.caller.pk: 0})

    def test_create_ledger_failure_rolls_back_record(self):
        with patch('apps.performance.apostille.award', side_effect=RuntimeError('ledger unavailable')):
            with self.assertRaises(RuntimeError):
                self.make()
        self.assertFalse(Apostille.objects.exists())
        self.assertFalse(PointsEntry.objects.filter(event='APOSTILLE').exists())

    def test_edit_failure_after_first_delta_rolls_back_everything(self):
        record = self.make()
        record.caller = self.other
        calls = 0

        def fail_second(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('second award failed')
            return award(**kwargs)

        with patch('apps.performance.apostille.award', side_effect=fail_second), self.assertRaises(RuntimeError):
            record.save()
        record.refresh_from_db()
        self.assertEqual(record.caller, self.caller)
        self.assertEqual(record.points_entries.count(), 1)
        self.assertEqual(self.totals(), {self.caller.pk: 10})

    def test_delete_failure_rolls_back(self):
        record = self.make()
        with patch('apps.performance.apostille.award', side_effect=RuntimeError('ledger unavailable')):
            with self.assertRaises(RuntimeError), transaction.atomic():
                record.delete()
        self.assertTrue(Apostille.objects.filter(pk=record.pk).exists())
        self.assertEqual(self.totals(), {self.caller.pk: 10})

    def test_outer_transaction_rollback_retains_record_and_points(self):
        record = self.make()
        pk = record.pk
        with self.assertRaises(RuntimeError), transaction.atomic():
            record.delete()
            raise RuntimeError('later failure')
        self.assertTrue(Apostille.objects.filter(pk=pk).exists())
        self.assertEqual(self.totals(), {self.caller.pk: 10})

    def test_unrelated_historical_points_untouched(self):
        historical = PointsEntry.objects.create(caller=self.caller, points=75, event='COUNSELLING',
                                               reason='Historical', event_key='historical:apostille-test')
        before = PointsEntry.objects.values().get(pk=historical.pk)
        record = self.make()
        record.amount_received = 5000
        record.save()
        record.delete()
        self.assertEqual(before, PointsEntry.objects.values().get(pk=historical.pk))

    def test_caller_and_linked_lead_are_protected(self):
        self.make(candidate_type='EXISTING_LEAD', lead=self.lead, name='', phone='')
        for obj in (self.lead, self.caller):
            with self.assertRaises(ProtectedError):
                obj.delete()

    def test_creator_can_be_deleted_without_deleting_record(self):
        record = self.make()
        self.admin.delete()
        record.refresh_from_db()
        self.assertIsNone(record.created_by)
        self.assertEqual(self.totals(), {self.caller.pk: 10})

    def test_bulk_writes_cannot_bypass_ledger(self):
        record = self.make()
        for operation in (lambda: Apostille.objects.update(amount_received=5000),
                          lambda: Apostille.objects.bulk_update([record], ['amount_received']),
                          lambda: Apostille.objects.bulk_create([record])):
            with self.assertRaises(ValidationError):
                operation()
        self.assertEqual(self.totals(), {self.caller.pk: 10})


@skipUnlessDBFeature('has_select_for_update')
class ApostilleConcurrencyTests(TransactionTestCase):
    def test_concurrent_edits_reconcile_against_locked_current_ledger(self):
        caller = User.objects.create_user(username='concurrent-a', role='CALLER')
        other = User.objects.create_user(username='concurrent-b', role='CALLER')
        record = Apostille.objects.create(name='Concurrent', phone='9876543210', country='India',
                                         number_of_documents=1, amount_received=2000, caller=caller)
        barrier = Barrier(2)

        def edit(caller_id, amount):
            close_old_connections()
            try:
                instance = Apostille.objects.get(pk=record.pk)
                instance.caller_id = caller_id
                instance.amount_received = amount
                barrier.wait(timeout=10)
                instance.save()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(edit, caller.pk, 5000), executor.submit(edit, other.pk, 10000)]
            for future in futures:
                future.result(timeout=30)
        record.refresh_from_db()
        totals = dict(record.points_entries.order_by().values('caller_id').annotate(total=Sum('points'))
                      .values_list('caller_id', 'total'))
        self.assertEqual(totals.get(record.caller_id), record.points)
        self.assertTrue(all(total == 0 for key, total in totals.items() if key != record.caller_id))
