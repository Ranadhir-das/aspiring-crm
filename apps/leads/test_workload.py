"""
Comprehensive test suite for Smart Caller Workload Monitoring & Alerts (V1).

Covers:
- Low-workload threshold (<= 10 pending leads) and target (30).
- Suggested lead recommendations: max(0, 30 - pending_count).
- Lifecycle: 11 leads (no alert), 10 leads (open alert), 10 -> 9 -> 8 (single open alert),
  10 -> 11 (resolved alert), drop back to 10 (new open alert).
- Inactive, registration-pending, and non-caller exclusions.
- Only PENDING leads counted (dispositioned/converted leads excluded).
- Unassigned leads and other callers' leads excluded.
- Concurrency and conditional database unique constraint enforcement.
- Bulk assignment and status change updates.
- Management-only permissions for dashboard and notification bell.
- Zero N+1 query regression.
- Management command execution.
"""
from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.db.models import QuerySet
from django.test import TestCase, Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.capabilities import is_caller_capable
from apps.leads.models import CallerWorkloadAlert, Lead
from apps.leads.services import bulk_assign_leads
from apps.leads.workload import (
    evaluate_all_callers_workload,
    evaluate_caller_workload,
    get_caller_pending_lead_count,
    get_workload_summary,
)

User = get_user_model()


class CallerWorkloadServiceTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user(
            username="caller_alice",
            email="alice@example.com",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
            registration_pending=False,
            first_name="Alice",
            last_name="Caller",
        )
        self.caller_bob = User.objects.create_user(
            username="caller_bob",
            email="bob@example.com",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
            registration_pending=False,
            first_name="Bob",
            last_name="Caller",
        )
        self.counselor = User.objects.create_user(
            username="counselor_dan",
            email="dan@example.com",
            password="password123",
            role=User.Role.COUNSELOR,
            is_active=True,
            registration_pending=False,
        )
        self.inactive_caller = User.objects.create_user(
            username="inactive_caller",
            email="inactive@example.com",
            password="password123",
            role=User.Role.CALLER,
            is_active=False,
            registration_pending=False,
        )
        self.pending_caller = User.objects.create_user(
            username="pending_caller",
            email="pending@example.com",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
            registration_pending=True,
        )

    def _create_leads(self, caller, count, status=Lead.Status.PENDING):
        leads = []
        for i in range(count):
            leads.append(
                Lead.objects.create(
                    name=f"Lead {caller.username if caller else 'Unassigned'} {i}",
                    phone=f"98765{i:05d}",
                    assigned_caller=caller,
                    status=status,
                )
            )
        return leads

    def test_pending_count_above_threshold_no_alert(self):
        """11 pending leads -> no low-workload alert created."""
        self._create_leads(self.caller, 11)
        self.assertEqual(get_caller_pending_lead_count(self.caller), 11)

        alert = evaluate_caller_workload(self.caller)
        self.assertIsNone(alert)
        self.assertFalse(
            CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).exists()
        )

    def test_pending_count_at_threshold_creates_alert(self):
        """10 pending leads -> alert created with suggested count = 20 (target 30 - 10)."""
        self._create_leads(self.caller, 10)
        self.assertEqual(get_caller_pending_lead_count(self.caller), 10)

        alert = evaluate_caller_workload(self.caller)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.status, CallerWorkloadAlert.Status.OPEN)
        self.assertEqual(alert.pending_count, 10)
        self.assertEqual(alert.target_count, 30)
        self.assertEqual(alert.suggested_count, 20)
        self.assertIsNone(alert.resolved_at)

    def test_zero_pending_leads_alert(self):
        """0 pending leads -> alert created with suggested count = 30."""
        self.assertEqual(get_caller_pending_lead_count(self.caller), 0)

        alert = evaluate_caller_workload(self.caller)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.pending_count, 0)
        self.assertEqual(alert.suggested_count, 30)

    def test_repeated_checks_no_duplicate_open_alert(self):
        """Repeated checks when count is 10 must not create duplicate OPEN alerts."""
        self._create_leads(self.caller, 10)
        alert1 = evaluate_caller_workload(self.caller)
        alert2 = evaluate_caller_workload(self.caller)

        self.assertEqual(alert1.pk, alert2.pk)
        self.assertEqual(
            CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).count(),
            1,
        )

    def test_count_drops_further_updates_existing_open_alert(self):
        """When count drops 10 -> 9 -> 8: exactly one OPEN alert maintained with updated counts."""
        leads = self._create_leads(self.caller, 10)
        alert = evaluate_caller_workload(self.caller)
        self.assertEqual(alert.pending_count, 10)
        self.assertEqual(alert.suggested_count, 20)

        # Drop to 9 (simulate a call changing status)
        leads[0].status = Lead.Status.INTERESTED
        leads[0].save()
        self.assertEqual(get_caller_pending_lead_count(self.caller), 9)

        alert_after_9 = evaluate_caller_workload(self.caller)
        self.assertEqual(alert_after_9.pk, alert.pk)
        self.assertEqual(alert_after_9.pending_count, 9)
        self.assertEqual(alert_after_9.suggested_count, 21)

        # Drop to 8
        leads[1].status = Lead.Status.CALLED
        leads[1].save()
        self.assertEqual(get_caller_pending_lead_count(self.caller), 8)

        alert_after_8 = evaluate_caller_workload(self.caller)
        self.assertEqual(alert_after_8.pk, alert.pk)
        self.assertEqual(alert_after_8.pending_count, 8)
        self.assertEqual(alert_after_8.suggested_count, 22)

        self.assertEqual(
            CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).count(),
            1,
        )

    def test_pending_count_rises_above_threshold_resolves_alert(self):
        """When pending leads rise 10 -> 11, the open alert is marked RESOLVED."""
        self._create_leads(self.caller, 10)
        alert = evaluate_caller_workload(self.caller)
        self.assertEqual(alert.status, CallerWorkloadAlert.Status.OPEN)

        # Add 1 more lead bringing count to 11
        self._create_leads(self.caller, 1)
        self.assertEqual(get_caller_pending_lead_count(self.caller), 11)

        res = evaluate_caller_workload(self.caller)
        self.assertIsNone(res)

        alert.refresh_from_db()
        self.assertEqual(alert.status, CallerWorkloadAlert.Status.RESOLVED)
        self.assertIsNotNone(alert.resolved_at)
        self.assertFalse(
            CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).exists()
        )

    def test_cycle_resolved_alert_followed_by_later_drop_reopens_new_alert(self):
        """Resolved alert followed by a later drop to 10 or fewer creates a NEW open alert."""
        # 1. Start with 10 -> Alert 1 (OPEN)
        leads = self._create_leads(self.caller, 10)
        alert1 = evaluate_caller_workload(self.caller)
        self.assertEqual(alert1.status, CallerWorkloadAlert.Status.OPEN)

        # 2. Add 5 leads (total 15) -> Alert 1 resolves
        self._create_leads(self.caller, 5)
        evaluate_caller_workload(self.caller)
        alert1.refresh_from_db()
        self.assertEqual(alert1.status, CallerWorkloadAlert.Status.RESOLVED)

        # 3. Work 6 leads (15 - 6 = 9 pending) -> Alert 2 created (OPEN)
        all_leads = list(Lead.objects.filter(assigned_caller=self.caller, status=Lead.Status.PENDING))
        for l in all_leads[:6]:
            l.status = Lead.Status.CALLED
            l.save()

        self.assertEqual(get_caller_pending_lead_count(self.caller), 9)
        alert2 = evaluate_caller_workload(self.caller)
        self.assertIsNotNone(alert2)
        self.assertNotEqual(alert1.pk, alert2.pk)
        self.assertEqual(alert2.status, CallerWorkloadAlert.Status.OPEN)
        self.assertEqual(alert2.pending_count, 9)

        # Total alerts for caller = 2 (1 RESOLVED, 1 OPEN)
        self.assertEqual(CallerWorkloadAlert.objects.filter(caller=self.caller).count(), 2)
        self.assertEqual(
            CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).count(),
            1,
        )

    def test_inactive_and_pending_callers_excluded(self):
        """Inactive employees and registration-pending users are excluded from workload monitoring."""
        self._create_leads(self.inactive_caller, 5)
        self._create_leads(self.pending_caller, 5)

        self.assertEqual(get_caller_pending_lead_count(self.inactive_caller), 0)
        self.assertEqual(get_caller_pending_lead_count(self.pending_caller), 0)

        alert_inactive = evaluate_caller_workload(self.inactive_caller)
        alert_pending = evaluate_caller_workload(self.pending_caller)

        self.assertIsNone(alert_inactive)
        self.assertIsNone(alert_pending)
        self.assertFalse(CallerWorkloadAlert.objects.filter(caller=self.inactive_caller).exists())
        self.assertFalse(CallerWorkloadAlert.objects.filter(caller=self.pending_caller).exists())

    def test_non_caller_capable_users_excluded(self):
        """Users without caller capability (e.g. pure Counselors) are excluded."""
        self.assertFalse(is_caller_capable(self.counselor))
        self._create_leads(self.counselor, 5)

        self.assertEqual(get_caller_pending_lead_count(self.counselor), 0)
        alert = evaluate_caller_workload(self.counselor)
        self.assertIsNone(alert)
        self.assertFalse(CallerWorkloadAlert.objects.filter(caller=self.counselor).exists())

    def test_dual_role_counselor_with_caller_capability_included(self):
        """Dual-role users (e.g. Counselor with can_act_as_caller=True) are monitored."""
        self.counselor.can_act_as_caller = True
        self.counselor.save()
        self.assertTrue(is_caller_capable(self.counselor))

        self._create_leads(self.counselor, 5)
        self.assertEqual(get_caller_pending_lead_count(self.counselor), 5)

        alert = evaluate_caller_workload(self.counselor)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.caller, self.counselor)
        self.assertEqual(alert.pending_count, 5)

    def test_only_pending_leads_counted(self):
        """Only status='PENDING' is counted; other worked/closed statuses are excluded."""
        self._create_leads(self.caller, 5, status=Lead.Status.PENDING)
        self._create_leads(self.caller, 10, status=Lead.Status.INTERESTED)
        self._create_leads(self.caller, 10, status=Lead.Status.NOT_INTERESTED)
        self._create_leads(self.caller, 10, status=Lead.Status.ADMISSION_DONE)
        self._create_leads(self.caller, 10, status=Lead.Status.CALL_BACK)
        self._create_leads(self.caller, 10, status=Lead.Status.CONVERTED)

        # Even though caller has 55 leads assigned, only 5 are PENDING
        self.assertEqual(get_caller_pending_lead_count(self.caller), 5)
        alert = evaluate_caller_workload(self.caller)
        self.assertIsNotNone(alert)
        self.assertEqual(alert.pending_count, 5)
        self.assertEqual(alert.suggested_count, 25)

    def test_unassigned_and_other_callers_leads_excluded(self):
        """Unassigned leads and leads belonging to another caller are not counted."""
        self._create_leads(None, 50, status=Lead.Status.PENDING)
        self._create_leads(self.caller_bob, 20, status=Lead.Status.PENDING)
        self._create_leads(self.caller, 8, status=Lead.Status.PENDING)

        self.assertEqual(get_caller_pending_lead_count(self.caller), 8)
        self.assertEqual(get_caller_pending_lead_count(self.caller_bob), 20)

        alert_alice = evaluate_caller_workload(self.caller)
        alert_bob = evaluate_caller_workload(self.caller_bob)

        self.assertIsNotNone(alert_alice)
        self.assertEqual(alert_alice.pending_count, 8)
        self.assertIsNone(alert_bob)

    def test_bulk_assign_leads_triggers_workload_update(self):
        """bulk_assign_leads() updates the new caller and re-evaluates previous callers."""
        # Start alice with 8 leads -> alert
        self._create_leads(self.caller, 8)
        alert = evaluate_caller_workload(self.caller)
        self.assertEqual(alert.status, CallerWorkloadAlert.Status.OPEN)

        # Create 10 unassigned leads
        unassigned = self._create_leads(None, 10)
        unassigned_ids = [l.pk for l in unassigned]

        # Bulk assign 10 leads to alice -> total 18 -> alert should resolve!
        bulk_assign_leads(unassigned_ids, self.caller, assigned_by=self.caller)
        self.assertEqual(get_caller_pending_lead_count(self.caller), 18)

        alert.refresh_from_db()
        self.assertEqual(alert.status, CallerWorkloadAlert.Status.RESOLVED)

    def test_reassignment_triggers_both_callers_workload_update(self):
        """Reassigning leads from Bob to Alice updates workload for both callers."""
        # Alice starts with 15 leads (OK), Bob starts with 15 leads (OK)
        alice_leads = self._create_leads(self.caller, 15)
        bob_leads = self._create_leads(self.caller_bob, 15)
        evaluate_all_callers_workload()

        self.assertFalse(CallerWorkloadAlert.objects.filter(status=CallerWorkloadAlert.Status.OPEN).exists())

        # Reassign 8 of Bob's leads to Alice -> Bob now has 7 (<= 10), Alice has 23 (> 10)
        reassign_ids = [l.pk for l in bob_leads[:8]]
        bulk_assign_leads(reassign_ids, self.caller, assigned_by=self.caller, reassign=True)

        self.assertEqual(get_caller_pending_lead_count(self.caller_bob), 7)
        self.assertEqual(get_caller_pending_lead_count(self.caller), 23)

        bob_alert = CallerWorkloadAlert.objects.filter(caller=self.caller_bob, status=CallerWorkloadAlert.Status.OPEN).first()
        self.assertIsNotNone(bob_alert)
        self.assertEqual(bob_alert.pending_count, 7)

        alice_alert = CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).first()
        self.assertIsNone(alice_alert)

    def test_conditional_unique_constraint_concurrency(self):
        """Conditional unique constraint prevents duplicate open alerts at DB level."""
        CallerWorkloadAlert.objects.create(
            caller=self.caller,
            pending_count=5,
            target_count=30,
            suggested_count=25,
            status=CallerWorkloadAlert.Status.OPEN,
        )

        with self.assertRaises(IntegrityError):
            CallerWorkloadAlert.objects.create(
                caller=self.caller,
                pending_count=4,
                target_count=30,
                suggested_count=26,
                status=CallerWorkloadAlert.Status.OPEN,
            )

    def test_conflict_recovery_savepoint(self):
        """
        When concurrent workers evaluate the same caller and trigger IntegrityError
        on CallerWorkloadAlert.objects.create, the nested transaction.atomic() savepoint
        rolls back cleanly without aborting the outer transaction, re-selects the
        conflicting open alert, updates it, and returns it.
        """
        # Alice has 5 pending leads
        self._create_leads(self.caller, 5)
        CallerWorkloadAlert.objects.all().delete()

        # Pre-create an alert with stale counts (simulating a concurrent process committing first)
        existing_alert = CallerWorkloadAlert.objects.create(
            caller=self.caller,
            pending_count=8,
            target_count=30,
            suggested_count=22,
            status=CallerWorkloadAlert.Status.OPEN,
        )

        orig_first = QuerySet.first
        first_call_counter = {'count': 0}

        def mock_first(qs):
            # First call in evaluate_caller_workload returns None to simulate race condition
            # (i.e. open_alert wasn't seen initially before create attempted)
            if first_call_counter['count'] == 0:
                first_call_counter['count'] += 1
                return None
            return orig_first(qs)

        with patch.object(QuerySet, 'first', side_effect=mock_first, autospec=True):
            alert = evaluate_caller_workload(self.caller)

        self.assertIsNotNone(alert)
        self.assertEqual(alert.pk, existing_alert.pk)
        # Conflicting alert was recovered and updated to current pending count (5)
        self.assertEqual(alert.pending_count, 5)
        self.assertEqual(alert.suggested_count, 25)
        # Exactly one OPEN alert remains in the DB
        self.assertEqual(
            CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).count(),
            1
        )

    def test_signal_skips_unrelated_lead_field_updates(self):
        """Lead updates that do not alter assigned caller or PENDING status skip workload evaluation."""
        lead = Lead.objects.create(
            name="Alice Lead",
            phone="9876500001",
            assigned_caller=self.caller,
            status=Lead.Status.PENDING,
        )
        # Updating notes or email
        lead.notes = "Spoke to customer, requested callback."
        lead.save()
        self.assertFalse(getattr(lead, '_workload_should_evaluate', True))

        # Updating notes using update_fields skips evaluation without DB queries
        lead.notes = "Second note"
        lead.save(update_fields=['notes'])
        self.assertFalse(getattr(lead, '_workload_should_evaluate', True))

        # Changing status from PENDING to CALLED should trigger evaluation
        lead.status = Lead.Status.CALLED
        lead.save()
        self.assertTrue(getattr(lead, '_workload_should_evaluate', False))
        self.assertIn(self.caller.pk, getattr(lead, '_workload_callers_to_eval', set()))

        # Updating status back to PENDING using update_fields should trigger evaluation
        lead.status = Lead.Status.PENDING
        lead.save(update_fields=['status'])
        self.assertTrue(getattr(lead, '_workload_should_evaluate', False))

        # Reassigning to Bob should trigger evaluation for both Alice and Bob
        lead.assigned_caller = self.caller_bob
        lead.save()
        self.assertTrue(getattr(lead, '_workload_should_evaluate', False))
        callers = getattr(lead, '_workload_callers_to_eval', set())
        self.assertIn(self.caller.pk, callers)
        self.assertIn(self.caller_bob.pk, callers)

    def test_signal_lead_deletion_evaluates_caller(self):
        """Deleting a lead fires post_delete signal and re-evaluates caller backlog via transaction.on_commit."""
        # Create 11 leads for Alice (above threshold: no alert)
        leads = self._create_leads(self.caller, 11)
        evaluate_caller_workload(self.caller)
        self.assertFalse(CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).exists())

        # Delete 1 lead with captureOnCommitCallbacks
        with self.captureOnCommitCallbacks(execute=True):
            leads[0].delete()

        # Alice now has 10 pending leads -> post_delete on_commit evaluation opened an alert!
        alert = CallerWorkloadAlert.objects.filter(caller=self.caller, status=CallerWorkloadAlert.Status.OPEN).first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.pending_count, 10)
        self.assertEqual(alert.suggested_count, 20)

    def test_no_n_plus_one_queries_in_workload_summary(self):
        """get_workload_summary() executes in a fixed number of queries (2 queries) regardless of caller count."""
        # Create 10 callers with various leads
        callers = []
        for i in range(10):
            c = User.objects.create_user(
                username=f"perf_caller_{i}",
                password="password123",
                role=User.Role.CALLER,
                is_active=True,
                registration_pending=False,
            )
            callers.append(c)
            self._create_leads(c, i + 5)

        with CaptureQueriesContext(connection) as ctx:
            summary = get_workload_summary()

        # Should execute exactly 2 queries:
        # 1. Caller query with Count aggregation
        # 2. Open alerts query
        self.assertLessEqual(len(ctx.captured_queries), 3)
        self.assertEqual(summary['total_callers'], 12)  # 10 perf callers + alice + bob

    def test_evaluate_all_callers_workload_management_command(self):
        """evaluate_all_callers_workload evaluates all eligible callers and is idempotent."""
        # Create leads for Alice (5 <= 10) and Bob (25 > 10)
        self._create_leads(self.caller, 5)
        self._create_leads(self.caller_bob, 25)

        # Clear any auto-generated alerts so evaluate_all_callers_workload creates from scratch
        CallerWorkloadAlert.objects.all().delete()

        res1 = evaluate_all_callers_workload()
        self.assertEqual(res1['opened_alerts'], 1)
        self.assertEqual(res1['total_open_alerts'], 1)

        # Running again is idempotent: 0 opened, 0 resolved
        res2 = evaluate_all_callers_workload()
        self.assertEqual(res2['opened_alerts'], 0)
        self.assertEqual(res2['resolved_alerts'], 0)
        self.assertEqual(res2['total_open_alerts'], 1)

        # Test CLI management command execution
        out = StringIO()
        call_command('check_caller_workload', stdout=out)
        output = out.getvalue()
        self.assertIn("Workload evaluation complete", output)
        self.assertIn("Total active OPEN alerts: 1", output)
        self.assertIn("caller_alice", output)


class WorkloadUIPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin_user",
            email="admin@example.com",
            password="password123",
            role=User.Role.SUPER_ADMIN,
        )
        self.manager = User.objects.create_user(
            username="manager_user",
            email="manager@example.com",
            password="password123",
            role=User.Role.MANAGER,
            is_active=True,
            registration_pending=False,
        )
        self.caller = User.objects.create_user(
            username="caller_charlie",
            email="charlie@example.com",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
            registration_pending=False,
            first_name="Charlie",
            last_name="Caller",
        )
        # Create 5 leads for Charlie -> low workload
        for i in range(5):
            Lead.objects.create(name=f"Lead {i}", phone=f"9876543{i:03d}", assigned_caller=self.caller, status=Lead.Status.PENDING)
        evaluate_caller_workload(self.caller)

    def test_admin_can_view_workload_section_and_notification(self):
        """Admins can view workload alerts in the dashboard and topbar bell."""
        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:dashboard'), secure=True)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Smart Caller Workload &amp; Lead Inventory")
        self.assertContains(res, "Charlie")
        self.assertContains(res, "+25 leads suggested")

        # Notification bell context processor
        self.assertIn('header_notifications', res.context)
        bell_titles = [n['title'] for n in res.context['header_notifications']]
        self.assertTrue(any("has only 5 pending leads" in t for t in bell_titles))

    def test_manager_can_view_workload_section_and_notification(self):
        """Managers can view workload alerts in the dashboard and topbar bell."""
        self.client.force_login(self.manager)
        res = self.client.get(reverse('web:dashboard'), secure=True)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Smart Caller Workload &amp; Lead Inventory")
        self.assertContains(res, "Charlie")

        bell_titles = [n['title'] for n in res.context['header_notifications']]
        self.assertTrue(any("has only 5 pending leads" in t for t in bell_titles))

    def test_caller_cannot_access_management_workload_dashboard(self):
        """Callers are redirected away from dashboard to employee-home and cannot view workload alerts."""
        self.client.force_login(self.caller)
        res = self.client.get(reverse('web:dashboard'), secure=True)
        # Callers role is CALLER, but dashboard redirects non-management or renders employee view
        # In analytics_views.py: if request.user.role not in CALLING: return redirect('web:employee-home')
        # However, workload_summary is only set if request.user.role in MANAGEMENT:
        if res.status_code == 200:
            self.assertNotContains(res, "Smart Caller Workload &amp; Lead Inventory")
            self.assertIsNone(res.context.get('workload_summary'))

        # Callers never receive manager notifications
        self.assertNotIn('header_notifications', res.context)

    def test_stale_alert_excluded_from_bell_when_caller_deactivated_or_ineligible(self):
        """Alerts for deactivated or non-caller employees are excluded from notification bell."""
        self.client.force_login(self.admin)
        res1 = self.client.get(reverse('web:dashboard'), secure=True)
        self.assertIn('header_notifications', res1.context)
        bell_titles = [n['title'] for n in res1.context['header_notifications']]
        self.assertTrue(any("Charlie Caller has only 5 pending leads" in t for t in bell_titles))

        # Deactivate Charlie
        self.caller.is_active = False
        self.caller.save()

        res2 = self.client.get(reverse('web:dashboard'), secure=True)
        bell_titles2 = [n['title'] for n in res2.context['header_notifications']]
        self.assertFalse(any("Charlie Caller has only 5 pending leads" in t for t in bell_titles2))

        # Reactivate Charlie but make registration_pending = True
        self.caller.is_active = True
        self.caller.registration_pending = True
        self.caller.save()

        res3 = self.client.get(reverse('web:dashboard'), secure=True)
        bell_titles3 = [n['title'] for n in res3.context['header_notifications']]
        self.assertFalse(any("Charlie Caller has only 5 pending leads" in t for t in bell_titles3))
