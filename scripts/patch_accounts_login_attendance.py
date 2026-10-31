with open('apps/accounts/api/views.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_block = """        close_open_sessions(user, now)
        if verification or face_exempt:
            attendance, _ = Attendance.objects.get_or_create(employee=user, date=timezone.localdate(), defaults={'checked_in': now, 'login_remark': login_remark})
            if attendance.checked_in is None:
                attendance.checked_in = now
            attendance.checked_out = None
            if login_remark and not attendance.login_remark:
                attendance.login_remark = login_remark
            attendance.save()
        Token.objects.filter(user=user).delete()"""

new_block = """        close_open_sessions(user, now)
        attendance = None
        if verification or face_exempt:
            # Login is authentication only for opted-in shift employees.
            # Legacy employees retain automatic attendance check-in on login.
            if not getattr(user, 'shift_workflow_enabled', False):
                attendance, _ = Attendance.objects.get_or_create(
                    employee=user, date=timezone.localdate(), defaults={'checked_in': now, 'login_remark': login_remark}
                )
                if attendance.checked_in is None:
                    attendance.checked_in = now
                attendance.checked_out = None
                if login_remark and not attendance.login_remark:
                    attendance.login_remark = login_remark
                attendance.save()
        Token.objects.filter(user=user).delete()"""

assert old_block in content, "old_block not found in apps/accounts/api/views.py"
content = content.replace(old_block, new_block, 1)

with open('apps/accounts/api/views.py', 'w', encoding='utf-8', newline='') as f:
    f.write(content)

print("Successfully updated apps/accounts/api/views.py")

