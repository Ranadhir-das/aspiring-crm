"""Comprehensive automated tests for secure Windows PC activity tracking backend."""

from datetime import datetime, timedelta, date
import json
import uuid
from io import StringIO
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import User
from apps.activity.models import (
    ActivityCategory,
    ActivityBreak,
    BreakReasonConfig,
    DesktopDevice,
    DesktopTrackingSession,
    DesktopActivityBucket,
    DesktopAppUsage,
    DesktopDailySummary,
    WorkSession,
)
from apps.activity.desktop_services import (
    generate_pairing_code,
    register_or_pair_desktop_device,
    revoke_desktop_device,
    calculate_employee_daily_pc_summary,
    aggregate_and_prune_activity_buckets,
    IneligibleEmployeeError,
)
from apps.web.models import Attendance, AuditEvent


class DesktopTrackingBackendTests(TestCase):

    def setUp(self):
        self.client = Client()

        # 1. Create pilot users
        self.it_user = User.objects.create_user(
            username="it_pilot_user",
            password="SecurePassword123!",
            role=User.Role.IT,
            first_name="Ravi",
            last_name="Sharma",
            desktop_tracking_enabled=True,
        )
        self.editor_user = User.objects.create_user(
            username="editor_pilot_user",
            password="SecurePassword123!",
            role=User.Role.VIDEO_EDITOR,
            first_name="Pooja",
            last_name="Verma",
            desktop_tracking_enabled=True,
        )
        self.admin_user = User.objects.create_superuser(
            username="admin_tracker_user",
            password="SecurePassword123!",
            email="admin@vaani.internal",
            role=User.Role.ADMIN,
        )

        # 2. Activity Category & Break Reason
        self.category = ActivityCategory.objects.create(
            name="Development & IT",
            slug="it-dev",
            color_hex="#3B82F6",
            icon_key="code",
            is_active=True,
            is_default_general=True,
        )
        self.break_reason = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=60,
            is_active=True,
        )

        # 3. Create active WorkSession for IT user
        self.now = timezone.now()
        self.it_attendance = Attendance.objects.create(
            employee=self.it_user,
            date=self.now.date(),
            status="PRESENT",
            checked_in=self.now,
        )
        self.it_session = WorkSession.objects.create(
            user=self.it_user,
            attendance=self.it_attendance,
            date=self.now.date(),
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=2),
        )

        # 4. Pair device for IT user
        self.device, self.raw_token = register_or_pair_desktop_device(
            user=self.it_user,
            device_uuid="dev-it-001",
            machine_name="DESKTOP-IT-01",
            os_version="Windows 11 Pro",
            client_version="1.0.0",
        )
        self.auth_headers = {
            "HTTP_X_DEVICE_TOKEN": self.raw_token,
            "HTTP_X_DEVICE_UUID": "dev-it-001",
        }

    # =========================================================================
    # 1. Device Registration & Pairing Tests
    # =========================================================================

    def test_registration_with_pairing_code(self):
        # Generate code from web CRM
        code = generate_pairing_code(self.editor_user, valid_minutes=15)
        self.assertTrue(code.startswith("VN-"))

        resp = self.client.post(
            "/api/v1/desktop-tracker/register/",
            data=json.dumps({
                "device_uuid": "dev-editor-999",
                "machine_name": "VIDEO-EDIT-STATION",
                "pairing_code": code,
                "os_version": "Windows 11 Enterprise",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()
        self.assertEqual(data["status"], "APPROVED")
        self.assertIn("auth_token", data)
        self.assertEqual(data["employee_id"], self.editor_user.id)

        # Verify code cannot be reused
        resp_reused = self.client.post(
            "/api/v1/desktop-tracker/register/",
            data=json.dumps({
                "device_uuid": "dev-editor-other",
                "machine_name": "OTHER-PC",
                "pairing_code": code,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp_reused.status_code, status.HTTP_400_BAD_REQUEST)

    def test_registration_with_credentials(self):
        resp = self.client.post(
            "/api/v1/desktop-tracker/register/",
            data=json.dumps({
                "device_uuid": "dev-it-cred-1",
                "machine_name": "IT-LAPTOP",
                "username": "it_pilot_user",
                "password": "SecurePassword123!",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("auth_token", resp.json())

    # =========================================================================
    # 2. Heartbeat & Shift Status Tests
    # =========================================================================

    def test_heartbeat_status_returns_live_active_shift(self):
        resp = self.client.get(
            "/api/v1/desktop-tracker/status/",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertTrue(data["is_approved"])
        self.assertIsNotNone(data["session"])
        self.assertEqual(data["session"]["id"], self.it_session.id)
        self.assertEqual(data["session"]["status"], "ACTIVE")
        self.assertIn("server_time", data)

    # =========================================================================
    # 3. Batch Synchronization & Ingestion Tests
    # =========================================================================

    def test_sync_buckets_success(self):
        b_id = str(uuid.uuid4())
        b_start = (self.now - timedelta(minutes=10)).isoformat()
        b_end = (self.now - timedelta(minutes=9)).isoformat()

        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": b_start,
                    "bucket_end": b_end,
                    "active_seconds": 45,
                    "idle_seconds": 15,
                    "state": "ACTIVE",
                    "app_usages": [
                        {"process_name": "code.exe", "duration_seconds": 45}
                    ]
                }
            ]
        }

        resp = self.client.post(
            "/api/v1/desktop-tracker/sync/",
            data=json.dumps(payload),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        res_data = resp.json()
        self.assertEqual(res_data["ingested_count"], 1)
        self.assertEqual(res_data["duplicate_count"], 0)

        # Verify database record
        bucket = DesktopActivityBucket.objects.filter(bucket_id=b_id).first()
        self.assertIsNotNone(bucket)
        self.assertEqual(bucket.user, self.it_user)
        self.assertEqual(bucket.device, self.device)
        self.assertEqual(bucket.active_seconds, 45)
        self.assertEqual(bucket.idle_seconds, 15)

        # Verify app usage
        app = DesktopAppUsage.objects.filter(bucket=bucket).first()
        self.assertIsNotNone(app)
        self.assertEqual(app.process_name, "code.exe")
        self.assertEqual(app.duration_seconds, 45)

    def test_duplicate_sync_idempotency_does_not_double_count(self):
        b_id = str(uuid.uuid4())
        b_start = (self.now - timedelta(minutes=5)).isoformat()
        b_end = (self.now - timedelta(minutes=4)).isoformat()

        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": b_start,
                    "bucket_end": b_end,
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ]
        }

        # First delivery
        resp1 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.json()["ingested_count"], 1)

        # Duplicate delivery
        resp2 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.json()["duplicate_count"], 1)
        self.assertEqual(resp2.json()["ingested_count"], 0)

        # Exactly 1 row in DB
        self.assertEqual(DesktopActivityBucket.objects.filter(bucket_id=b_id).count(), 1)

    # =========================================================================
    # 4. Break Period Enforcement Tests
    # =========================================================================

    def test_break_periods_zero_out_active_time(self):
        break_start = self.now - timedelta(minutes=30)
        break_end = self.now - timedelta(minutes=15)

        # Record a break on the work session
        ActivityBreak.objects.create(
            session=self.it_session,
            reason_config=self.break_reason,
            started_at=break_start,
            ended_at=break_end,
            warning_threshold_minutes_snapshot=60,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
        )

        # Attempt to claim active work DURING the break period
        b_id = str(uuid.uuid4())
        b_start = (break_start + timedelta(minutes=2)).isoformat()
        b_end = (break_start + timedelta(minutes=3)).isoformat()

        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": b_start,
                    "bucket_end": b_end,
                    "active_seconds": 60,
                    "idle_seconds": 0,
                    "state": "ACTIVE",
                }
            ]
        }

        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        bucket = DesktopActivityBucket.objects.filter(bucket_id=b_id).first()
        self.assertIsNotNone(bucket)
        # Server must override active_seconds to 0 and state to PAUSED
        self.assertEqual(bucket.active_seconds, 0)
        self.assertEqual(bucket.state, "PAUSED")

    # =========================================================================
    # 5. Shift Validation & Completed Shift Tests
    # =========================================================================

    def test_sync_rejected_when_shift_completed(self):
        # End shift
        self.it_session.status = WorkSession.Status.COMPLETED
        self.it_session.ended_at = self.now - timedelta(minutes=10)
        self.it_session.closure_reason = WorkSession.ClosureReason.MANUAL
        self.it_session.save()

        b_id = str(uuid.uuid4())
        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": (self.now - timedelta(minutes=2)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=1)).isoformat(),
                    "active_seconds": 60,
                }
            ]
        }

        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("INVALID_SHIFT", resp.json()["error"])

    # =========================================================================
    # 6. Device Revocation & Unauthorized Access Tests
    # =========================================================================

    def test_revoked_device_is_rejected(self):
        revoke_desktop_device(self.device, self.admin_user, reason="Security review")

        resp = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

        # Verify audit log was written
        audit = AuditEvent.objects.filter(category="DESKTOP_DEVICE", actor=self.admin_user).first()
        self.assertIsNotNone(audit)
        self.assertIn("Revoked desktop device", audit.description)

    def test_unauthorized_token_rejected(self):
        bad_headers = {
            "HTTP_X_DEVICE_TOKEN": "invalid_fake_token_123",
            "HTTP_X_DEVICE_UUID": "dev-it-001",
        }
        resp = self.client.get("/api/v1/desktop-tracker/status/", **bad_headers)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    # =========================================================================
    # 7. Privacy Violations & Prohibited Data Rejection
    # =========================================================================

    def test_privacy_rejection_of_prohibited_telemetry(self):
        forbidden_payloads = [
            {"keystrokes": "user typed password"},
            {"screenshot": "base64_data"},
            {"window_title": "Confidential Salary Report.xlsx"},
            {"url": "https://intranet.company.com/payroll"},
            {"clipboard": "copied secret"},
        ]

        for forbidden in forbidden_payloads:
            b_id = str(uuid.uuid4())
            bucket_data = {
                "bucket_id": b_id,
                "bucket_start": (self.now - timedelta(minutes=2)).isoformat(),
                "bucket_end": (self.now - timedelta(minutes=1)).isoformat(),
                "active_seconds": 60,
            }
            bucket_data.update(forbidden)
            payload = {"device_uuid": "dev-it-001", "buckets": [bucket_data]}

            resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(resp.json()["error"], "PRIVACY_VIOLATION")

    # =========================================================================
    # 8. Large Payload Rejection Tests
    # =========================================================================

    def test_large_batch_rejected(self):
        # 121 buckets exceeds MAX_BATCH_SIZE (120)
        oversized = [
            {
                "bucket_id": str(uuid.uuid4()),
                "bucket_start": (self.now - timedelta(minutes=i)).isoformat(),
                "bucket_end": (self.now - timedelta(minutes=i-1)).isoformat(),
                "active_seconds": 60,
            }
            for i in range(125, 0, -1)
        ]
        payload = {"device_uuid": "dev-it-001", "buckets": oversized}

        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_PAYLOAD")

    # =========================================================================
    # 9. Untracked Time & Geofence Independence Tests
    # =========================================================================

    def test_missing_data_classified_as_untracked_not_idle(self):
        # Session duration = 2 hours = 7200 seconds
        # Ingest 30 minutes active, 10 minutes idle (total 40 min = 2400 seconds)
        b_id = str(uuid.uuid4())
        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": (self.now - timedelta(minutes=40)).isoformat(),
                    "bucket_end": (self.now).isoformat(),
                    "active_seconds": 1800,  # 30m
                    "idle_seconds": 600,    # 10m
                }
            ]
        }
        self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)

        summary = calculate_employee_daily_pc_summary(self.it_user, self.now.date())
        self.assertEqual(summary["pc_active_seconds"], 1800)
        self.assertEqual(summary["pc_idle_seconds"], 600)
        # Expected work time = 2 hours (7200s). Recorded = 2400s. Untracked = 4800s.
        self.assertEqual(summary["untracked_seconds"], summary["expected_work_seconds"] - 2400)
        self.assertGreater(summary["untracked_seconds"], 0)

    def test_pc_tracking_does_not_alter_attendance_or_geofence(self):
        initial_hours = self.it_attendance.hours

        # Ingest bucket
        b_id = str(uuid.uuid4())
        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                    "active_seconds": 60,
                }
            ]
        }
        self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)

        # Refresh attendance record
        self.it_attendance.refresh_from_db()
        self.assertEqual(self.it_attendance.hours, initial_hours)
        self.assertEqual(self.it_attendance.status, "PRESENT")

    # =========================================================================
    # 10. Rate Limiting / Throttling Tests
    # =========================================================================

    def test_registration_rate_limiting_throttles_after_five_attempts(self):
        cache.clear()
        # 5 attempts within 1 minute allowed (returning 400 for invalid code)
        for i in range(5):
            resp = self.client.post(
                "/api/v1/desktop-tracker/register/",
                data=json.dumps({
                    "device_uuid": f"throttle-test-{i}",
                    "pairing_code": "VN-0000",
                }),
                content_type="application/json",
                REMOTE_ADDR="198.51.100.42",
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 6th attempt from the same IP must be throttled with HTTP 429
        resp_throttled = self.client.post(
            "/api/v1/desktop-tracker/register/",
            data=json.dumps({
                "device_uuid": "throttle-test-6",
                "pairing_code": "VN-0000",
            }),
            content_type="application/json",
            REMOTE_ADDR="198.51.100.42",
        )
        self.assertEqual(resp_throttled.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    # =========================================================================
    # 11. Pilot Eligibility & Role Restriction Tests
    # =========================================================================

    def test_ineligible_roles_rejected_from_pc_tracking(self):
        caller_user = User.objects.create_user(
            username="caller_pilot_test",
            password="SecurePassword123!",
            role=User.Role.CALLER,
            first_name="Anita",
            last_name="Roy",
        )

        # 1. Pairing code generation blocked
        with self.assertRaises(IneligibleEmployeeError):
            generate_pairing_code(caller_user)

        # 2. Direct credential registration blocked
        resp = self.client.post(
            "/api/v1/desktop-tracker/register/",
            data=json.dumps({
                "device_uuid": "caller-dev-1",
                "machine_name": "CALLER-PC",
                "username": "caller_pilot_test",
                "password": "SecurePassword123!",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    # =========================================================================
    # 12. Strict Interval Validation & Malicious Payload Tests
    # =========================================================================

    def test_inverted_timestamps_rejected(self):
        # b_end earlier than b_start
        b_id = str(uuid.uuid4())
        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": self.now.isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=1)).isoformat(),
                    "active_seconds": 60,
                }
            ]
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_PAYLOAD")

    def test_future_bucket_timestamp_rejected(self):
        # b_start > 5 minutes in future
        b_id = str(uuid.uuid4())
        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": (self.now + timedelta(minutes=15)).isoformat(),
                    "bucket_end": (self.now + timedelta(minutes=16)).isoformat(),
                    "active_seconds": 60,
                }
            ]
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_PAYLOAD")

    def test_active_plus_idle_exceeding_duration_rejected(self):
        # 60s bucket with active 45s + idle 30s = 75s (> 60s)
        b_id = str(uuid.uuid4())
        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": (self.now - timedelta(minutes=3)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=2)).isoformat(),
                    "active_seconds": 45,
                    "idle_seconds": 30,
                }
            ]
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_PAYLOAD")

    def test_app_duration_exceeding_bucket_duration_rejected(self):
        # 60s bucket with app duration 75s
        b_id = str(uuid.uuid4())
        payload = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": (self.now - timedelta(minutes=4)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=3)).isoformat(),
                    "active_seconds": 60,
                    "app_usages": [
                        {"process_name": "malicious.exe", "duration_seconds": 75}
                    ]
                }
            ]
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_PAYLOAD")

    def test_overlapping_buckets_for_same_employee_rejected(self):
        # First ingest bucket for minute -10 to -9
        b1_id = str(uuid.uuid4())
        payload1 = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b1_id,
                    "bucket_start": (self.now - timedelta(minutes=10)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=9)).isoformat(),
                    "active_seconds": 60,
                }
            ]
        }
        resp1 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload1), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)

        # Attempt overlapping bucket for minute -9.5 to -8.5
        b2_id = str(uuid.uuid4())
        payload2 = {
            "device_uuid": "dev-it-001",
            "buckets": [
                {
                    "bucket_id": b2_id,
                    "bucket_start": (self.now - timedelta(minutes=9, seconds=30)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=8, seconds=30)).isoformat(),
                    "active_seconds": 60,
                }
            ]
        }
        resp2 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload2), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp2.json()["error"], "INVALID_PAYLOAD")

    # =========================================================================
    # 13. Activity Retention & Daily Summary Aggregation Tests
    # =========================================================================

    def test_retention_aggregation_and_pruning(self):
        old_date = self.now.date() - timedelta(days=95)
        old_start = self.now - timedelta(days=95, hours=4)
        old_end = old_start + timedelta(minutes=1)

        # Create an old historical tracking session and bucket
        hist_session = WorkSession.objects.create(
            user=self.it_user,
            attendance=self.it_attendance,
            date=old_date,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=old_start,
            ended_at=old_start + timedelta(hours=8),
        )
        hist_ts = DesktopTrackingSession.objects.create(
            device=self.device,
            work_session=hist_session,
            user=self.it_user,
            started_at=old_start,
            ended_at=old_start + timedelta(hours=8),
        )
        old_bucket = DesktopActivityBucket.objects.create(
            tracking_session=hist_ts,
            device=self.device,
            work_session=hist_session,
            user=self.it_user,
            bucket_id=uuid.uuid4(),
            bucket_start=old_start,
            bucket_end=old_end,
            active_seconds=45,
            idle_seconds=15,
            state="ACTIVE",
        )
        DesktopAppUsage.objects.create(
            bucket=old_bucket,
            process_name="pycharm64.exe",
            duration_seconds=45,
        )

        # 1. Test Dry Run via management command
        out_dry = StringIO()
        call_command("prune_desktop_activity", "--dry-run", stdout=out_dry)
        self.assertIn("DRY-RUN", out_dry.getvalue())
        # Old bucket must still exist
        self.assertTrue(DesktopActivityBucket.objects.filter(id=old_bucket.id).exists())
        self.assertFalse(DesktopDailySummary.objects.filter(user=self.it_user, date=old_date).exists())

        # 2. Test Execution
        out_live = StringIO()
        call_command("prune_desktop_activity", stdout=out_live)
        self.assertIn("CLEANUP COMPLETE", out_live.getvalue())

        # Old bucket must be deleted
        self.assertFalse(DesktopActivityBucket.objects.filter(id=old_bucket.id).exists())
        self.assertFalse(DesktopAppUsage.objects.filter(process_name="pycharm64.exe").exists())

        # Daily summary must exist with exact values
        summary = DesktopDailySummary.objects.filter(user=self.it_user, date=old_date).first()
        self.assertIsNotNone(summary)
        self.assertEqual(summary.total_active_seconds, 45)
        self.assertEqual(summary.total_idle_seconds, 15)
        self.assertEqual(summary.app_usage_summary.get("pycharm64.exe"), 45)

        # Legal/Attendance records must remain untouched
        self.it_attendance.refresh_from_db()
        self.assertEqual(self.it_attendance.status, "PRESENT")
        self.assertTrue(WorkSession.objects.filter(id=hist_session.id).exists())

    # =========================================================================
    # 14. WorkSession Calculation Lifecycle & Historical Clamping Tests
    # =========================================================================

    def test_historical_unclosed_work_session_does_not_extend_to_now(self):
        hist_user = User.objects.create_user(
            username="hist_unclosed_test_user",
            password="SecurePassword123!",
            role=User.Role.IT,
        )
        hist_date = timezone.localdate() - timedelta(days=14)
        hist_start = timezone.now() - timedelta(days=14, hours=6)

        hist_att = Attendance.objects.create(
            employee=hist_user,
            date=hist_date,
            status="PRESENT",
            checked_in=hist_start,
        )
        # Create unclosed session (ended_at=None)
        WorkSession.objects.create(
            user=hist_user,
            attendance=hist_att,
            date=hist_date,
            status=WorkSession.Status.ACTIVE,
            started_at=hist_start,
            ended_at=None,
        )

        summary = calculate_employee_daily_pc_summary(hist_user, hist_date)
        # Must not calculate (now - hist_start) which would be 14 days (~1.2 million seconds)
        self.assertEqual(summary["expected_work_seconds"], 0)
        self.assertEqual(summary["untracked_seconds"], 0)

    def test_today_unclosed_work_session_extends_to_now(self):
        today = timezone.localdate()
        # self.it_session was created in setUp() 2 hours ago with ended_at=None
        summary = calculate_employee_daily_pc_summary(self.it_user, today)
        # Expected work time should be ~2 hours (7200 seconds +/- 15s)
        self.assertAlmostEqual(summary["expected_work_seconds"], 7200, delta=15)

    def test_closed_historical_work_session_uses_exact_ended_at(self):
        hist_date = timezone.localdate() - timedelta(days=5)
        hist_start = timezone.now() - timedelta(days=5, hours=8)
        hist_end = hist_start + timedelta(hours=6, minutes=30)

        hist_att = Attendance.objects.create(
            employee=self.it_user,
            date=hist_date,
            status="PRESENT",
            checked_in=hist_start,
        )
        WorkSession.objects.create(
            user=self.it_user,
            attendance=hist_att,
            date=hist_date,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=hist_start,
            ended_at=hist_end,
        )

        summary = calculate_employee_daily_pc_summary(self.it_user, hist_date)
        expected_seconds = int((6 * 3600) + (30 * 60))  # 23400s
        self.assertEqual(summary["expected_work_seconds"], expected_seconds)

    def test_multiple_work_sessions_on_same_day_accumulates_correctly(self):
        multi_date = timezone.localdate() - timedelta(days=3)
        day_start = timezone.now() - timedelta(days=3, hours=9)

        multi_att = Attendance.objects.create(
            employee=self.it_user,
            date=multi_date,
            status="PRESENT",
            checked_in=day_start,
        )

        # Session 1: 3 hours (10800s)
        WorkSession.objects.create(
            user=self.it_user,
            attendance=multi_att,
            date=multi_date,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=day_start,
            ended_at=day_start + timedelta(hours=3),
        )

        # Session 2: 4 hours (14400s) with 30m break (1800s)
        s2_start = day_start + timedelta(hours=4)
        s2 = WorkSession.objects.create(
            user=self.it_user,
            attendance=multi_att,
            date=multi_date,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=s2_start,
            ended_at=s2_start + timedelta(hours=4),
        )
        ActivityBreak.objects.create(
            session=s2,
            reason_config=self.break_reason,
            started_at=s2_start + timedelta(hours=1),
            ended_at=s2_start + timedelta(hours=1, minutes=30),
            warning_threshold_minutes_snapshot=60,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
        )

        summary = calculate_employee_daily_pc_summary(self.it_user, multi_date)
        # Gross = 3h + 4h = 7h (25200s). Break = 30m (1800s). Expected = 23400s (6.5h).
        self.assertEqual(summary["break_seconds"], 1800)
        self.assertEqual(summary["expected_work_seconds"], 23400)
