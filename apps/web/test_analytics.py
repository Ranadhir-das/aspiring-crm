from datetime import timedelta
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.activity.models import ActivityLog
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Admission, Counselling, Lead, Service
from apps.performance.models import PeerAppreciation, PointsEntry
from apps.performance.services import get_current_appreciation_period
from apps.web.analytics_service import get_analytics_overview, parse_date_filters


class AnalyticsDashboardTests(TestCase):
    def setUp(self):
        self.web_client = Client()
        self.api_client = APIClient()

        self.admin = User.objects.create_user(
            username="admin_user",
            password="password123",
            role=User.Role.ADMIN,
            first_name="Admin",
            last_name="Boss",
        )
        self.caller1 = User.objects.create_user(
            username="caller_alice",
            password="password123",
            role=User.Role.CALLER,
            first_name="Alice",
            last_name="Caller",
        )
        self.caller2 = User.objects.create_user(
            username="caller_bob",
            password="password123",
            role=User.Role.CALLER,
            first_name="Bob",
            last_name="Caller",
        )

        self.service1 = Service.objects.create(name="Medical NEET", code="NEET", is_active=True)
        self.service2 = Service.objects.create(name="Engineering JEE", code="JEE", is_active=True)

        # Create test leads
        now = timezone.now()
        self.lead1 = Lead.objects.create(
            name="Student One",
            phone="9876543210",
            service=self.service1.name,
            service_type=self.service1,
            assigned_caller=self.caller1,
            source="website",
            status=Lead.Status.INTERESTED,
        )
        self.lead2 = Lead.objects.create(
            name="Student Two",
            phone="9876543211",
            service=self.service2.name,
            service_type=self.service2,
            assigned_caller=self.caller2,
            source="direct",
            status=Lead.Status.CALLED,
        )
        self.lead3 = Lead.objects.create(
            name="Student Three",
            phone="9876543212",
            service=self.service1.name,
            service_type=self.service1,
            assigned_caller=None,
            source="website",
            status=Lead.Status.PENDING,
        )

        # Create call records
        self.call1 = Call.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            phone_number=self.lead1.phone,
            outcome=Call.Outcome.INTERESTED,
            started_at=now - timedelta(hours=2),
            ended_at=now - timedelta(hours=2) + timedelta(minutes=5),
            duration_seconds=300,
        )
        self.call2 = Call.objects.create(
            caller=self.caller2,
            lead=self.lead2,
            phone_number=self.lead2.phone,
            outcome=Call.Outcome.NO_ANSWER,
            started_at=now - timedelta(hours=1),
            ended_at=now - timedelta(hours=1) + timedelta(minutes=1),
            duration_seconds=60,
        )

        # Follow-up
        self.followup1 = FollowUp.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            phone_number=self.lead1.phone,
            scheduled_at=now + timedelta(days=1),
            status=FollowUp.Status.PENDING,
        )
        self.followup_missed = FollowUp.objects.create(
            caller=self.caller2,
            lead=self.lead2,
            phone_number=self.lead2.phone,
            scheduled_at=now - timedelta(hours=3),
            status=FollowUp.Status.PENDING,
        )

        # Counselling & Admission
        self.counselling1 = Counselling.objects.create(
            lead=self.lead1,
            caller=self.caller1,
            counselling_type=Counselling.CounsellingType.WALK_IN,
            conducted_at=now - timedelta(days=1),
            created_by=self.caller1,
        )
        self.admission1 = Admission.objects.create(
            lead=self.lead1,
            caller=self.caller1,
            college="Government Medical College",
            course="MBBS",
            admission_date=now.date(),
            fees=50000,
            created_by=self.admin,
        )

        # Performance Points and Peer Appreciation
        PointsEntry.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            event=PointsEntry.Event.INTERESTED_LEAD,
            points=25,
            reason="High intent lead recorded",
            event_key="test-key-alice-1",
        )
        current_month = get_current_appreciation_period()
        PeerAppreciation.objects.create(
            reviewer=self.caller2,
            employee=self.caller1,
            month=current_month,
            score=9,
        )

        # Activity log
        ActivityLog.objects.create(
            actor=self.caller1,
            verb=ActivityLog.Verb.CALL_LOGGED,
            lead=self.lead1,
            description="completed call with outcome Interested",
        )

    def test_analytics_service_structure(self):
        """Verify get_analytics_overview returns all required data blocks."""
        data = get_analytics_overview(self.admin, {"days": "7"})

        # Top-level keys
        expected_keys = [
            'kpis', 'lead_status', 'lead_sources', 'services',
            'lead_trend', 'call_trend', 'interested_trend', 'admission_trend',
            'calls_by_employee', 'leads_by_employee', 'admissions_by_employee',
            'points_by_employee', 'employee_performance', 'recent_activity',
            'live_status', 'filters', 'updated_at', 'server_timestamp',
        ]
        for key in expected_keys:
            self.assertIn(key, data, f"Missing key in analytics overview: {key}")

        # Check KPI keys
        kpis = data['kpis']
        expected_kpis = [
            'total_leads', 'new_leads', 'calls', 'interested_leads',
            'followups', 'missed_followups', 'counselling_demo', 'verified_admissions',
        ]
        for k in expected_kpis:
            self.assertIn(k, kpis, f"Missing KPI: {k}")
            self.assertIn('value', kpis[k])
            self.assertIn('diff_pct', kpis[k])
            self.assertIn('trend', kpis[k])

    def test_kpi_counts_and_metrics(self):
        """Verify KPI values reflect database truth."""
        data = get_analytics_overview(self.admin, {"days": "7"})
        kpis = data['kpis']

        self.assertEqual(kpis['total_leads']['value'], 3)
        self.assertEqual(kpis['new_leads']['value'], 3)
        self.assertEqual(kpis['calls']['value'], 2)
        self.assertEqual(kpis['interested_leads']['value'], 1)
        self.assertEqual(kpis['followups']['value'], 2)
        self.assertEqual(kpis['missed_followups']['value'], 1)
        self.assertEqual(kpis['counselling_demo']['value'], 1)
        self.assertEqual(kpis['verified_admissions']['value'], 1)

    def test_live_system_status(self):
        """Verify real-time pulse numbers."""
        data = get_analytics_overview(self.admin, {})
        live = data['live_status']

        self.assertIn('active_callers', live)
        self.assertIn('active_calls', live)
        self.assertIn('website_leads_waiting', live)
        self.assertIn('followups_due', live)
        self.assertIn('claimed_leads', live)
        self.assertIn('pending_unassigned_leads', live)

        # We created 1 unassigned pending lead (lead3)
        self.assertGreaterEqual(live['pending_unassigned_leads'], 1)
        # We created 1 missed/due follow-up
        self.assertGreaterEqual(live['followups_due'], 1)

    def test_employee_scoreboard_separate_points_and_appreciation(self):
        """Verify performance points and peer appreciation are strictly separated."""
        data = get_analytics_overview(self.admin, {"days": "7"})
        scoreboard = data['employee_performance']
        self.assertTrue(len(scoreboard) >= 2)

        alice_row = next((r for r in scoreboard if r['username'] == 'caller_alice'), None)
        self.assertIsNotNone(alice_row)
        self.assertEqual(alice_row['performance_points'], 600)
        self.assertEqual(alice_row['peer_appreciation_score'], 9.0)
        self.assertEqual(alice_row['peer_appreciation_count'], 1)

        # Points and appreciation must remain distinct attributes
        self.assertNotEqual(alice_row['performance_points'], alice_row['peer_appreciation_score'])

    def test_dimension_filters(self):
        """Verify filtering by service, caller, source, and status."""
        # Filter by service 1 (NEET)
        data_s1 = get_analytics_overview(self.admin, {"service": str(self.service1.id)})
        self.assertEqual(data_s1['kpis']['total_leads']['value'], 2)

        # Filter by caller 1 (Alice)
        data_alice = get_analytics_overview(self.admin, {"caller": str(self.caller1.id)})
        self.assertEqual(data_alice['kpis']['total_leads']['value'], 1)
        self.assertEqual(data_alice['kpis']['calls']['value'], 1)

        # Filter by source (website)
        data_web = get_analytics_overview(self.admin, {"source": "website"})
        self.assertEqual(data_web['kpis']['total_leads']['value'], 2)

        # Filter by status (INTERESTED)
        data_int = get_analytics_overview(self.admin, {"status": "INTERESTED"})
        self.assertEqual(data_int['kpis']['total_leads']['value'], 1)

    def test_dashboard_web_view(self):
        """Verify web GET on dashboard returns 200 with new analytics template."""
        self.web_client.force_login(self.admin)
        response = self.web_client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CRM Overview")
        self.assertContains(response, "LIVE")
        self.assertContains(response, "analytics-initial-data")
        self.assertContains(response, "chart.umd.min.js")
        self.assertContains(response, "analytics.js")

    def test_dashboard_web_view_caller_scoped(self):
        """Verify caller can access dashboard and receives their scoped overview."""
        self.web_client.force_login(self.caller1)
        response = self.web_client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CRM Overview")

    def test_dashboard_web_view_unauthenticated(self):
        """Verify anonymous user is redirected to login."""
        response = self.web_client.get(reverse('web:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_analytics_api_endpoint(self):
        """Verify GET /api/v1/analytics/overview/ returns 200 with JSON payload."""
        self.api_client.force_authenticate(user=self.admin)
        response = self.api_client.get('/api/v1/analytics/overview/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('kpis', data)
        self.assertIn('live_status', data)
        self.assertIn('lead_status', data)
        self.assertEqual(data['kpis']['total_leads']['value'], 3)

    def test_analytics_api_endpoint_unauthenticated(self):
        """Verify anonymous request to API is rejected with 401."""
        response = self.api_client.get('/api/v1/analytics/overview/')
        self.assertIn(response.status_code, [401, 403])


from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.authtoken.models import Token

from apps.accounts.models import CallerSession
from apps.leads.models import LeadAssignmentHistory, LeadAvailability
from apps.web.models import Attendance


ALL_24_TOP_LEVEL_KEYS = [
    'kpis',
    'lead_status',
    'lead_sources',
    'services',
    'call_outcomes',
    'conversion_funnel',
    'source_matrix',
    'followup_health',
    'admissions_by_program',
    'quick_links',
    'lead_trend',
    'call_trend',
    'interested_trend',
    'admission_trend',
    'calls_by_employee',
    'leads_by_employee',
    'admissions_by_employee',
    'points_by_employee',
    'employee_performance',
    'recent_activity',
    'live_status',
    'filters',
    'updated_at',
    'server_timestamp',
]


class AnalyticsAuthorizationAndIsolationTests(TestCase):
    """Comprehensive security, session/token auth, and data-isolation tests for IMP-02 / RISK-002."""

    def setUp(self):
        self.web_client = Client()
        self.api_client = APIClient()
        self.now = timezone.now()
        self.today = timezone.localdate()

        # 1. Management roles (3)
        self.super_admin = User.objects.create_user(
            username="super_admin_sec",
            password="password123",
            role=User.Role.SUPER_ADMIN,
            first_name="Super",
            last_name="Admin",
        )
        self.admin = User.objects.create_user(
            username="admin_sec",
            password="password123",
            role=User.Role.ADMIN,
            first_name="Admin",
            last_name="Sec",
        )
        self.manager = User.objects.create_user(
            username="manager_sec",
            password="password123",
            role=User.Role.MANAGER,
            first_name="Manager",
            last_name="Sec",
            can_act_as_caller=True,  # Verifies management scope takes precedence over caller flag
        )

        # 2. Authorized non-management callers: ordinary & dual-role (4)
        self.caller1 = User.objects.create_user(
            username="caller_one",
            password="password123",
            role=User.Role.CALLER,
            first_name="Alice",
            last_name="One",
        )
        self.caller2 = User.objects.create_user(
            username="caller_two",
            password="password123",
            role=User.Role.CALLER,
            first_name="Bob",
            last_name="Two",
        )
        self.dual_counselor = User.objects.create_user(
            username="dual_counselor",
            password="password123",
            role=User.Role.COUNSELOR,
            can_act_as_caller=True,
            first_name="Connie",
            last_name="Dual",
        )
        self.dual_employee = User.objects.create_user(
            username="dual_employee",
            password="password123",
            role=User.Role.EMPLOYEE,
            can_act_as_caller=True,
            first_name="Evan",
            last_name="Dual",
        )

        # 3. Unauthorized active roles (5)
        self.pure_counselor = User.objects.create_user(
            username="pure_counselor",
            password="password123",
            role=User.Role.COUNSELOR,
            can_act_as_caller=False,
        )
        self.pure_employee = User.objects.create_user(
            username="pure_employee",
            password="password123",
            role=User.Role.EMPLOYEE,
            can_act_as_caller=False,
        )
        self.it_user = User.objects.create_user(
            username="it_user",
            password="password123",
            role=User.Role.IT,
        )
        self.video_editor = User.objects.create_user(
            username="video_editor",
            password="password123",
            role=User.Role.VIDEO_EDITOR,
        )
        self.accountant = User.objects.create_user(
            username="accountant",
            password="password123",
            role=User.Role.ACCOUNTANT,
        )

        # 4. Inactive and registration-pending accounts (2)
        self.inactive_caller = User.objects.create_user(
            username="inactive_caller",
            password="password123",
            role=User.Role.CALLER,
            is_active=False,
        )
        self.pending_caller = User.objects.create_user(
            username="pending_caller",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
            registration_pending=True,
        )

        # 5. Services (2)
        self.service1 = Service.objects.create(name="Medical NEET", code="NEET", is_active=True)
        self.service2 = Service.objects.create(name="Engineering JEE", code="JEE", is_active=True)

        # 6. Leads (4)
        self.lead_c1 = Lead.objects.create(
            name="Alice Student",
            phone="9876500001",
            service=self.service1.name,
            service_type=self.service1,
            assigned_caller=self.caller1,
            source="website",
            status=Lead.Status.INTERESTED,
        )
        self.lead_c2 = Lead.objects.create(
            name="Bob Student",
            phone="9876500002",
            service=self.service2.name,
            service_type=self.service2,
            assigned_caller=self.caller2,
            source="direct",
            status=Lead.Status.CALLED,
        )
        self.lead_dual = Lead.objects.create(
            name="Connie Student",
            phone="9876500003",
            service=self.service1.name,
            service_type=self.service1,
            assigned_caller=self.dual_counselor,
            source="referral",
            status=Lead.Status.INTERESTED,
        )
        self.lead_unassigned = Lead.objects.create(
            name="Unassigned Student",
            phone="9876500004",
            service=self.service1.name,
            service_type=self.service1,
            assigned_caller=None,
            source="website",
            status=Lead.Status.PENDING,
        )

        # 7. Calls (3)
        self.call_c1 = Call.objects.create(
            caller=self.caller1,
            lead=self.lead_c1,
            phone_number=self.lead_c1.phone,
            outcome=Call.Outcome.INTERESTED,
            started_at=self.now - timedelta(hours=1),
            ended_at=self.now - timedelta(minutes=55),
            duration_seconds=300,
        )
        self.call_c2 = Call.objects.create(
            caller=self.caller2,
            lead=self.lead_c2,
            phone_number=self.lead_c2.phone,
            outcome=Call.Outcome.NO_ANSWER,
            started_at=self.now - timedelta(hours=1),
            ended_at=self.now - timedelta(minutes=59),
            duration_seconds=60,
        )
        self.call_dual = Call.objects.create(
            caller=self.dual_counselor,
            lead=self.lead_dual,
            phone_number=self.lead_dual.phone,
            outcome=Call.Outcome.INTERESTED,
            started_at=self.now - timedelta(minutes=30),
            ended_at=self.now - timedelta(minutes=25),
            duration_seconds=300,
        )

        # 8. Follow-ups (3: 1 upcoming on lead_c1, 1 overdue on lead_c2, 1 by caller1 on reassigned lead_c2)
        self.fu_c1_upcoming = FollowUp.objects.create(
            caller=self.caller1,
            lead=self.lead_c1,
            phone_number=self.lead_c1.phone,
            scheduled_at=self.now + timedelta(days=1),
            status=FollowUp.Status.PENDING,
        )
        self.fu_c2_overdue = FollowUp.objects.create(
            caller=self.caller2,
            lead=self.lead_c2,
            phone_number=self.lead_c2.phone,
            scheduled_at=self.now - timedelta(hours=2),
            status=FollowUp.Status.PENDING,
        )
        self.fu_c1_reassigned_lead = FollowUp.objects.create(
            caller=self.caller1,
            lead=self.lead_c2,
            phone_number=self.lead_c2.phone,
            scheduled_at=self.now - timedelta(hours=1),
            status=FollowUp.Status.PENDING,
        )

        # 9. Counselling (+75 pts signal) & Admission (+500 pts signal, fees=50000) for caller1
        self.counselling_c1 = Counselling.objects.create(
            lead=self.lead_c1,
            caller=self.caller1,
            counselling_type=Counselling.CounsellingType.WALK_IN,
            conducted_at=self.now - timedelta(hours=2),
            created_by=self.caller1,
        )
        self.admission_c1 = Admission.objects.create(
            lead=self.lead_c1,
            caller=self.caller1,
            college="Government Medical College",
            course="MBBS",
            admission_date=self.today,
            fees=50000,
            created_by=self.admin,
        )

        # 10. ActivityLog by caller1 on lead_c2 (which is now assigned to caller2)
        self.act_reassigned = ActivityLog.objects.create(
            actor=self.caller1,
            verb=ActivityLog.Verb.CALL_LOGGED,
            lead=self.lead_c2,
            description="logged call on lead later reassigned to caller2",
        )

    def _create_verified_token_client(self, user):
        """Helper returning an APIClient authenticated via real DRF Token + active CallerSession."""
        token, _ = Token.objects.get_or_create(user=user)
        CallerSession.objects.create(
            caller=user,
            verified_at=self.now - timedelta(minutes=10),
            expires_at=self.now + timedelta(hours=8),
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        return client

    def test_management_roles_full_access_via_real_session_and_caller_filter(self):
        """SUPER_ADMIN, ADMIN, and MANAGER receive full organization-wide data via real session login."""
        for mgr in (self.super_admin, self.admin, self.manager):
            logged_in = self.web_client.login(username=mgr.username, password="password123")
            self.assertTrue(logged_in)

            # Web HTML dashboard
            html_resp = self.web_client.get(reverse('web:dashboard'))
            self.assertEqual(html_resp.status_code, 200)
            self.assertEqual(html_resp.context['today_calls'], 3)

            # JSON API via SessionAuthentication
            api_resp = self.web_client.get('/api/v1/analytics/overview/')
            self.assertEqual(api_resp.status_code, 200)
            data = api_resp.json()
            self.assertEqual(set(data.keys()), set(ALL_24_TOP_LEVEL_KEYS))
            self.assertEqual(data['kpis']['total_leads']['value'], 4)
            self.assertEqual(data['kpis']['calls']['value'], 3)
            self.assertEqual(data['kpis']['followups']['value'], 3)
            self.assertEqual(data['kpis']['missed_followups']['value'], 2)
            self.assertEqual(data['admissions_by_program'][0]['total_fees'], 50000.0)
            self.assertEqual(len(data['filters']['available_callers']), 5)

            # Management can filter by caller1
            filtered_resp = self.web_client.get(f'/api/v1/analytics/overview/?caller={self.caller1.pk}')
            self.assertEqual(filtered_resp.status_code, 200)
            self.assertEqual(filtered_resp.json()['kpis']['total_leads']['value'], 1)

    def test_ordinary_caller_strict_isolation_via_real_token_and_session(self):
        """Ordinary CALLER sees only their own records via both VerifiedSessionAuthentication and SessionAuthentication."""
        client = self._create_verified_token_client(self.caller1)
        resp = client.get('/api/v1/analytics/overview/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(set(data.keys()), set(ALL_24_TOP_LEVEL_KEYS))
        self.assertEqual(data['kpis']['total_leads']['value'], 1)
        self.assertEqual(data['kpis']['calls']['value'], 1)
        self.assertEqual(data['kpis']['followups']['value'], 1)
        self.assertEqual(data['kpis']['missed_followups']['value'], 0)
        self.assertEqual(len(data['employee_performance']), 1)
        self.assertEqual(data['employee_performance'][0]['id'], self.caller1.pk)
        self.assertEqual(data['employee_performance'][0]['performance_points'], 575)

    def test_dual_role_counselor_and_employee_caller_isolation(self):
        """Dual-role COUNSELOR and EMPLOYEE (can_act_as_caller=True) can access HTML & API with strict self-scoping."""
        # 1. Dual-role Counselor via real token + CallerSession
        dual_client = self._create_verified_token_client(self.dual_counselor)
        resp = dual_client.get('/api/v1/analytics/overview/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['kpis']['total_leads']['value'], 1)
        self.assertEqual(data['kpis']['calls']['value'], 1)
        self.assertEqual(len(data['employee_performance']), 1)
        self.assertEqual(data['employee_performance'][0]['id'], self.dual_counselor.pk)
        self.assertEqual(data['filters']['available_callers'], [{
            'id': self.dual_counselor.pk,
            'first_name': 'Connie',
            'last_name': 'Dual',
            'username': 'dual_counselor',
        }])

        # 2. Dual-role Counselor via web session on /dashboard/ and /analytics/overview/
        self.web_client.login(username="dual_counselor", password="password123")
        for url_name in ('web:dashboard', 'web:analytics-overview'):
            web_resp = self.web_client.get(reverse(url_name))
            self.assertEqual(web_resp.status_code, 200)
            self.assertEqual(web_resp.context['today_calls'], 1)
            self.assertIsNone(web_resp.context['workload_summary'])
            self.assertIsNone(web_resp.context['attendance_leaderboard'])

        # 3. Dual-role Employee with 0 leads/calls
        emp_client = self._create_verified_token_client(self.dual_employee)
        emp_resp = emp_client.get('/api/v1/analytics/overview/')
        self.assertEqual(emp_resp.status_code, 200)
        self.assertEqual(emp_resp.json()['kpis']['total_leads']['value'], 0)
        self.assertEqual(emp_resp.json()['kpis']['calls']['value'], 0)

    def test_all_unauthorized_roles_rejected_across_api_web_and_direct_service(self):
        """Pure COUNSELOR, pure EMPLOYEE, IT, VIDEO_EDITOR, and ACCOUNTANT are denied on API, redirected on Web, and rejected by service."""
        unauthorized_users = [
            self.pure_counselor,
            self.pure_employee,
            self.it_user,
            self.video_editor,
            self.accountant,
        ]
        for u in unauthorized_users:
            # 1. Direct service call raises PermissionDenied
            with self.assertRaises(PermissionDenied):
                get_analytics_overview(u, {})

            # 2. Web session login -> 403 on JSON API, 302 to employee-home on HTML dashboard
            self.web_client.login(username=u.username, password="password123")
            api_resp = self.web_client.get('/api/v1/analytics/overview/')
            self.assertEqual(api_resp.status_code, 403, f"API must return 403 for {u.role}")

            for url_name in ('web:dashboard', 'web:analytics-overview'):
                web_resp = self.web_client.get(reverse(url_name))
                self.assertRedirects(web_resp, reverse('web:employee-home'))

            # 3. Mobile token + CallerSession -> 403 on JSON API
            token_client = self._create_verified_token_client(u)
            token_resp = token_client.get('/api/v1/analytics/overview/')
            self.assertEqual(token_resp.status_code, 403, f"Token API must return 403 for {u.role}")

    def test_inactive_and_registration_pending_users_denied(self):
        """Inactive and registration-pending accounts are rejected across service, session, and token paths."""
        for u in (self.inactive_caller, self.pending_caller):
            with self.assertRaises(PermissionDenied):
                get_analytics_overview(u, {})

            token, _ = Token.objects.get_or_create(user=u)
            CallerSession.objects.create(
                caller=u,
                verified_at=self.now - timedelta(minutes=5),
                expires_at=self.now + timedelta(hours=8),
            )
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
            resp = client.get('/api/v1/analytics/overview/')
            self.assertIn(resp.status_code, [401, 403])

        # Session-authenticated registration_pending user is also denied
        self.web_client.force_login(self.pending_caller)
        self.assertEqual(self.web_client.get('/api/v1/analytics/overview/').status_code, 403)
        self.assertRedirects(self.web_client.get(reverse('web:dashboard')), reverse('web:employee-home'))

    def test_mobile_token_lifecycle_missing_unverified_logged_out_and_expired_sessions(self):
        """Real Token header without force_authenticate requires an open, verified, non-expired CallerSession."""
        token = Token.objects.create(user=self.caller1)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        # 1. Missing CallerSession -> 401/403
        self.assertIn(client.get('/api/v1/analytics/overview/').status_code, [401, 403])

        # 2. Unverified CallerSession (verified_at=None) -> 401/403
        session = CallerSession.objects.create(
            caller=self.caller1,
            verified_at=None,
            expires_at=self.now + timedelta(hours=8),
        )
        self.assertIn(client.get('/api/v1/analytics/overview/').status_code, [401, 403])

        # 3. Expired CallerSession (expires_at <= now) -> 401/403
        session.verified_at = self.now - timedelta(hours=9)
        session.expires_at = self.now - timedelta(seconds=1)
        session.save(update_fields=['verified_at', 'expires_at'])
        self.assertIn(client.get('/api/v1/analytics/overview/').status_code, [401, 403])

        # 4. Logged-out CallerSession (logged_out_at != None) -> 401/403
        session.expires_at = self.now + timedelta(hours=8)
        session.logged_out_at = self.now - timedelta(minutes=1)
        session.save(update_fields=['expires_at', 'logged_out_at'])
        self.assertIn(client.get('/api/v1/analytics/overview/').status_code, [401, 403])

        # 5. Valid active CallerSession -> 200 OK
        session.logged_out_at = None
        session.save(update_fields=['logged_out_at'])
        ok_resp = client.get('/api/v1/analytics/overview/')
        self.assertEqual(ok_resp.status_code, 200)
        self.assertEqual(ok_resp.json()['kpis']['total_leads']['value'], 1)

    def test_cross_employee_filter_injection_and_reassigned_lead_ownership(self):
        """Caller1 cannot inject ?caller=caller2 and does not see follow-ups or activity for leads reassigned to caller2."""
        client = self._create_verified_token_client(self.caller1)
        resp = client.get(f'/api/v1/analytics/overview/?caller={self.caller2.pk}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # ?caller=caller2 is ignored and scrubbed from filters and deep-link URLs
        self.assertEqual(data['kpis']['total_leads']['value'], 1)
        self.assertEqual(data['filters']['caller'], '')
        self.assertNotIn(f"caller={self.caller2.pk}", data['kpis']['calls']['url'])
        self.assertNotIn(f"owner={self.caller2.pk}", data['kpis']['total_leads']['url'])

        # fu_c1_reassigned_lead (on lead_c2 owned by caller2) is excluded from caller1's follow-up counts
        self.assertEqual(data['kpis']['followups']['value'], 1)
        self.assertEqual(data['kpis']['missed_followups']['value'], 0)
        self.assertEqual(data['followup_health']['overdue_count'], 0)

        # act_reassigned (on lead_c2 owned by caller2) is excluded from caller1's recent_activity
        activity_lead_ids = [item['lead_id'] for item in data['recent_activity'] if item['lead_id'] is not None]
        self.assertNotIn(self.lead_c2.pk, activity_lead_ids)

    def test_no_employee_directory_fee_or_global_live_status_leakage(self):
        """Scoped callers do not receive other callers' names, fee totals, or global live_status counts."""
        client = self._create_verified_token_client(self.caller1)

        # Before attendance check-in today, caller1's active_callers is 0 (not 1 just because user.is_active=True)
        resp_before = client.get('/api/v1/analytics/overview/')
        data_before = resp_before.json()
        self.assertEqual(data_before['live_status']['active_callers'], 0)
        self.assertEqual(data_before['live_status']['followups_due'], 0)
        self.assertEqual(data_before['live_status']['pending_unassigned_leads'], 0)
        self.assertEqual(data_before['live_status']['website_leads_waiting'], 0)
        self.assertIsNone(data_before['admissions_by_program'][0]['total_fees'])
        self.assertEqual(
            [c['id'] for c in data_before['filters']['available_callers']],
            [self.caller1.pk],
        )
        self.assertEqual(data_before['filters']['available_sources'], ['website'])

        # After caller1 checks in today, active_callers becomes 1; after checkout, becomes 0
        att = Attendance.objects.create(
            employee=self.caller1,
            date=self.today,
            checked_in=self.now - timedelta(hours=1),
            status="PRESENT",
        )
        resp_checked_in = client.get('/api/v1/analytics/overview/')
        self.assertEqual(resp_checked_in.json()['live_status']['active_callers'], 1)

        att.checked_out = self.now
        att.save(update_fields=['checked_out'])
        resp_checked_out = client.get('/api/v1/analytics/overview/')
        self.assertEqual(resp_checked_out.json()['live_status']['active_callers'], 0)

    def test_android_admin_webview_sso_bridge_to_analytics_flow(self):
        """End-to-end verification of the Android AdminWebView flow: bridge redirect -> HTML -> AJAX poll."""
        token = Token.objects.create(user=self.admin)
        bridge_resp = self.web_client.get(
            f'/api/v1/mobile/webview-bridge/?token={token.key}&next=/analytics/overview/'
        )
        self.assertEqual(bridge_resp.status_code, 302)
        self.assertEqual(bridge_resp.url, '/analytics/overview/')

        # Follow redirect into /analytics/overview/ using the WebView's session cookie
        overview_html = self.web_client.get('/analytics/overview/')
        self.assertEqual(overview_html.status_code, 200)
        self.assertContains(overview_html, 'CRM Overview')

        # Poll /api/v1/analytics/overview/ using the same session cookie (matching analytics.js)
        poll_resp = self.web_client.get(
            '/api/v1/analytics/overview/?days=7',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(poll_resp.status_code, 200)
        self.assertEqual(poll_resp.json()['kpis']['total_leads']['value'], 4)

    def test_analytics_get_requests_are_strictly_read_only(self):
        """GET /api/v1/analytics/overview/ must execute only SELECT queries and never mutate stale website claims."""
        # Create an expired website claim on lead_c1
        avail = LeadAvailability.objects.create(
            lead=self.lead_c1,
            available_at=self.now - timedelta(minutes=30),
            claimed_by=self.caller1,
            claimed_at=self.now - timedelta(minutes=25),
        )
        history_count_before = LeadAssignmentHistory.objects.count()

        client = self._create_verified_token_client(self.caller1)
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get('/api/v1/analytics/overview/')
        self.assertEqual(resp.status_code, 200)

        # Verify every SQL statement was a read-only SELECT (or SAVEPOINT in test runner, never UPDATE/INSERT/DELETE)
        for q in ctx.captured_queries:
            sql_upper = q['sql'].strip().upper()
            self.assertFalse(
                sql_upper.startswith(('INSERT', 'UPDATE', 'DELETE')),
                f"Unexpected write query during analytics GET: {q['sql']}",
            )

        avail.refresh_from_db()
        self.lead_c1.refresh_from_db()
        self.assertEqual(avail.claimed_by_id, self.caller1.pk)
        self.assertEqual(self.lead_c1.assigned_caller_id, self.caller1.pk)
        self.assertEqual(LeadAssignmentHistory.objects.count(), history_count_before)

