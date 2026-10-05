from datetime import timedelta
from django.test import Client, TestCase
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
        response = self.web_client.get('/')
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
