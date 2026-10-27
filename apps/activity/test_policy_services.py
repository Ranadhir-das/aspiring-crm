"""
Unit and integration tests for Vaani office geofencing, GPS quality/freshness,
authoritative net duration, and rule-specific policy exceptions.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import math

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    EmployeeOfficeAssignment,
    OfficeLocation,
    PolicyException,
    WorkActivitySegment,
    WorkSession,
)
from apps.activity.policy_services import (
    EARTH_RADIUS_METERS,
    MAX_GPS_ACCURACY_METERS,
    MAX_GPS_AGE_SECONDS,
    MIN_NET_WORK_SECONDS,
    STRICT_GEOFENCE_RADIUS_METERS,
    GeofenceViolationError,
    GpsFreshnessError,
    GpsMockError,
    GpsQualityError,
    InsufficientWorkDurationError,
    ProtectedAction,
    calculate_haversine_distance_meters,
    calculate_persisted_net_work_seconds,
    check_office_geofence,
    evaluate_protected_action,
    get_active_policy_exception,
    get_employee_authorized_offices,
    parse_and_validate_gps_reading,
)


class GpsQualityAndFreshnessTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_valid_coordinates_and_quality(self):
        reading = parse_and_validate_gps_reading(
            latitude=22.5726,
            longitude=88.3639,
            accuracy=12.5,
            recorded_at=self.now,
            is_mocked=False,
            now=self.now,
        )
        self.assertAlmostEqual(reading.latitude, 22.5726)
        self.assertAlmostEqual(reading.longitude, 88.3639)
        self.assertEqual(reading.accuracy, 12.5)
        self.assertFalse(reading.is_mocked)

    def test_boundary_coordinates(self):
        # Exact edge coordinates [-90, 90] and [-180, 180]
        r1 = parse_and_validate_gps_reading(90.0, 180.0, 10.0, self.now, now=self.now)
        self.assertEqual(r1.latitude, 90.0)
        self.assertEqual(r1.longitude, 180.0)

        r2 = parse_and_validate_gps_reading(-90.0, -180.0, 10.0, self.now, now=self.now)
        self.assertEqual(r2.latitude, -90.0)
        self.assertEqual(r2.longitude, -180.0)

    def test_out_of_range_coordinates_rejected(self):
        with self.assertRaises(GpsQualityError):
            parse_and_validate_gps_reading(90.1, 88.0, 10.0, self.now, now=self.now)

        with self.assertRaises(GpsQualityError):
            parse_and_validate_gps_reading(22.0, 180.1, 10.0, self.now, now=self.now)

        with self.assertRaises(GpsQualityError):
            parse_and_validate_gps_reading("not-a-number", 88.0, 10.0, self.now, now=self.now)

    def test_accuracy_threshold_boundary(self):
        # Exactly 25.0 metres is allowed
        reading = parse_and_validate_gps_reading(22.0, 88.0, 25.0, self.now, now=self.now)
        self.assertEqual(reading.accuracy, 25.0)

        # Worse than 25.0 metres must be rejected
        with self.assertRaises(GpsQualityError) as ctx:
            parse_and_validate_gps_reading(22.0, 88.0, 25.1, self.now, now=self.now)
        self.assertIn("accuracy is insufficient", str(ctx.exception))

        # Negative accuracy rejected
        with self.assertRaises(GpsQualityError):
            parse_and_validate_gps_reading(22.0, 88.0, -1.0, self.now, now=self.now)

    def test_reading_age_freshness_boundary(self):
        # Exactly 60.0 seconds old is allowed
        t_60 = self.now - timedelta(seconds=60)
        reading = parse_and_validate_gps_reading(22.0, 88.0, 10.0, t_60, now=self.now)
        self.assertEqual(reading.recorded_at, t_60)

        # 60.5 seconds old is stale and rejected
        t_stale = self.now - timedelta(seconds=60.5)
        with self.assertRaises(GpsFreshnessError) as ctx:
            parse_and_validate_gps_reading(22.0, 88.0, 10.0, t_stale, now=self.now)
        self.assertIn("stale", str(ctx.exception).lower())

        # Future timestamp (> 5s ahead) rejected
        t_future = self.now + timedelta(seconds=10)
        with self.assertRaises(GpsFreshnessError) as ctx:
            parse_and_validate_gps_reading(22.0, 88.0, 10.0, t_future, now=self.now)
        self.assertIn("future", str(ctx.exception).lower())

    def test_mock_location_fails_closed(self):
        with self.assertRaises(GpsMockError):
            parse_and_validate_gps_reading(22.0, 88.0, 10.0, self.now, is_mocked=True, now=self.now)


class OfficeLocationAndAssignmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("emp_geo_test", role=User.Role.CALLER)
        self.office_kol = OfficeLocation.objects.create(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude=Decimal("22.572646"),
            longitude=Decimal("88.363895"),
            radius_meters=50,
            is_active=True,
        )
        self.office_sil = OfficeLocation.objects.create(
            name="Siliguri Branch",
            code="SIL_BR",
            latitude=Decimal("26.727101"),
            longitude=Decimal("88.395286"),
            radius_meters=50,
            is_active=True,
        )

    def test_office_radius_strictly_50m_enforced(self):
        # Direct attempt to violate 50m radius fails validation
        office_bad = OfficeLocation(
            name="Bad Radius Office",
            code="BAD_RAD",
            latitude=Decimal("22.0"),
            longitude=Decimal("88.0"),
            radius_meters=100,
        )
        with self.assertRaises(ValidationError):
            office_bad.full_clean()

    def test_primary_and_additional_assignments(self):
        a1 = EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office_kol,
            is_primary=True,
            is_active=True,
        )
        a2 = EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office_sil,
            is_primary=False,
            is_active=True,
        )

        offices = get_employee_authorized_offices(self.user, only_active=True)
        self.assertEqual(len(offices), 2)
        # Primary is listed first
        self.assertEqual(offices[0].pk, self.office_kol.pk)
        self.assertEqual(offices[1].pk, self.office_sil.pk)

    def test_duplicate_primary_office_rejected(self):
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office_kol,
            is_primary=True,
            is_active=True,
        )
        a2 = EmployeeOfficeAssignment(
            user=self.user,
            office=self.office_sil,
            is_primary=True,
            is_active=True,
        )
        with self.assertRaises(ValidationError):
            a2.clean()

    def test_duplicate_assignment_rejected(self):
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office_kol,
            is_primary=True,
            is_active=True,
        )
        with self.assertRaises((ValidationError, IntegrityError)):
            EmployeeOfficeAssignment.objects.create(
                user=self.user,
                office=self.office_kol,
                is_primary=False,
                is_active=True,
            )

    def test_inactive_office_or_assignment_excluded(self):
        # Inactive assignment
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office_kol,
            is_primary=True,
            is_active=False,
        )
        # Active assignment to inactive office
        self.office_sil.is_active = False
        self.office_sil.save()
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office_sil,
            is_primary=False,
            is_active=True,
        )

        offices = get_employee_authorized_offices(self.user, only_active=True)
        self.assertEqual(len(offices), 0)


class GeofenceDistanceCalculationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("emp_dist_test", role=User.Role.CALLER)
        # Kolkata HQ reference: 22.572646, 88.363895
        self.office = OfficeLocation.objects.create(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude=Decimal("22.572646"),
            longitude=Decimal("88.363895"),
            radius_meters=50,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office,
            is_primary=True,
            is_active=True,
        )

    def test_haversine_exact_calculation(self):
        # 1 degree of latitude is roughly 111.19 km
        d = calculate_haversine_distance_meters(0.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(d, 111194.9, delta=100.0)

        # Distance to self is 0
        d_self = calculate_haversine_distance_meters(22.572646, 88.363895, 22.572646, 88.363895)
        self.assertEqual(d_self, 0.0)

    def test_geofence_boundary_50_metres(self):
        # Offset coordinates approximately:
        # At latitude 22.57 deg, 1 second of lat is ~30.8 meters.
        # ~0.00036 deg lat ~= 40 metres north (Inside 50m)
        lat_inside = 22.572646 + 0.00036
        lon_inside = 88.363895
        res_inside = check_office_geofence(self.user, lat_inside, lon_inside, radius_meters=50.0)
        self.assertTrue(res_inside.is_within)
        self.assertLessEqual(res_inside.distance_meters, 50.0)

        # ~0.00072 deg lat ~= 80 metres north (Outside 50m)
        lat_outside = 22.572646 + 0.00072
        lon_outside = 88.363895
        res_outside = check_office_geofence(self.user, lat_outside, lon_outside, radius_meters=50.0)
        self.assertFalse(res_outside.is_within)
        self.assertGreater(res_outside.distance_meters, 50.0)

    def test_secondary_authorized_office_satisfies_geofence(self):
        office2 = OfficeLocation.objects.create(
            name="Salt Lake Annex",
            code="SL_ANNEX",
            latitude=Decimal("22.580000"),
            longitude=Decimal("88.420000"),
            radius_meters=50,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=office2,
            is_primary=False,
            is_active=True,
        )

        # Employee is 10m from office2, far from office1
        res = check_office_geofence(self.user, 22.58008, 88.42000, radius_meters=50.0)
        self.assertTrue(res.is_within)
        self.assertEqual(res.nearest_office.pk, office2.pk)


class PolicyExceptionTests(TestCase):
    def setUp(self):
        self.employee = User.objects.create_user("emp_exc_test", role=User.Role.CALLER)
        self.manager = User.objects.create_user("mgr_exc_test", role=User.Role.MANAGER)
        self.admin = User.objects.create_user("adm_exc_test", role=User.Role.ADMIN)
        self.now = timezone.now()

    def test_manager_cannot_approve_exception(self):
        exc = PolicyException(
            user=self.employee,
            rule_type=PolicyException.RuleType.LOGIN_GEOFENCE,
            reason="WFH requested due to fever",
            requested_by=self.manager,
            approved_by=self.manager,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now,
            valid_until=self.now + timedelta(hours=8),
        )
        with self.assertRaises(ValidationError) as ctx:
            exc.clean()
        self.assertIn("Only administrators", str(ctx.exception))

    def test_admin_approves_exception(self):
        exc = PolicyException.objects.create(
            user=self.employee,
            rule_type=PolicyException.RuleType.LOGIN_GEOFENCE,
            reason="Approved client outreach day",
            requested_by=self.manager,
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=7),
            is_active=True,
        )
        self.assertTrue(exc.is_valid_at(self.now))

        active = get_active_policy_exception(self.employee, PolicyException.RuleType.LOGIN_GEOFENCE, at_time=self.now)
        self.assertIsNotNone(active)
        self.assertEqual(active.pk, exc.pk)

    def test_expired_exception_rejected(self):
        PolicyException.objects.create(
            user=self.employee,
            rule_type=PolicyException.RuleType.LOGIN_GEOFENCE,
            reason="Past exception",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now - timedelta(hours=10),
            valid_from=self.now - timedelta(hours=9),
            valid_until=self.now - timedelta(hours=1),
            is_active=True,
        )
        active = get_active_policy_exception(self.employee, PolicyException.RuleType.LOGIN_GEOFENCE, at_time=self.now)
        self.assertIsNone(active)

    def test_revoked_exception_rejected(self):
        exc = PolicyException.objects.create(
            user=self.employee,
            rule_type=PolicyException.RuleType.LOGIN_GEOFENCE,
            reason="Revoked approval",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=8),
            is_active=False,
            revoked_at=self.now,
            revoked_by=self.admin,
            revocation_reason="Returned to office early",
        )
        active = get_active_policy_exception(self.employee, PolicyException.RuleType.LOGIN_GEOFENCE, at_time=self.now)
        self.assertIsNone(active)

    def test_rule_specific_scope_isolation(self):
        # Exception for LOGIN_GEOFENCE must not bypass MIN_DURATION or END_SHIFT_GEOFENCE
        PolicyException.objects.create(
            user=self.employee,
            rule_type=PolicyException.RuleType.LOGIN_GEOFENCE,
            reason="Morning client visit",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=8),
            is_active=True,
        )

        login_exc = get_active_policy_exception(self.employee, PolicyException.RuleType.LOGIN_GEOFENCE, self.now)
        self.assertIsNotNone(login_exc)

        duration_exc = get_active_policy_exception(self.employee, PolicyException.RuleType.MIN_DURATION, self.now)
        self.assertIsNone(duration_exc)

        end_geo_exc = get_active_policy_exception(self.employee, PolicyException.RuleType.END_SHIFT_GEOFENCE, self.now)
        self.assertIsNone(end_geo_exc)


class PersistedNetWorkDurationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("emp_dur_test", role=User.Role.CALLER)
        self.category = ActivityCategory.objects.create(
            name="General Calling",
            slug="general-calling",
            is_default_for_caller=True,
            is_active=True,
        )
        self.break_reason = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=45,
            requires_geofence_resume=True,
            is_active=True,
        )
        self.now = timezone.now()

    def test_net_work_calculation_without_breaks(self):
        session = WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.COMPLETED,
            started_at=self.now - timedelta(hours=8),
            ended_at=self.now,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=session.started_at,
            ended_at=session.ended_at,
        )
        net_secs = calculate_persisted_net_work_seconds(session, now=self.now)
        self.assertEqual(net_secs, 8 * 3600)

    def test_net_work_deducts_breaks_authoritatively(self):
        # 9 hours gross - 1.5 hours break = 7.5 hours net
        t_start = self.now - timedelta(hours=9)
        session = WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.COMPLETED,
            started_at=t_start,
            ended_at=self.now,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        # Segment 1: 4 hours
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=t_start,
            ended_at=t_start + timedelta(hours=4),
        )
        # Break: 1.5 hours
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_reason,
            started_at=t_start + timedelta(hours=4),
            ended_at=t_start + timedelta(hours=5, minutes=30),
            warning_threshold_minutes_snapshot=45,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
        )
        # Segment 2: 3.5 hours
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=t_start + timedelta(hours=5, minutes=30),
            ended_at=self.now,
        )

        net_secs = calculate_persisted_net_work_seconds(session, now=self.now)
        expected = int(7.5 * 3600)  # 27,000s
        self.assertEqual(net_secs, expected)


class ProtectedActionUnifiedEvaluationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("emp_prot_test", role=User.Role.CALLER)
        self.admin = User.objects.create_user("adm_prot_test", role=User.Role.ADMIN)
        self.office = OfficeLocation.objects.create(
            name="Kolkata Office",
            code="KOL_OFFICE",
            latitude=Decimal("22.572646"),
            longitude=Decimal("88.363895"),
            radius_meters=50,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office,
            is_primary=True,
            is_active=True,
        )
        self.category = ActivityCategory.objects.create(
            name="Counseling",
            slug="counseling",
            is_default_for_caller=True,
            is_active=True,
        )
        self.now = timezone.now()

    def test_login_outside_geofence_rejected(self):
        gps = parse_and_validate_gps_reading(
            latitude=22.6000,  # ~3km away
            longitude=88.3639,
            accuracy=10.0,
            recorded_at=self.now,
            now=self.now,
        )
        res = evaluate_protected_action(self.user, ProtectedAction.LOGIN, gps_reading=gps, now=self.now)
        self.assertFalse(res.permitted)
        self.assertEqual(res.error_code, "GEOFENCE_VIOLATION")

    def test_login_with_active_exception_permitted(self):
        PolicyException.objects.create(
            user=self.user,
            rule_type=PolicyException.RuleType.LOGIN_GEOFENCE,
            reason="WFH day approved",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=8),
            is_active=True,
        )
        # Even without GPS reading, exception permits login
        res = evaluate_protected_action(self.user, ProtectedAction.LOGIN, gps_reading=None, now=self.now)
        self.assertTrue(res.permitted)
        self.assertTrue(res.bypassed_by_exception)

    def test_end_shift_under_8_hours_blocked(self):
        gps_inside = parse_and_validate_gps_reading(
            latitude=22.572646,
            longitude=88.363895,
            accuracy=10.0,
            recorded_at=self.now,
            now=self.now,
        )
        # 6-hour session
        session = WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=6),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=session.started_at,
        )

        res = evaluate_protected_action(
            self.user,
            ProtectedAction.END_SHIFT,
            gps_reading=gps_inside,
            session=session,
            now=self.now,
        )
        self.assertFalse(res.permitted)
        self.assertEqual(res.error_code, "INSUFFICIENT_WORK_DURATION")
        self.assertEqual(res.net_work_seconds, 6 * 3600)

    def test_end_shift_under_8_hours_with_min_duration_exception_permitted(self):
        gps_inside = parse_and_validate_gps_reading(
            latitude=22.572646,
            longitude=88.363895,
            accuracy=10.0,
            recorded_at=self.now,
            now=self.now,
        )
        session = WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=5),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=session.started_at,
        )

        PolicyException.objects.create(
            user=self.user,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            reason="Medical emergency approved early leave",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=2),
            is_active=True,
        )

        res = evaluate_protected_action(
            self.user,
            ProtectedAction.END_SHIFT,
            gps_reading=gps_inside,
            session=session,
            now=self.now,
        )
        self.assertTrue(res.permitted)

    def test_end_shift_satisfies_both_geofence_and_8_hours(self):
        gps_inside = parse_and_validate_gps_reading(
            latitude=22.572646,
            longitude=88.363895,
            accuracy=10.0,
            recorded_at=self.now,
            now=self.now,
        )
        # 8 hours 10 minutes net work
        session = WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=8, minutes=10),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=session.started_at,
        )

        res = evaluate_protected_action(
            self.user,
            ProtectedAction.END_SHIFT,
            gps_reading=gps_inside,
            session=session,
            now=self.now,
        )
        self.assertTrue(res.permitted)
        self.assertFalse(res.bypassed_by_exception)
        self.assertGreaterEqual(res.net_work_seconds, 28800)
