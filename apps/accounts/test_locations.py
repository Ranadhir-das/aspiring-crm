from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import User, CallerSession, EmployeeLocationPoint
from apps.accounts.location_service import freshness
from apps.accounts.location_consumer import EmployeeLocationConsumer


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class EmployeeLocationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.employee = User.objects.create_user('location-employee', role='EMPLOYEE')
        self.other = User.objects.create_user('location-other', role='CALLER')
        self.admin = User.objects.create_user('location-admin', role='ADMIN')
        self.session = self.session_for(self.employee)
        self.other_session = self.session_for(self.other)
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=self.employee).key}')

    def session_for(self, user):
        session = CallerSession.objects.create(caller=user, verified_at=timezone.now(), expires_at=timezone.now() + timedelta(hours=4))
        CallerSession.objects.filter(pk=session.pk).update(logged_in_at=timezone.now() - timedelta(hours=2))
        session.refresh_from_db()
        return session

    def point(self, **changes):
        point = dict(latitude=22.5726, longitude=88.3639, accuracy=12.5, altitude=10, speed=4.2,
                     heading=180, recorded_at=timezone.now().isoformat())
        point.update(changes)
        return point

    def submit(self, points=None, **changes):
        data = {'session_id': str(self.session.pk), 'points': points or [self.point()]}
        data.update(changes)
        return self.api.post('/api/v1/mobile/location/', data, format='json')

    def stored_point(self, employee=None, session=None, **changes):
        point = self.point(recorded_at=timezone.now())
        point.update(changes)
        return EmployeeLocationPoint.objects.create(employee=employee or self.employee, session=session or self.session, **point)

    def admin_client(self, role='ADMIN'):
        user = self.admin if role == 'ADMIN' else User.objects.create_user(f'location-{role}', role=role)
        api = APIClient()
        api.force_login(user)
        return api

    def test_employee_batch_owned_by_authenticated_employee(self):
        response = self.submit([self.point(recorded_at=(timezone.now() - timedelta(minutes=i)).isoformat()) for i in range(3)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['accepted_count'], 3)
        self.assertEqual(EmployeeLocationPoint.objects.filter(employee=self.employee, session=self.session).count(), 3)
        self.session.refresh_from_db()
        self.assertIsNone(self.session.latitude)
        self.assertEqual(self.session.active_seconds, 0)

    def test_missing_auth_and_ended_or_expired_sessions_rejected(self):
        self.assertEqual(APIClient().post('/api/v1/mobile/location/', {}, format='json').status_code, 401)
        for changes in ({'expires_at': timezone.now() - timedelta(seconds=1)}, {'logged_out_at': timezone.now()}):
            CallerSession.objects.filter(pk=self.session.pk).update(**changes)
            self.assertEqual(self.submit().status_code, 401)
        self.assertFalse(EmployeeLocationPoint.objects.exists())

    def test_ownership_spoofing_rejected(self):
        for key in ('employee', 'user', 'employee_id', 'caller'):
            self.assertEqual(self.submit(**{key: self.other.pk}).status_code, 400)
            self.assertEqual(self.submit([self.point(**{key: self.other.pk})]).status_code, 400)
        self.assertEqual(self.submit(session_id=str(self.other_session.pk)).status_code, 403)
        self.assertFalse(EmployeeLocationPoint.objects.exists())

    def test_invalid_coordinates_and_measurements(self):
        for field, value in [('latitude', 91), ('latitude', -91), ('longitude', 181), ('longitude', -181),
                             ('latitude', 'NaN'), ('longitude', 'Infinity'), ('accuracy', -1), ('speed', -1), ('heading', 361)]:
            with self.subTest(field=field, value=value):
                self.assertEqual(self.submit([self.point(**{field:value})]).status_code, 400)
        self.assertFalse(EmployeeLocationPoint.objects.exists())

    def test_duplicate_within_batch_and_retry_is_idempotent(self):
        point = self.point()
        for _ in range(2):
            response = self.submit([point, point])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['accepted_count'], 1)
        self.assertEqual(EmployeeLocationPoint.objects.count(), 1)

    def test_timestamp_limits_and_atomic_validation(self):
        for when in (timezone.now() + timedelta(minutes=5), timezone.now() - timedelta(days=91), self.session.logged_in_at - timedelta(seconds=1), self.session.expires_at):
            response = self.submit([self.point(), self.point(recorded_at=when.isoformat())])
            self.assertEqual(response.status_code, 400)
            self.assertEqual(len(response.data['rejected']), 1)
            self.assertFalse(EmployeeLocationPoint.objects.exists())
        self.assertEqual(self.submit([self.point(recorded_at=(timezone.now() - timedelta(hours=1)).isoformat())]).status_code, 200)

    def test_bounded_batches_and_empty_batch(self):
        self.assertEqual(self.submit([self.point() for _ in range(101)]).status_code, 400)
        self.assertEqual(self.api.post('/api/v1/mobile/location/', {'session_id':str(self.session.pk), 'points':[]}, format='json').status_code, 400)

    def test_offline_points_from_own_closed_session_can_replay_after_reauthentication(self):
        ended = timezone.now()-timedelta(minutes=20)
        CallerSession.objects.filter(pk=self.session.pk).update(logged_out_at=ended)
        self.session_for(self.employee)
        valid = self.point(recorded_at=(ended-timedelta(minutes=10)).isoformat())
        self.assertEqual(self.submit([valid]).status_code, 200)
        self.assertEqual(self.submit([self.point(recorded_at=(ended+timedelta(seconds=1)).isoformat())]).status_code, 400)
        self.assertEqual(self.submit([valid], session_id=str(self.other_session.pk)).status_code, 403)

    def test_status_is_own_session_only_and_does_not_change_attendance(self):
        response = self.api.get('/api/v1/mobile/location/status/', {'employee':self.other.pk})
        self.assertEqual(response.data['session_id'], str(self.session.pk))
        self.assertNotIn('latitude', response.data)
        before = CallerSession.objects.values().get(pk=self.session.pk)
        result = self.api.post('/api/v1/mobile/location/status/', {'session_id':str(self.session.pk), 'state':'UNAVAILABLE'}, format='json')
        self.assertEqual(result.status_code, 200)
        after = CallerSession.objects.values().get(pk=self.session.pk)
        for key in ('last_seen', 'active_seconds', 'latitude', 'longitude', 'logged_out_at'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after['location_state'], 'UNAVAILABLE')

    def test_invalid_status_or_other_session_not_accepted(self):
        for data, code in [({'session_id':str(self.other_session.pk), 'state':'ACTIVE'},403),
                           ({'session_id':str(self.session.pk), 'state':'BAD'},400)]:
            self.assertEqual(self.api.post('/api/v1/mobile/location/status/', data, format='json').status_code, code)

    def test_only_admin_roles_can_access_live_history_and_page(self):
        self.stored_point()
        urls = ['/api/v1/admin/employee-locations/live/',
                f'/api/v1/admin/employee-locations/history/?employee={self.employee.pk}', '/employee-locations/']
        for role in User.Role.values:
            client = self.admin_client(role)
            for url in urls:
                with self.subTest(role=role, url=url):
                    self.assertEqual(client.get(url).status_code, 200 if role in ('ADMIN','SUPER_ADMIN') else 403)
        self.assertEqual(self.api.get(urls[0]).status_code, 403)
        self.assertEqual(APIClient().get(urls[0]).status_code, 403)

    def test_latest_uses_recorded_time_not_offline_upload_time(self):
        latest = self.stored_point(latitude=23)
        self.stored_point(recorded_at=timezone.now()-timedelta(hours=1), latitude=24)
        response = self.admin_client().get('/api/v1/admin/employee-locations/live/')
        row = next(r for r in response.data['results'] if r['employee'] == self.employee.pk)
        self.assertEqual(row['latitude'], latest.latitude)
        self.assertIn('no-store', response['Cache-Control'])

    def test_live_query_count_does_not_grow_per_employee(self):
        for i in range(20):
            employee = User.objects.create_user(f'field-{i}', role='EMPLOYEE')
            self.stored_point(employee=employee, session=self.session_for(employee))
        api = self.admin_client()
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(api.get('/api/v1/admin/employee-locations/live/').status_code, 200)
        self.assertLessEqual(len(captured), 7)

    def test_history_employee_date_range_and_pagination(self):
        today = timezone.localdate()
        for i in range(5):
            self.stored_point(recorded_at=timezone.now()-timedelta(seconds=i))
        self.stored_point(employee=self.other, session=self.other_session)
        self.stored_point(recorded_at=timezone.now()-timedelta(days=2))
        api = self.admin_client()
        query = {'employee':self.employee.pk, 'date':today.isoformat(), 'page_size':2}
        first = api.get('/api/v1/admin/employee-locations/history/', query)
        self.assertEqual(first.data['count'], 5)
        self.assertEqual(len(first.data['results']), 2)
        times = [r['recorded_at'] for r in first.data['results']]
        self.assertEqual(times, sorted(times))
        query['page'] = 3
        self.assertEqual(len(api.get('/api/v1/admin/employee-locations/history/', query).data['results']), 1)
        query = {'employee':self.employee.pk, 'start':(timezone.now()-timedelta(days=3)).isoformat(), 'end':timezone.now().isoformat()}
        self.assertEqual(api.get('/api/v1/admin/employee-locations/history/', query).data['count'], 6)

    def test_history_invalid_or_unbounded_range_rejected(self):
        api = self.admin_client()
        for query in ({}, {'employee':self.employee.pk, 'date':'bad'},
                      {'employee':self.employee.pk, 'start':timezone.now().isoformat()},
                      {'employee':self.employee.pk, 'start':(timezone.now()-timedelta(days=32)).isoformat(), 'end':timezone.now().isoformat()}):
            self.assertEqual(api.get('/api/v1/admin/employee-locations/history/', query).status_code, 400)

    def test_freshness_thresholds_and_old_upload(self):
        now = timezone.now()
        point = self.stored_point()
        for age, expected in [(10,'LIVE'),(30,'RECENT'),(300,'STALE'),(900,'OFFLINE')]:
            point.recorded_at = now - timedelta(seconds=age)
            point.received_at = now
            self.assertEqual(freshness(point, now), expected)
        self.assertEqual(freshness(None), 'OFFLINE')

    def test_unavailable_is_distinct_from_network_freshness(self):
        self.stored_point(recorded_at=timezone.now()-timedelta(minutes=7))
        CallerSession.objects.filter(pk=self.session.pk).update(location_state='UNAVAILABLE')
        result = self.admin_client().get('/api/v1/admin/employee-locations/live/')
        row = next(r for r in result.data['results'] if r['employee']==self.employee.pk)
        self.assertEqual((row['status'], row['location_state'], row['tracking_active']), ('STALE','UNAVAILABLE',False))

    def test_cleanup_only_older_than_90_days_in_batches(self):
        now = timezone.now()
        expired = [self.stored_point(recorded_at=now-timedelta(days=91, seconds=i)) for i in range(5)]
        retained = self.stored_point(recorded_at=now-timedelta(days=90))
        with patch('apps.accounts.location_service.timezone.now', return_value=now):
            call_command('cleanup_location_history', dry_run=True, stdout=StringIO())
            self.assertEqual(EmployeeLocationPoint.objects.count(), 6)
            call_command('cleanup_location_history', batch_size=2, stdout=StringIO())
        self.assertTrue(EmployeeLocationPoint.objects.filter(pk=retained.pk).exists())
        self.assertFalse(EmployeeLocationPoint.objects.filter(pk__in=[p.pk for p in expired]).exists())
        self.assertTrue(CallerSession.objects.filter(pk=self.session.pk).exists())

    def test_broadcast_only_after_commit_and_none_on_rollback(self):
        with patch('apps.accounts.api.location.notify_location_change') as notify:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.submit().status_code, 200)
                notify.assert_not_called()
            notify.assert_called_once()
            notify.reset_mock()
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(RuntimeError), transaction.atomic():
                    self.submit()
                    raise RuntimeError('rollback')
            notify.assert_not_called()
        self.assertEqual(EmployeeLocationPoint.objects.count(), 1)

    def test_page_sidebar_and_manager_login_location_redaction(self):
        CallerSession.objects.filter(pk=self.session.pk).update(latitude=22.5726, longitude=88.3639)
        admin = self.admin_client()
        self.assertContains(admin.get('/employee-locations/'), 'employee-location-map')
        self.assertContains(admin.get('/caller-sessions/'), '/employee-locations/')
        manager = self.admin_client('MANAGER')
        response = manager.get('/caller-sessions/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'google.com/maps')
        self.assertNotContains(response, '/employee-locations/')


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class LocationSocketTests(TransactionTestCase):
    def test_cookie_session_admin_only_and_role_revocation(self):
        async def exercise(user, session_key, allowed):
            socket = WebsocketCommunicator(EmployeeLocationConsumer.as_asgi(), '/ws/employee-locations/')
            socket.scope.update(user=user, session=SessionStore(session_key=session_key))
            connected, _ = await socket.connect()
            self.assertEqual(connected, allowed)
            await socket.disconnect()
        for role in ('ADMIN','SUPER_ADMIN','MANAGER','COUNSELOR','CALLER','EMPLOYEE'):
            user = User.objects.create_user(f'socket-{role}', role=role)
            self.client.force_login(user)
            async_to_sync(exercise)(user, self.client.session.session_key, role in ('ADMIN','SUPER_ADMIN'))
