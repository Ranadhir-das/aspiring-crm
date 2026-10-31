with open('apps/activity/api/views.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_start = """        # Retrieve today's attendance record if one exists, to link it
        attendance = Attendance.objects.filter(employee=request.user, date=timezone.localdate(now)).first()

        session = start_work_session(
            user=request.user,
            attendance=attendance,
            category=category,
            started_at=now,
            note=data.get("note", ""),
        )"""

new_start = """        # Retrieve or create today's attendance record with real shift start time
        attendance, _ = Attendance.objects.get_or_create(
            employee=request.user,
            date=timezone.localdate(now),
            defaults={"checked_in": now, "status": "PRESENT", "login_remark": data.get("note", "")},
        )
        if attendance.checked_in is None:
            attendance.checked_in = now
            attendance.save(update_fields=["checked_in"])

        # Link open CallerSession if unlinked
        from apps.accounts.models import CallerSession
        CallerSession.objects.filter(caller=request.user, logged_out_at__isnull=True, attendance__isnull=True).update(attendance=attendance)

        session = start_work_session(
            user=request.user,
            attendance=attendance,
            category=category,
            started_at=now,
            note=data.get("note", ""),
        )"""

assert old_start in content, "old_start not found in apps/activity/api/views.py"
content = content.replace(old_start, new_start, 1)

old_end = """        closed_session = end_work_session(
            session_id=session.id,
            user=request.user,
            ended_at=now,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            closed_by=request.user,
            admin_note=data.get("note", ""),
        )"""

new_end = """        closed_session = end_work_session(
            session_id=session.id,
            user=request.user,
            ended_at=now,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            closed_by=request.user,
            admin_note=data.get("note", ""),
        )

        # Update checked_out on attendance record
        att_to_update = closed_session.attendance or Attendance.objects.filter(employee=request.user, date=closed_session.date).first()
        if att_to_update:
            att_to_update.checked_out = closed_session.ended_at
            att_to_update.save(update_fields=["checked_out"])"""

assert old_end in content, "old_end not found in apps/activity/api/views.py"
content = content.replace(old_end, new_end, 1)

with open('apps/activity/api/views.py', 'w', encoding='utf-8', newline='') as f:
    f.write(content)

print("Successfully updated apps/activity/api/views.py for StartActivityView and EndActivityView")

