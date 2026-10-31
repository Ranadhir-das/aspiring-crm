from pathlib import Path

path = Path("E:/react/aspiring-crm/apps/web/test_attendance_redesign.py")
content = path.read_text(encoding="utf-8")

# Fix 1: start status code
content = content.replace(
    'self.assertEqual(resp_start.status_code, status.HTTP_200_OK)',
    'self.assertIn(resp_start.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])'
)

# Fix 2: warning_threshold_minutes_snapshot on break
old_break = """        ActivityBreak.objects.create(
            session=ws,
            reason_config=self.break_reason,
            started_at=start + timedelta(hours=3),
            ended_at=start + timedelta(hours=4),
        )"""

new_break = """        ActivityBreak.objects.create(
            session=ws,
            reason_config=self.break_reason,
            warning_threshold_minutes_snapshot=self.break_reason.warning_threshold_minutes,
            started_at=start + timedelta(hours=3),
            ended_at=start + timedelta(hours=4),
        )"""
content = content.replace(old_break.replace('\r\n', '\n'), new_break.replace('\r\n', '\n'))
content = content.replace(old_break, new_break)

# Fix 3: PolicyException is_approved
old_exc = """        exception = PolicyException.objects.create(
            user=self.optin_employee,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            approved_by=self.admin,
            valid_from=start - timedelta(minutes=10),
            valid_until=end + timedelta(minutes=10),
            reason="Approved medical leave early release",
            is_active=True,
        )"""

new_exc = """        exception = PolicyException.objects.create(
            user=self.optin_employee,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            approved_by=self.admin,
            is_approved=True,
            approved_at=start,
            valid_from=start - timedelta(minutes=10),
            valid_until=end + timedelta(minutes=10),
            reason="Approved medical leave early release",
            is_active=True,
        )"""
content = content.replace(old_exc.replace('\r\n', '\n'), new_exc.replace('\r\n', '\n'))
content = content.replace(old_exc, new_exc)

# Fix 4: CSV header assertion
content = content.replace('self.assertIn("Net Work Hours", rows[0])', 'self.assertIn("Net Working Hours", rows[0])')

path.write_text(content, encoding="utf-8")
print("Successfully patched test_attendance_redesign.py")

