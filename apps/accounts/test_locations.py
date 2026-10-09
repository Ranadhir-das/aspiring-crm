from datetime import timedelta, datetime, timezone as utc_timezone
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


    def test_old_session_cannot_become_live_and_bad_quality_does_not_replace_fix(self):
        point = self.stored_point(recorded_at=timezone.now()-timedelta(seconds=10))
        CallerSession.objects.filter(pk=self.session.pk).update(logged_out_at=timezone.now()-timedelta(seconds=5))
        current = self.session_for(self.employee)
        CallerSession.objects.filter(pk=current.pk).update(logged_in_at=timezone.now(), location_state='ACTIVE')
        self.stored_point(session=current, accuracy=1000)
        self.stored_point(session=current, mocked=True)
        result = self.admin_client().get('/api/v1/admin/employee-locations/live/')
        row = next(r for r in result.data['results'] if r['employee']==self.employee.pk)
        self.assertEqual(row['last_seen_at'], point.recorded_at)
        self.assertFalse(row['current_session_point'])
        self.assertEqual(row['status'], 'STALE')
        self.assertTrue(row['tracking_active'])

    def test_history_pagination_excludes_newly_received_delayed_samples(self):
        captured=timezone.now()-timedelta(minutes=10)
        points=[self.stored_point(recorded_at=captured+timedelta(minutes=i)) for i in range(3)]
        client=self.admin_client()
        first=client.get('/api/v1/admin/employee-locations/history/', {'employee':self.employee.pk,
            'start':captured.isoformat(),'end':timezone.now().isoformat(),'page_size':2})
        self.assertIn('snapshot_at=',first.data['next'])
        self.stored_point(recorded_at=captured+timedelta(seconds=30))
        from urllib.parse import urlsplit
        next_url=urlsplit(first.data['next'])
        second=client.get(next_url.path+'?'+next_url.query)
        self.assertEqual(second.data['count'],3)
        self.assertEqual([p['id'] for p in second.data['results']],[points[2].pk])
        self.assertEqual(second.data['results'][0]['segment_reason'],'CONTINUOUS')

    def test_freshness_filters_are_applied_before_pagination(self):
        self.stored_point(recorded_at=timezone.now()-timedelta(seconds=10))
        self.stored_point(employee=self.other,session=self.other_session,recorded_at=timezone.now()-timedelta(minutes=2))
        client=self.admin_client()
        for state,expected in [('LIVE',self.employee.pk),('RECENT',self.other.pk)]:
            response=client.get('/api/v1/admin/employee-locations/live/', {'status':state,'page_size':1})
            self.assertEqual(response.data['count'],1)
            self.assertEqual(response.data['results'][0]['employee'],expected)
        CallerSession.objects.filter(pk=self.session.pk).update(logged_out_at=timezone.now(),location_state='ACTIVE')
        response=client.get('/api/v1/admin/employee-locations/live/', {'tracking':'STOPPED','search':'location-employee'})
        self.assertEqual(response.data['count'],1)
        self.assertEqual(response.data['results'][0]['location_state'],'STOPPED')

    def test_metadata_and_diagnostics_are_optional_and_validated(self):
        self.assertEqual(self.submit([self.point(source='expo-location', platform='android', mocked=True)]).status_code, 200)
        stored = EmployeeLocationPoint.objects.get()
        self.assertTrue(stored.mocked)
        self.assertEqual(stored.source, 'expo-location')
        self.assertEqual(self.submit([self.point(source='arbitrary')]).status_code, 400)
        result = self.api.post('/api/v1/mobile/location/status/', {'session_id':str(self.session.pk),
            'state':'UNAVAILABLE', 'reason':'GPS_UNAVAILABLE'}, format='json')
        self.assertEqual(result.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.location_reason, 'GPS_UNAVAILABLE')

    def test_out_of_order_and_delayed_upload_preserve_capture_and_raw_values(self):
        times = [timezone.now()-timedelta(minutes=n) for n in (2,8,5)]
        self.assertEqual(self.submit([self.point(recorded_at=t.isoformat()) for t in times]).status_code, 200)
        points = list(EmployeeLocationPoint.objects.order_by('recorded_at'))
        self.assertEqual([p.recorded_at for p in points], sorted(times))
        self.assertTrue(all(p.received_at > p.recorded_at for p in points))
        self.assertEqual(self.submit([self.point(recorded_at=times[0].isoformat(), latitude=1)]).status_code, 200)
        self.assertEqual(EmployeeLocationPoint.objects.get(recorded_at=times[0]).latitude, 22.5726)

    @override_settings(TIME_ZONE='Asia/Kolkata')
    def test_previous_date_exact_boundaries_and_time_range(self):
        from zoneinfo import ZoneInfo
        day = timezone.localdate()-timedelta(days=2)
        start = datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo('Asia/Kolkata'))
        for offset in (-1,0,3600,86399,86400):
            self.stored_point(recorded_at=start+timedelta(seconds=offset))
        api = self.admin_client()
        query = {'employee':self.employee.pk, 'date':day.isoformat()}
        result = api.get('/api/v1/admin/employee-locations/history/', query)
        self.assertEqual(result.data['count'], 3)
        self.assertEqual(result.data['start'], start.astimezone(utc_timezone.utc))
        query.update(time_from='00:00',time_to='01:00')
        self.assertEqual(api.get('/api/v1/admin/employee-locations/history/', query).data['count'], 1)
        query.update(date=(day-timedelta(days=3)).isoformat())
        self.assertEqual(api.get('/api/v1/admin/employee-locations/history/', query).data['count'], 0)

    @override_settings(TIME_ZONE='America/New_York')
    def test_daylight_saving_days_and_ambiguous_local_times(self):
        from apps.accounts.api.location import HistoryFilter
        for day, hours in [('2026-03-08',23),('2026-11-01',25)]:
            data = HistoryFilter(data={'employee':self.employee.pk,'date':day})
            self.assertTrue(data.is_valid(), data.errors)
            self.assertEqual((data.validated_data['end']-data.validated_data['start']).total_seconds(), hours*3600)
        for day, clock in [('2026-03-08','02:30'),('2026-11-01','01:30')]:
            data = HistoryFilter(data={'employee':self.employee.pk,'date':day,'time_from':clock})
            self.assertFalse(data.is_valid())

    @override_settings(EMPLOYEE_LOCATION_MAX_GAP_SECONDS=300, EMPLOYEE_LOCATION_MAX_SPEED_MPS=55)
    def test_route_segments_quality_jumps_and_pagination_boundary(self):
        start = timezone.now()-timedelta(hours=1)
        specs = [(0,22.0,10,self.session,False), (60,22.0001,10,self.session,False),
                 (120,50,10,self.session,False), (600,50,10,self.session,False),
                 (660,50,1000,self.session,False), (720,50,10,self.session,False),
                 (780,50,10,self.other_session,False), (840,50,10,self.other_session,True)]
        for seconds, lat, accuracy, session, mocked in specs:
            self.stored_point(session=session, recorded_at=start+timedelta(seconds=seconds), latitude=lat, accuracy=accuracy, mocked=mocked)
        query = {'employee':self.employee.pk,'start':start.isoformat(),'end':timezone.now().isoformat(),'page_size':2}
        api = self.admin_client()
        results=[]
        for page in range(1,5):
            query['page']=page
            results.extend(api.get('/api/v1/admin/employee-locations/history/',query).data['results'])
        self.assertEqual([r['segment_reason'] for r in results], ['START','CONTINUOUS','IMPLAUSIBLE_JUMP','TIME_GAP','POOR_ACCURACY','QUALITY_GAP','SESSION_CHANGE','MOCK_LOCATION'])
        self.assertGreater(results[1]['distance_from_previous_m'],0)
        self.assertTrue(all(r['distance_from_previous_m']==0 for i,r in enumerate(results) if i!=1))
        self.assertEqual(EmployeeLocationPoint.objects.count(),8)

    def test_search_tracking_filter_and_tile_referrer_policy(self):
        CallerSession.objects.filter(pk=self.session.pk).update(location_state='ACTIVE')
        client=self.admin_client()
        data=client.get('/api/v1/admin/employee-locations/live/', {'search':'location-employee','tracking':'ACTIVE'}).data
        self.assertEqual([r['employee'] for r in data['results']], [self.employee.pk])
        response=client.get('/employee-locations/')
        self.assertEqual(response['Referrer-Policy'],'strict-origin-when-cross-origin')
        self.assertContains(response, 'location-play')



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


    def test_socket_reconnect_and_access_revocation(self):
        from channels.db import database_sync_to_async
        from channels.layers import get_channel_layer
        from apps.accounts.location_service import LOCATION_GROUP
        user=User.objects.create_user('socket-reconnect',role='ADMIN')
        self.client.force_login(user)
        key=self.client.session.session_key
        async def exercise():
            async def connect():
                socket=WebsocketCommunicator(EmployeeLocationConsumer.as_asgi(),'/ws/employee-locations/')
                socket.scope.update(user=user,session=SessionStore(session_key=key))
                self.assertTrue((await socket.connect())[0])
                return socket
            socket=await connect()
            await socket.disconnect()
            socket=await connect()
            await get_channel_layer().group_send(LOCATION_GROUP,{'type':'location.changed'})
            self.assertEqual(await socket.receive_json_from(),{'type':'location.changed'})
            await database_sync_to_async(User.objects.filter(pk=user.pk).update)(role='MANAGER')
            await get_channel_layer().group_send(LOCATION_GROUP,{'type':'location.changed'})
            self.assertEqual((await socket.receive_output())['code'],4003)
            await socket.disconnect()
        async_to_sync(exercise)()


class LocationMigrationTests(TransactionTestCase):
    def test_additive_migration_preserves_existing_location_and_session(self):
        from django.db.migrations.executor import MigrationExecutor
        old=('accounts','0012_callersession_location_state_and_more')
        new=('accounts','0014_location_received_index')
        executor=MigrationExecutor(connection)
        executor.migrate([old])
        try:
            state=executor.loader.project_state([old]).apps
            employee=state.get_model('accounts','User').objects.create(username='pre-upgrade-location',role='EMPLOYEE')
            session=state.get_model('accounts','CallerSession').objects.create(caller_id=employee.pk,
                verified_at=timezone.now(),expires_at=timezone.now()+timedelta(hours=1),location_state='ACTIVE')
            captured=timezone.now()-timedelta(minutes=5)
            point=state.get_model('accounts','EmployeeLocationPoint').objects.create(employee_id=employee.pk,
                session_id=session.pk,latitude=22.57,longitude=88.36,accuracy=12,recorded_at=captured)
            before=(point.latitude,point.longitude,point.recorded_at,point.received_at,point.session_id)
        finally:
            MigrationExecutor(connection).migrate([new])
        point=EmployeeLocationPoint.objects.get(pk=point.pk)
        self.assertEqual((point.latitude,point.longitude,point.recorded_at,point.received_at,point.session_id),before)
        self.assertIsNone(point.mocked)
        self.assertEqual(point.platform,'')
        self.assertEqual(CallerSession.objects.get(pk=session.pk).location_state,'ACTIVE')
