from datetime import datetime, time, timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Lead
from apps.performance.models import PointsEntry


class PointsDashboardAndTalkTimeTests(TestCase):
    """
    Tests for Points Overview, Points Breakdown, Talk Time calculations,
    and Detailed Points History endpoints and business rules.
    """

    def setUp(self):
        self.client = APIClient()
        self.caller1 = User.objects.create_user(
            username="caller_alice",
            password="testpass123",
            role="CALLER",
            can_act_as_caller=True,
        )
        self.caller2 = User.objects.create_user(
            username="caller_bob",
            password="testpass123",
            role="CALLER",
            can_act_as_caller=True,
        )
        self.client.force_authenticate(user=self.caller1)
        self.lead = Lead.objects.create(name="Rahul Sharma", phone="9876543210")

    def test_lifetime_earned_versus_deductions_and_net_balance(self):
        now = timezone.now()
        # Positive transactions
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.INTERESTED_LEAD,
            points=25,
            reason="Interested lead",
            event_key="test:pt:1",
            occurred_at=now - timedelta(days=2),
        )
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.CALL_DAILY_BONUS,
            points=50,
            reason="Daily call milestone",
            event_key="test:pt:2",
            occurred_at=now - timedelta(days=1),
        )
        # Negative transaction (deduction/penalty)
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.MISSED_FOLLOWUP,
            points=-15,
            reason="Missed follow-up penalty",
            event_key="test:pt:3",
            occurred_at=now - timedelta(hours=2),
        )

        resp = self.client.get("/api/v1/points/me/?all_time=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data["summary"]

        # Lifetime earned: 25 + 50 = 75
        self.assertEqual(summary["lifetime_earned"], 75)
        # Lifetime deducted: |-15| = 15
        self.assertEqual(summary["lifetime_deducted"], 15)
        # Current balance: 75 - 15 = 60
        self.assertEqual(summary["current_balance"], 60)

    def test_today_points_and_deductions(self):
        today = timezone.localdate()
        today_noon = timezone.make_aware(datetime.combine(today, time(12, 0)))
        yesterday_noon = today_noon - timedelta(days=1)

        # Yesterday transaction
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.INTERESTED_LEAD,
            points=30,
            reason="Yesterday lead",
            event_key="test:yest:1",
            occurred_at=yesterday_noon,
        )
        # Today positive transaction
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.COUNSELLING_COMPLETED,
            points=40,
            reason="Today counselling",
            event_key="test:today:1",
            occurred_at=today_noon - timedelta(hours=1),
        )
        # Today deduction
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.MISSED_FOLLOWUP,
            points=-5,
            reason="Today penalty",
            event_key="test:today:2",
            occurred_at=today_noon,
        )

        resp = self.client.get("/api/v1/points/me/")
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()["summary"]

        self.assertEqual(summary["today_earned"], 40)
        self.assertEqual(summary["today_deducted"], 5)
        self.assertEqual(summary["today_net"], 35)

    def test_breakdown_separates_earned_and_deductions(self):
        now = timezone.now()
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.CALL_DAILY_BONUS,
            points=50,
            reason="Bonus",
            event_key="bk:1",
            occurred_at=now,
        )
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.MISSED_FOLLOWUP,
            points=-10,
            reason="Penalty",
            event_key="bk:2",
            occurred_at=now,
        )

        resp = self.client.get("/api/v1/points/me/?all_time=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertTrue(len(data["earned_breakup"]) >= 1)
        self.assertEqual(data["earned_breakup"][0]["event"], "CALL_DAILY_BONUS")
        self.assertEqual(data["earned_breakup"][0]["points"], 50)

        self.assertTrue(len(data["deduction_breakup"]) >= 1)
        self.assertEqual(data["deduction_breakup"][0]["event"], "MISSED_FOLLOWUP")
        self.assertEqual(data["deduction_breakup"][0]["points"], -10)

    def test_running_balance_in_ledger_transactions(self):
        now = timezone.now()
        p1 = PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.CALL_DAILY_BONUS,
            points=25,
            reason="Call milestone 1",
            event_key="rb:1",
            occurred_at=now - timedelta(hours=3),
        )
        p2 = PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.INTERESTED_LEAD,
            points=30,
            reason="Interested lead",
            event_key="rb:2",
            occurred_at=now - timedelta(hours=2),
        )
        p3 = PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.MISSED_FOLLOWUP,
            points=-10,
            reason="Missed follow-up",
            event_key="rb:3",
            occurred_at=now - timedelta(hours=1),
        )

        resp = self.client.get("/api/v1/points/me/?all_time=true")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]

        # Results ordered -occurred_at: p3, p2, p1
        self.assertEqual(results[0]["id"], p3.pk)
        self.assertEqual(results[0]["running_balance"], 45)  # 25 + 30 - 10

        self.assertEqual(results[1]["id"], p2.pk)
        self.assertEqual(results[1]["running_balance"], 55)  # 25 + 30

        self.assertEqual(results[2]["id"], p1.pk)
        self.assertEqual(results[2]["running_balance"], 25)  # 25

    def test_transaction_filtering_by_type_earned_and_deducted(self):
        now = timezone.now()
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.CALL_DAILY_BONUS,
            points=50,
            reason="Earned bonus",
            event_key="tf:1",
            occurred_at=now - timedelta(minutes=10),
        )
        PointsEntry.objects.create(
            caller=self.caller1,
            event=PointsEntry.Event.MISSED_FOLLOWUP,
            points=-15,
            reason="Deduction penalty",
            event_key="tf:2",
            occurred_at=now - timedelta(minutes=5),
        )

        resp_earned = self.client.get("/api/v1/points/me/?type=earned&all_time=true")
        self.assertEqual(resp_earned.status_code, 200)
        items_earned = resp_earned.json()["results"]
        self.assertEqual(len(items_earned), 1)
        self.assertEqual(items_earned[0]["points"], 50)

        resp_deducted = self.client.get("/api/v1/points/me/?type=deducted&all_time=true")
        self.assertEqual(resp_deducted.status_code, 200)
        items_deducted = resp_deducted.json()["results"]
        self.assertEqual(len(items_deducted), 1)
        self.assertEqual(items_deducted[0]["points"], -15)

    def test_talk_time_excludes_unconnected_calls(self):
        now = timezone.now()
        # 1. Connected call: duration 120s, outcome INTERESTED
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead,
            started_at=now - timedelta(hours=2),
            ended_at=now - timedelta(hours=2) + timedelta(seconds=120),
            duration_seconds=120,
            outcome=Call.Outcome.INTERESTED,
        )
        # 2. Connected call: duration 80s, outcome NOT_INTERESTED
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead,
            started_at=now - timedelta(hours=1),
            ended_at=now - timedelta(hours=1) + timedelta(seconds=80),
            duration_seconds=80,
            outcome=Call.Outcome.NOT_INTERESTED,
        )
        # 3. Unconnected call: NO_ANSWER with 0s duration
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead,
            started_at=now - timedelta(minutes=45),
            duration_seconds=0,
            outcome=Call.Outcome.NO_ANSWER,
        )
        # 4. Unconnected call: BUSY with duration 15s (network ring tone)
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead,
            started_at=now - timedelta(minutes=30),
            duration_seconds=15,
            outcome=Call.Outcome.BUSY,
        )
        # 5. Connected call from OTHER caller (should be isolated!)
        Call.objects.create(
            caller=self.caller2,
            lead=self.lead,
            started_at=now - timedelta(minutes=20),
            duration_seconds=300,
            outcome=Call.Outcome.INTERESTED,
        )

        resp = self.client.get("/api/v1/points/talk-time/?timeframe=today")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Caller 1 placed 4 calls total: 2 connected, 2 unconnected.
        # Total talk time: 120 + 80 = 200s
        self.assertEqual(data["total_calls_count"], 4)
        self.assertEqual(data["connected_calls_count"], 2)
        self.assertEqual(data["unconnected_calls_count"], 2)
        self.assertEqual(data["connection_rate_percent"], 50.0)
        self.assertEqual(data["total_talk_time_seconds"], 200)
        # Average per connected call: 200 / 2 = 100s -> "1m 40s"
        self.assertEqual(data["average_duration_seconds"], 100)
        self.assertEqual(data["formatted_avg_duration"], "1m 40s")
        # Average per total call: 200 / 4 = 50s -> "0m 50s"
        self.assertEqual(data["average_talk_time_per_call_seconds"], 50)
        self.assertEqual(data["formatted_avg_talk_time_per_call"], "0m 50s")
        self.assertEqual(data["formatted_talk_time"], "3m 20s")

    def test_talk_time_date_filters(self):
        today = timezone.localdate()
        today_noon = timezone.make_aware(datetime.combine(today, time(12, 0)))
        yesterday_noon = today_noon - timedelta(days=1)
        two_days_ago_noon = today_noon - timedelta(days=2)

        # Call today
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead,
            started_at=today_noon,
            duration_seconds=60,
            outcome=Call.Outcome.INTERESTED,
        )
        # Call yesterday
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead,
            started_at=yesterday_noon,
            duration_seconds=120,
            outcome=Call.Outcome.INTERESTED,
        )
        # Call 2 days ago
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead,
            started_at=two_days_ago_noon,
            duration_seconds=180,
            outcome=Call.Outcome.INTERESTED,
        )

        # 1. Today filter
        resp_today = self.client.get("/api/v1/points/talk-time/?timeframe=today")
        self.assertEqual(resp_today.status_code, 200)
        self.assertEqual(resp_today.json()["total_calls_count"], 1)
        self.assertEqual(resp_today.json()["total_talk_time_seconds"], 60)

        # 2. Yesterday filter
        resp_yest = self.client.get("/api/v1/points/talk-time/?timeframe=yesterday")
        self.assertEqual(resp_yest.status_code, 200)
        self.assertEqual(resp_yest.json()["total_calls_count"], 1)
        self.assertEqual(resp_yest.json()["total_talk_time_seconds"], 120)

        # 3. 7 Days filter
        resp_7d = self.client.get("/api/v1/points/talk-time/?timeframe=7days")
        self.assertEqual(resp_7d.status_code, 200)
        self.assertEqual(resp_7d.json()["total_calls_count"], 3)
        self.assertEqual(resp_7d.json()["total_talk_time_seconds"], 360)

        # 4. This Month filter
        resp_month = self.client.get("/api/v1/points/talk-time/?timeframe=this_month")
        self.assertEqual(resp_month.status_code, 200)
        # All calls were within this month if today >= 3rd, else within month
        self.assertTrue(resp_month.json()["total_calls_count"] >= 1)

        # 5. Custom Date Range
        start_str = (today - timedelta(days=2)).isoformat()
        end_str = (today - timedelta(days=1)).isoformat()
        resp_custom = self.client.get(f"/api/v1/points/talk-time/?timeframe=custom&start_date={start_str}&end_date={end_str}")
        self.assertEqual(resp_custom.status_code, 200)
        self.assertEqual(resp_custom.json()["total_calls_count"], 2)
        self.assertEqual(resp_custom.json()["total_talk_time_seconds"], 300)

        # 6. Invalid custom date range (end before start)
        resp_invalid = self.client.get(f"/api/v1/points/talk-time/?timeframe=custom&start_date={end_str}&end_date={start_str}")
        self.assertEqual(resp_invalid.status_code, 400)

    def test_talk_time_unauthenticated(self):
        anon_client = APIClient()
        resp = anon_client.get("/api/v1/points/talk-time/")
        self.assertEqual(resp.status_code, 401)

    def test_caller_isolation_cannot_view_other_caller_data(self):
        now = timezone.now()
        PointsEntry.objects.create(
            caller=self.caller2,
            event=PointsEntry.Event.CALL_DAILY_BONUS,
            points=100,
            reason="Caller2 Bonus",
            event_key="c2:1",
            occurred_at=now,
        )

        # Authenticated as caller1, requesting points for caller2 should be forbidden (403)
        resp = self.client.get(f"/api/v1/points/callers/{self.caller2.pk}/")
        self.assertEqual(resp.status_code, 403)

        # Requesting talk time for caller2 should be forbidden (403)
        resp_tt = self.client.get(f"/api/v1/points/callers/{self.caller2.pk}/talk-time/")
        self.assertEqual(resp_tt.status_code, 403)

    def test_empty_points_and_talk_time_state(self):
        # Caller has no points and no calls
        resp = self.client.get("/api/v1/points/me/?all_time=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["results"], [])
        self.assertEqual(data["summary"]["lifetime_earned"], 0)
        self.assertEqual(data["summary"]["lifetime_deducted"], 0)
        self.assertEqual(data["summary"]["current_balance"], 0)

        resp_tt = self.client.get("/api/v1/points/talk-time/?timeframe=7days")
        self.assertEqual(resp_tt.status_code, 200)
        tt_data = resp_tt.json()
        self.assertEqual(tt_data["total_calls_count"], 0)
        self.assertEqual(tt_data["connected_calls_count"], 0)
        self.assertEqual(tt_data["unconnected_calls_count"], 0)
        self.assertEqual(tt_data["connection_rate_percent"], 0.0)
        self.assertEqual(tt_data["total_talk_time_seconds"], 0)
        self.assertEqual(tt_data["formatted_talk_time"], "0m 0s")
        self.assertEqual(tt_data["average_duration_seconds"], 0)
        self.assertEqual(tt_data["formatted_avg_duration"], "0m 0s")
