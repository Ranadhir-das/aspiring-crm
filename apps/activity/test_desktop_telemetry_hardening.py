"""Comprehensive tests for Desktop Telemetry Synchronization and Lifecycle Hardening (Phase 1, Step 4).

Verifies:
1. Valid batch ingestion & metrics aggregation.
2. Idempotency & duplicate batch retries (no double-counting).
3. Concurrent duplicate submission race condition handling via DB savepoints.
4. Device & employee authorization, isolation, and UUID matching.
5. Ineligible & revoked device sync rejection.
6. Rejection of telemetry on completed shifts & rejection of old records from previous shifts.
7. Batch atomicity (all-or-nothing rollback on invalid bucket data).
8. DesktopTrackingSession lifecycle cleanup on Desktop, Mobile, Web, and Auto-close completions.
9. Repeated completion idempotency and new shift tracking session isolation.
10. Privacy audit compliance.
"""

from datetime import datetime, timedelta
import json
import threading
import uuid
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, Client
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
    WorkSession,
)
from apps.activity.desktop_services import (
    register_or_pair_desktop_device,
    revoke_desktop_device,
    ingest_activity_buckets,
    get_or_create_tracking_session,
)
from apps.activity.services import (
    start_work_session,
    end_work_session,
    recover_overdue_sessions,
)
from apps.web.models import Attendance, AttendanceCorrectionRequest, AuditEvent


class DesktopTelemetryHardeningTests(TestCase):
    """Unit and integration tests for telemetry ingestion and lifecycle hardening."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.now = timezone.now()
        self.today = self.now.date()

        # Eligible IT employee
        self.user = User.objects.create_user(
            username="hardened_it_specialist",
            password="SecurePassword123!",
            role=User.Role.IT,
            desktop_tracking_enabled=True,
            shift_workflow_enabled=True,
        )

        # Second eligible employee
        self.other_user = User.objects.create_user(
            username="other_editor",
            password="SecurePassword123!",
            role=User.Role.VIDEO_EDITOR,
            desktop_tracking_enabled=True,
            shift_workflow_enabled=True,
        )

        # Admin user
        self.admin = User.objects.create_user(
            username="admin_auditor",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            is_staff=True,
        )

        # Category
        self.category = ActivityCategory.objects.create(
            name="General Operations",
            slug="gen-ops",
            is_active=True,
            is_default_general=True,
        )

        # Active attendance and work session for user
        self.attendance = Attendance.objects.create(
            employee=self.user,
            date=self.today,
            status="PRESENT",
            checked_in=self.now - timedelta(hours=2),
        )
        self.session = start_work_session(
            user=self.user,
            attendance=self.attendance,
            category=self.category,
            started_at=self.now - timedelta(hours=2),
        )

        # Register device for user
        self.device, self.raw_token = register_or_pair_desktop_device(
            user=self.user,
            device_uuid="dev-hardened-001",
            machine_name="HARDENED-WORKSTATION-01",
        )
        self.auth_headers = {
            "HTTP_X_DEVICE_TOKEN": self.raw_token,
            "HTTP_X_DEVICE_UUID": "dev-hardened-001",
        }

        # Register device for other user
        self.other_device, self.other_token = register_or_pair_desktop_device(
            user=self.other_user,
            device_uuid="dev-other-002",
            machine_name="OTHER-WORKSTATION-02",
        )
        self.other_headers = {
            "HTTP_X_DEVICE_TOKEN": self.other_token,
            "HTTP_X_DEVICE_UUID": "dev-other-002",
        }

    # =========================================================================
    # Workstream A: Batch Ownership, Authorization & Boundary Validation
    # =========================================================================

    def test_valid_batch_ingestion_success(self):
        b1_id = str(uuid.uuid4())
        b1_start = (self.now - timedelta(minutes=10)).isoformat()
        b1_end = (self.now - timedelta(minutes=9)).isoformat()

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": b1_id,
                    "bucket_start": b1_start,
                    "bucket_end": b1_end,
                    "active_seconds": 50,
                    "idle_seconds": 10,
                    "state": "ACTIVE",
                    "app_usages": [
                        {"process_name": "pycharm64.exe", "duration_seconds": 40},
                        {"process_name": "chrome.exe", "duration_seconds": 10},
                    ],
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(data["ingested_count"], 1)
        self.assertEqual(data["duplicate_count"], 0)

        # Check DB bucket
        b = DesktopActivityBucket.objects.get(bucket_id=b1_id)
        self.assertEqual(b.user, self.user)
        self.assertEqual(b.device, self.device)
        self.assertEqual(b.work_session, self.session)
        self.assertEqual(b.active_seconds, 50)
        self.assertEqual(b.idle_seconds, 10)

        # Check app usages
        apps = list(DesktopAppUsage.objects.filter(bucket=b).order_by("process_name"))
        self.assertEqual(len(apps), 2)
        self.assertEqual(apps[0].process_name, "chrome.exe")
        self.assertEqual(apps[0].duration_seconds, 10)
        self.assertEqual(apps[1].process_name, "pycharm64.exe")
        self.assertEqual(apps[1].duration_seconds, 40)

        # Check tracking session aggregates
        ts = DesktopTrackingSession.objects.get(work_session=self.session)
        self.assertEqual(ts.total_active_seconds, 50)
        self.assertEqual(ts.total_idle_seconds, 10)

    def test_cross_device_uuid_mismatch_rejected(self):
        # Mismatched payload device_uuid
        payload = {
            "device_uuid": "dev-other-002",  # Mismatch with token's device
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "DEVICE_MISMATCH")

        # Mismatched X-Device-UUID header
        bad_headers = {
            "HTTP_X_DEVICE_TOKEN": self.raw_token,
            "HTTP_X_DEVICE_UUID": "dev-other-002",
        }
        resp_hdr = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps({"buckets": []}), content_type="application/json", **bad_headers)
        self.assertEqual(resp_hdr.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_revoked_device_sync_rejected(self):
        revoke_desktop_device(self.device, self.admin, reason="Test revocation")
        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_disabled_employee_sync_rejected(self):
        self.user.desktop_tracking_enabled = False
        self.user.save()

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    def test_completed_shift_sync_rejected(self):
        """Buckets timestamped after a completed shift ended are rejected."""
        end_work_session(self.session.id, self.user, ended_at=self.now)

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": (self.now + timedelta(minutes=10)).isoformat(),
                    "bucket_end": (self.now + timedelta(minutes=11)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_SHIFT")

    def test_old_buckets_preceding_shift_start_rejected(self):
        """Old records from yesterday or before current shift start cannot be attached to new shift."""
        old_start = (self.session.started_at - timedelta(hours=3)).isoformat()
        old_end = (self.session.started_at - timedelta(hours=2, minutes=59)).isoformat()

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": old_start,
                    "bucket_end": old_end,
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_SHIFT")
        self.assertIn("precedes active shift start", resp.json()["message"])

    # =========================================================================
    # Workstream B: Idempotency & Duplicate Synchronization
    # =========================================================================

    def test_duplicate_batch_retry_idempotent(self):
        b_id = str(uuid.uuid4())
        b_start = (self.now - timedelta(minutes=5)).isoformat()
        b_end = (self.now - timedelta(minutes=4)).isoformat()

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": b_id,
                    "bucket_start": b_start,
                    "bucket_end": b_end,
                    "active_seconds": 60,
                    "idle_seconds": 0,
                    "app_usages": [{"process_name": "editor.exe", "duration_seconds": 60}],
                }
            ],
        }

        # 1st submission
        resp1 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.json()["ingested_count"], 1)
        self.assertEqual(resp1.json()["duplicate_count"], 0)

        # 2nd submission (retry)
        resp2 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.json()["ingested_count"], 0)
        self.assertEqual(resp2.json()["duplicate_count"], 1)

        # DB must have exactly 1 bucket and 1 app usage row
        self.assertEqual(DesktopActivityBucket.objects.filter(bucket_id=b_id).count(), 1)
        b = DesktopActivityBucket.objects.get(bucket_id=b_id)
        self.assertEqual(DesktopAppUsage.objects.filter(bucket=b).count(), 1)

        # Tracking session totals must NOT be double-counted
        ts = DesktopTrackingSession.objects.get(work_session=self.session)
        self.assertEqual(ts.total_active_seconds, 60)
        self.assertEqual(ts.total_idle_seconds, 0)

    # =========================================================================
    # Workstream C: Batch Atomicity & All-Or-Nothing Rollback
    # =========================================================================

    def test_batch_atomicity_all_or_nothing_rollback(self):
        """A batch containing one invalid bucket rolls back entirely; zero buckets persisted."""
        valid_b_id = str(uuid.uuid4())
        invalid_b_id = str(uuid.uuid4())

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": valid_b_id,
                    "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                },
                {
                    "bucket_id": invalid_b_id,
                    "bucket_start": (self.now - timedelta(minutes=4)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=5)).isoformat(),  # End precedes start (invalid!)
                    "active_seconds": 60,
                    "idle_seconds": 0,
                },
            ],
        }

        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_PAYLOAD")

        # Entire batch rolled back: valid_b_id was NOT committed
        self.assertFalse(DesktopActivityBucket.objects.filter(bucket_id=valid_b_id).exists())
        self.assertFalse(DesktopActivityBucket.objects.filter(bucket_id=invalid_b_id).exists())

    def test_invalid_bucket_formats_rejected(self):
        # Missing bucket_id
        p1 = {"device_uuid": "dev-hardened-001", "buckets": [{"bucket_start": self.now.isoformat()}]}
        resp1 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(p1), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp1.json()["error"], "INVALID_PAYLOAD")

        # Future timestamp
        p2 = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": (self.now + timedelta(hours=2)).isoformat(),
                    "bucket_end": (self.now + timedelta(hours=2, minutes=1)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp2 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(p2), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp2.json()["error"], "INVALID_PAYLOAD")

        # Active + Idle > bucket duration
        p3 = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                    "active_seconds": 50,
                    "idle_seconds": 20,  # 50 + 20 = 70s > 60s
                }
            ],
        }
        resp3 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(p3), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp3.status_code, status.HTTP_400_BAD_REQUEST)

        # App usage duration > bucket duration
        p4 = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                    "app_usages": [{"process_name": "app.exe", "duration_seconds": 90}],
                }
            ],
        }
        resp4 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(p4), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp4.status_code, status.HTTP_400_BAD_REQUEST)

    # =========================================================================
    # Workstream D: DesktopTrackingSession Lifecycle Across Completion Surfaces
    # =========================================================================

    def test_mobile_shift_completion_cleans_up_tracking_session(self):
        """When mobile calls end_work_session, DesktopTrackingSession.ended_at is populated."""
        ts = get_or_create_tracking_session(self.device, self.session)
        self.assertIsNone(ts.ended_at)

        end_time = self.now + timedelta(minutes=1)
        # Mobile completion path invokes end_work_session()
        end_work_session(self.session.id, self.user, ended_at=end_time)

        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WorkSession.Status.COMPLETED)

        ts.refresh_from_db()
        self.assertIsNotNone(ts.ended_at)
        self.assertEqual(ts.ended_at, end_time)

    def test_web_shift_completion_cleans_up_tracking_session(self):
        """When web CRM ends shift, DesktopTrackingSession.ended_at is populated."""
        ts = get_or_create_tracking_session(self.device, self.session)
        self.assertIsNone(ts.ended_at)

        end_time = self.now + timedelta(minutes=2)
        end_work_session(self.session.id, self.user, ended_at=end_time)

        ts.refresh_from_db()
        self.assertEqual(ts.ended_at, end_time)

    def test_auto_close_cutoff_cleans_up_tracking_session(self):
        """When midnight cutoff auto-closes stale shift, DesktopTrackingSession is finalized."""
        ts = get_or_create_tracking_session(self.device, self.session)
        self.assertIsNone(ts.ended_at)

        # Run auto-close with future time
        summary = recover_overdue_sessions(now=self.now + timedelta(days=2))
        self.assertGreaterEqual(summary["closed"], 1)

        ts.refresh_from_db()
        self.assertIsNotNone(ts.ended_at)

    def test_repeated_completion_is_safe_and_idempotent(self):
        ts = get_or_create_tracking_session(self.device, self.session)
        end_time = self.now + timedelta(minutes=1)

        s1 = end_work_session(self.session.id, self.user, ended_at=end_time)
        ts.refresh_from_db()
        first_ended = ts.ended_at

        # Repeated completion call
        s2 = end_work_session(self.session.id, self.user, ended_at=end_time + timedelta(hours=1))
        self.assertEqual(s1.id, s2.id)

        # Tracking session ended_at must NOT be overwritten
        ts.refresh_from_db()
        self.assertEqual(ts.ended_at, first_ended)

    def test_new_shift_creates_separate_tracking_session_preserving_history(self):
        # 1. Ensure tracking session exists, then end first shift
        ts1 = get_or_create_tracking_session(self.device, self.session)
        end_work_session(self.session.id, self.user, ended_at=self.now)
        ts1.refresh_from_db()
        self.assertIsNotNone(ts1.ended_at)

        # 2. Start new shift later
        next_day = self.today + timedelta(days=1)
        next_att = Attendance.objects.create(
            employee=self.user,
            date=next_day,
            status="PRESENT",
            checked_in=self.now + timedelta(days=1),
        )
        new_session = start_work_session(
            user=self.user,
            attendance=next_att,
            category=self.category,
            started_at=self.now + timedelta(days=1),
        )

        ts2 = get_or_create_tracking_session(self.device, new_session)
        self.assertNotEqual(ts1.id, ts2.id)
        self.assertEqual(ts2.work_session, new_session)
        self.assertIsNone(ts2.ended_at)

        # Verify historical ts1 is still intact
        ts1.refresh_from_db()
        self.assertIsNotNone(ts1.ended_at)
        self.assertEqual(ts1.work_session, self.session)

    # =========================================================================
    # Workstream F: Privacy Audit
    # =========================================================================

    def test_privacy_audit_rejects_prohibited_telemetry(self):
        prohibited_keys = ["keystrokes", "screenshot", "window_title", "url", "clipboard"]
        for key in prohibited_keys:
            payload = {
                "device_uuid": "dev-hardened-001",
                "buckets": [
                    {
                        "bucket_id": str(uuid.uuid4()),
                        "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                        "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                        "active_seconds": 60,
                        "idle_seconds": 0,
                        key: "sensitive_data",
                    }
                ],
            }
            resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(resp.json()["error"], "PRIVACY_VIOLATION")

    # =========================================================================
    # Workstream G: Secure Completed-Shift Offline Telemetry Recovery
    # =========================================================================

    def test_exact_30_offline_buckets_recovery_scenario(self):
        """Exact Step 4 scenario:
        1. Employee starts WorkSession A.
        2. DesktopTrackingSession A is created.
        3. 30 valid telemetry buckets generated during WorkSession A, kept offline.
        4. Employee ends WorkSession A (COMPLETED, ts_a.ended_at set).
        5. Employee starts WorkSession B (ACTIVE).
        6. Upload 30 old buckets.
        7. Verify all 30 associated with WorkSession A, NEVER WorkSession B.
        8. Active/idle totals and app usages counted exactly once.
        9. Duplicate retry of 30 buckets is acknowledged without changing totals.
        10. Historical WorkSession start/end and Attendance checked_out untouched.
        """
        # Step 1: WorkSession A (yesterday, from 8h ago to 1h ago)
        yesterday = self.today - timedelta(days=1)
        att_a = self.attendance
        att_a.date = yesterday
        att_a.checked_in = self.now - timedelta(days=1, hours=8)
        att_a.save(update_fields=["date", "checked_in"])

        ws_a = self.session
        ws_a_start = self.now - timedelta(days=1, hours=8)
        ws_a_end = self.now - timedelta(days=1, hours=1)
        ws_a.date = yesterday
        ws_a.started_at = ws_a_start
        ws_a.save(update_fields=["date", "started_at"])
        ws_a.segments.update(started_at=ws_a_start)

        # Step 2: DesktopTrackingSession A
        ts_a = get_or_create_tracking_session(self.device, ws_a)

        # Step 3: Generate 30 buckets during WorkSession A (kept offline)
        offline_buckets = []
        for i in range(30):
            b_start = ws_a_start + timedelta(minutes=i + 1)
            b_end = b_start + timedelta(minutes=1)
            offline_buckets.append({
                "bucket_id": str(uuid.uuid4()),
                "session_id": ws_a.id,
                "bucket_start": b_start.isoformat(),
                "bucket_end": b_end.isoformat(),
                "active_seconds": 50,
                "idle_seconds": 10,
                "state": "ACTIVE",
                "app_usages": [
                    {"process_name": "code.exe", "duration_seconds": 50}
                ],
            })

        # Step 4: Employee ends WorkSession A
        end_work_session(ws_a.id, self.user, ended_at=ws_a_end)
        ws_a.refresh_from_db()
        ts_a.refresh_from_db()
        self.assertEqual(ws_a.status, WorkSession.Status.COMPLETED)
        self.assertEqual(ws_a.ended_at, ws_a_end)
        self.assertIsNotNone(ts_a.ended_at)
        initial_ts_a_active = ts_a.total_active_seconds
        initial_ts_a_idle = ts_a.total_idle_seconds

        # Record original Attendance state
        att_a.refresh_from_db()
        original_checked_in = att_a.checked_in
        original_checked_out = att_a.checked_out

        # Step 5: Employee starts WorkSession B (1 hour ago)
        ws_b_start = self.now - timedelta(hours=1)
        att_b = Attendance.objects.create(
            employee=self.user,
            date=self.today,
            status="PRESENT",
            checked_in=ws_b_start,
        )
        ws_b = start_work_session(
            user=self.user,
            attendance=att_b,
            category=self.category,
            started_at=ws_b_start,
        )
        ts_b = get_or_create_tracking_session(self.device, ws_b)
        self.assertEqual(ws_b.status, WorkSession.Status.ACTIVE)
        self.assertIsNone(ts_b.ended_at)

        # Step 6: Upload the 30 old buckets while WorkSession B is active
        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": offline_buckets,
        }
        resp = self.client.post(
            "/api/v1/desktop-tracker/sync/",
            data=json.dumps(payload),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        data = resp.json()
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(data["ingested_count"], 30)
        self.assertEqual(data["recovered_count"], 30)
        self.assertEqual(data["duplicate_count"], 0)

        # Step 7: Verify all 30 are associated with WorkSession A, NEVER WorkSession B
        self.assertEqual(DesktopActivityBucket.objects.filter(work_session=ws_a).count(), 30)
        self.assertEqual(DesktopActivityBucket.objects.filter(work_session=ws_b).count(), 0)

        # Step 8: Verify active/idle totals counted on ts_a, NEVER ts_b
        ts_a.refresh_from_db()
        ts_b.refresh_from_db()
        self.assertEqual(ts_a.total_active_seconds, initial_ts_a_active + (30 * 50))
        self.assertEqual(ts_a.total_idle_seconds, initial_ts_a_idle + (30 * 10))
        self.assertEqual(ts_b.total_active_seconds, 0)
        self.assertEqual(ts_b.total_idle_seconds, 0)

        # Verify application usage rows counted exactly once
        self.assertEqual(DesktopAppUsage.objects.filter(bucket__work_session=ws_a).count(), 30)
        self.assertEqual(DesktopAppUsage.objects.filter(bucket__work_session=ws_b).count(), 0)

        # Step 9: Submit the exact same 30 buckets again (retry duplicate)
        resp_retry = self.client.post(
            "/api/v1/desktop-tracker/sync/",
            data=json.dumps(payload),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp_retry.status_code, status.HTTP_200_OK)
        retry_data = resp_retry.json()
        self.assertEqual(retry_data["status"], "SUCCESS")
        self.assertEqual(retry_data["ingested_count"], 0)
        self.assertEqual(retry_data["duplicate_count"], 30)
        self.assertEqual(retry_data["recovered_count"], 0)

        # Step 10: Verify totals do not change
        ts_a.refresh_from_db()
        self.assertEqual(ts_a.total_active_seconds, initial_ts_a_active + (30 * 50))
        self.assertEqual(ts_a.total_idle_seconds, initial_ts_a_idle + (30 * 10))

        # Step 11: Verify historical timestamps were NOT modified
        ws_a.refresh_from_db()
        self.assertEqual(ws_a.started_at, ws_a_start)
        self.assertEqual(ws_a.ended_at, ws_a_end)
        att_a.refresh_from_db()
        self.assertEqual(att_a.checked_in, original_checked_in)
        self.assertEqual(att_a.checked_out, original_checked_out)

    def test_recovery_bucket_before_work_session_rejected(self):
        """Recovery bucket starting before completed shift start (beyond 60s) is rejected."""
        end_work_session(self.session.id, self.user, ended_at=self.now)
        out_of_bounds_start = self.session.started_at - timedelta(minutes=5)
        out_of_bounds_end = out_of_bounds_start + timedelta(minutes=1)

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "session_id": self.session.id,
                    "bucket_start": out_of_bounds_start.isoformat(),
                    "bucket_end": out_of_bounds_end.isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_SHIFT")
        self.assertIn("precedes completed shift start", resp.json()["message"])

    def test_recovery_bucket_after_work_session_ended_rejected(self):
        """Recovery bucket ending after completed shift ended_at (beyond 60s) is rejected."""
        end_work_session(self.session.id, self.user, ended_at=self.now)
        out_of_bounds_start = self.now + timedelta(minutes=5)
        out_of_bounds_end = out_of_bounds_start + timedelta(minutes=1)

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "session_id": self.session.id,
                    "bucket_start": out_of_bounds_start.isoformat(),
                    "bucket_end": out_of_bounds_end.isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_SHIFT")
        self.assertIn("exceeds completed shift end", resp.json()["message"])

    def test_recovery_bucket_wrong_employee_rejected(self):
        """Recovery bucket with session_id belonging to another user is rejected."""
        other_att = Attendance.objects.create(
            employee=self.other_user,
            date=self.today,
            status="PRESENT",
            checked_in=self.now - timedelta(hours=2),
        )
        other_session = start_work_session(
            user=self.other_user,
            attendance=other_att,
            category=self.category,
            started_at=self.now - timedelta(hours=2),
        )
        end_work_session(other_session.id, self.other_user, ended_at=self.now)

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "session_id": other_session.id,
                    "bucket_start": (self.now - timedelta(minutes=10)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=9)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_SHIFT")
        self.assertIn("does not belong to employee", resp.json()["message"])

    def test_recovery_bucket_wrong_device_rejected(self):
        """Payload device_uuid mismatch is rejected with HTTP 403."""
        end_work_session(self.session.id, self.user, ended_at=self.now)
        payload = {
            "device_uuid": "dev-other-device-uuid",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "session_id": self.session.id,
                    "bucket_start": (self.now - timedelta(minutes=10)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=9)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "DEVICE_MISMATCH")

    def test_recovery_bucket_revoked_device_rejected(self):
        """Recovery submission by a revoked device is rejected with HTTP 403."""
        end_work_session(self.session.id, self.user, ended_at=self.now)
        revoke_desktop_device(self.device, self.admin, "Revoked for security test")

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "session_id": self.session.id,
                    "bucket_start": (self.now - timedelta(minutes=10)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=9)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_recovery_bucket_employee_disabled_after_shift_rejected(self):
        """If employee desktop eligibility is disabled after shift completion, recovery is rejected."""
        end_work_session(self.session.id, self.user, ended_at=self.now)
        self.user.desktop_tracking_enabled = False
        self.user.save(update_fields=["desktop_tracking_enabled"])

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "session_id": self.session.id,
                    "bucket_start": (self.now - timedelta(minutes=10)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=9)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    def test_recovery_bucket_invalid_session_id_rejected(self):
        """Malformed or non-existent session_id is rejected with HTTP 400."""
        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "session_id": 999999,
                    "bucket_start": (self.now - timedelta(minutes=10)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=9)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                }
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_SHIFT")
        self.assertIn("not found", resp.json()["message"])

        payload["buckets"][0]["session_id"] = "not-a-number"
        resp2 = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp2.json()["error"], "INVALID_SHIFT")

    def test_recovery_mixed_valid_and_invalid_batch_all_or_nothing(self):
        """A recovery batch with 2 valid buckets and 1 invalid bucket rolls back completely."""
        end_work_session(self.session.id, self.user, ended_at=self.now)
        valid_id_1 = str(uuid.uuid4())
        valid_id_2 = str(uuid.uuid4())
        invalid_id = str(uuid.uuid4())

        payload = {
            "device_uuid": "dev-hardened-001",
            "buckets": [
                {
                    "bucket_id": valid_id_1,
                    "session_id": self.session.id,
                    "bucket_start": (self.now - timedelta(minutes=20)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=19)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                },
                {
                    "bucket_id": invalid_id,
                    "session_id": self.session.id,
                    "bucket_start": (self.now - timedelta(minutes=19)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=18)).isoformat(),
                    "active_seconds": 120,  # Invalid: active > duration
                    "idle_seconds": 0,
                },
                {
                    "bucket_id": valid_id_2,
                    "session_id": self.session.id,
                    "bucket_start": (self.now - timedelta(minutes=18)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=17)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                },
            ],
        }
        resp = self.client.post("/api/v1/desktop-tracker/sync/", data=json.dumps(payload), content_type="application/json", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_PAYLOAD")

        # Zero buckets persisted
        self.assertFalse(DesktopActivityBucket.objects.filter(bucket_id__in=[valid_id_1, invalid_id, valid_id_2]).exists())

    def test_logout_does_not_end_shift(self):
        """Logout must only clear authentication, never end the active WorkSession or DesktopTrackingSession."""
        ts = get_or_create_tracking_session(self.device, self.session)
        self.assertEqual(self.session.status, WorkSession.Status.ACTIVE)
        self.assertIsNone(ts.ended_at)

        # Perform web logout
        logout_resp = self.client.post("/logout/")
        self.assertIn(logout_resp.status_code, [200, 302])

        # WorkSession and DesktopTrackingSession MUST remain ACTIVE
        self.session.refresh_from_db()
        ts.refresh_from_db()
        self.assertEqual(self.session.status, WorkSession.Status.ACTIVE)
        self.assertIsNone(self.session.ended_at)
        self.assertIsNone(ts.ended_at)

    def test_device_revocation_does_not_end_shift(self):
        """Revoking a desktop device must not complete or close the employee's active shift."""
        ts = get_or_create_tracking_session(self.device, self.session)
        self.assertEqual(self.session.status, WorkSession.Status.ACTIVE)

        # Admin revokes device
        revoke_desktop_device(self.device, self.admin, "Security audit test revocation")
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_revoked)
        self.assertFalse(self.device.is_active)

        # WorkSession MUST remain ACTIVE
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WorkSession.Status.ACTIVE)
        self.assertIsNone(self.session.ended_at)


class DesktopTelemetryConcurrencyTests(TransactionTestCase):
    """PostgreSQL-backed concurrency tests verifying simultaneous duplicate batch submissions."""

    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.today = self.now.date()

        self.user = User.objects.create_user(
            username="concurrent_tracker_user",
            password="SecurePassword123!",
            role=User.Role.IT,
            desktop_tracking_enabled=True,
            shift_workflow_enabled=True,
        )
        self.category = ActivityCategory.objects.create(
            name="General Operations",
            slug="gen-ops-concurrent",
            is_active=True,
            is_default_general=True,
        )
        self.attendance = Attendance.objects.create(
            employee=self.user,
            date=self.today,
            status="PRESENT",
            checked_in=self.now - timedelta(hours=2),
        )
        self.session = start_work_session(
            user=self.user,
            attendance=self.attendance,
            category=self.category,
            started_at=self.now - timedelta(hours=2),
        )
        self.device, self.raw_token = register_or_pair_desktop_device(
            user=self.user,
            device_uuid="dev-concurrent-001",
            machine_name="CONCURRENT-WORKSTATION",
        )

    def test_simultaneous_duplicate_batch_submission_threads(self):
        """Two concurrent threads simultaneously submit the exact same activity bucket with a Barrier.
        
        Verifies:
        1. Both threads synchronize at barrier and execute concurrently against PostgreSQL.
        2. Both complete without unhandled IntegrityError or HTTP 500 crashes.
        3. Exactly 1 DesktopActivityBucket is persisted.
        4. Exactly 1 DesktopAppUsage is persisted.
        5. Session active/idle seconds are NOT double-counted.
        """
        shared_bucket_id = str(uuid.uuid4())
        bucket_data = [
            {
                "bucket_id": shared_bucket_id,
                "session_id": self.session.id,
                "bucket_start": (self.now - timedelta(minutes=5)).isoformat(),
                "bucket_end": (self.now - timedelta(minutes=4)).isoformat(),
                "active_seconds": 60,
                "idle_seconds": 0,
                "state": "ACTIVE",
                "app_usages": [
                    {"process_name": "vscode.exe", "duration_seconds": 60}
                ],
            }
        ]

        results = []
        errors = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                connection.close()
                barrier.wait(timeout=5)
                res = ingest_activity_buckets(self.device, self.user, bucket_data)
                results.append(res)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Zero unhandled exceptions
        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        self.assertEqual(len(results), 2)

        # Both returned SUCCESS
        self.assertTrue(all(r["status"] == "SUCCESS" for r in results))

        # Combined ingested_count across both threads must be exactly 1, and duplicate_count must be 1
        total_ingested = sum(r["ingested_count"] for r in results)
        total_duplicates = sum(r["duplicate_count"] for r in results)
        self.assertEqual(total_ingested, 1)
        self.assertEqual(total_duplicates, 1)

        # Exactly 1 row in DesktopActivityBucket in PostgreSQL
        self.assertEqual(DesktopActivityBucket.objects.filter(bucket_id=shared_bucket_id).count(), 1)
        b = DesktopActivityBucket.objects.get(bucket_id=shared_bucket_id)
        self.assertEqual(DesktopAppUsage.objects.filter(bucket=b).count(), 1)

        # Totals in tracking session must be exactly 60, not 120
        ts = DesktopTrackingSession.objects.get(work_session=self.session)
        self.assertEqual(ts.total_active_seconds, 60)
        self.assertEqual(ts.total_idle_seconds, 0)

    def test_concurrent_duplicate_recovery_submissions_threads(self):
        """Two concurrent threads simultaneously submit the exact same recovery bucket for a completed shift.
        
        Verifies:
        1. Both threads synchronize at barrier and execute concurrently against PostgreSQL.
        2. Both complete without unhandled IntegrityError or HTTP 500 crashes.
        3. Exactly 1 recovery DesktopActivityBucket is persisted.
        4. Historical tracking session active/idle seconds are NOT double-counted.
        """
        end_work_session(self.session.id, self.user, ended_at=self.now)
        shared_bucket_id = str(uuid.uuid4())
        bucket_data = [
            {
                "bucket_id": shared_bucket_id,
                "session_id": self.session.id,
                "bucket_start": (self.now - timedelta(minutes=30)).isoformat(),
                "bucket_end": (self.now - timedelta(minutes=29)).isoformat(),
                "active_seconds": 60,
                "idle_seconds": 0,
                "state": "ACTIVE",
                "app_usages": [
                    {"process_name": "photoshop.exe", "duration_seconds": 60}
                ],
            }
        ]

        results = []
        errors = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                connection.close()
                barrier.wait(timeout=5)
                res = ingest_activity_buckets(self.device, self.user, bucket_data)
                results.append(res)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Zero unhandled exceptions
        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["status"] == "SUCCESS" for r in results))

        total_ingested = sum(r["ingested_count"] for r in results)
        total_duplicates = sum(r["duplicate_count"] for r in results)
        self.assertEqual(total_ingested, 1)
        self.assertEqual(total_duplicates, 1)

        self.assertEqual(DesktopActivityBucket.objects.filter(bucket_id=shared_bucket_id).count(), 1)
        b = DesktopActivityBucket.objects.get(bucket_id=shared_bucket_id)
        self.assertEqual(b.work_session, self.session)
        self.assertEqual(DesktopAppUsage.objects.filter(bucket=b).count(), 1)

        ts = DesktopTrackingSession.objects.filter(work_session=self.session).first()
        self.assertEqual(ts.total_active_seconds, 60)
        self.assertEqual(ts.total_idle_seconds, 0)
