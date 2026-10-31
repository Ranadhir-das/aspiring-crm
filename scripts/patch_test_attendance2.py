from pathlib import Path

path = Path("E:/react/aspiring-crm/apps/web/test_attendance_redesign.py")
content = path.read_text(encoding="utf-8")

old_break = """        ActivityBreak.objects.create(
            session=ws,
            reason_config=self.break_reason,
            warning_threshold_minutes_snapshot=self.break_reason.warning_threshold_minutes,
            started_at=start + timedelta(hours=3),
            ended_at=start + timedelta(hours=4),
        )"""

new_break = """        ActivityBreak.objects.create(
            session=ws,
            reason_config=self.break_reason,
            warning_threshold_minutes_snapshot=self.break_reason.warning_threshold_minutes,
            started_at=start + timedelta(hours=3),
            ended_at=start + timedelta(hours=4),
            closure_reason=ActivityBreak.ClosureReason.MANUAL,
        )"""

content = content.replace(old_break.replace('\r\n', '\n'), new_break.replace('\r\n', '\n'))
content = content.replace(old_break, new_break)

path.write_text(content, encoding="utf-8")
print("Successfully patched ActivityBreak closure_reason in test_attendance_redesign.py")

