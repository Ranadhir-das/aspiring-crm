# Generated manually for Feature 3: 7h 45m Work Report Reminder Notification

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0006_alter_notification_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='type',
            field=models.CharField(
                choices=[
                    ('LEAD_ASSIGNED', 'Lead assigned'),
                    ('TEAM_CHAT', 'Team chat message'),
                    ('NOTICE', 'Notice published'),
                    ('FOLLOWUP_DUE', 'Follow-up due'),
                    ('COUNSELOR_LEAD_FORWARDED', 'Lead forwarded to counselor'),
                    ('ADMISSION_REQUESTED', 'Admission requested'),
                    ('ATTENDANCE_REMINDER', 'Attendance reminder'),
                    ('LEAVE_REQUEST', 'Leave request'),
                    ('PENDING_LEADS', 'Pending leads alert'),
                    ('ATTENDANCE_CORRECTION', 'Attendance correction request'),
                    ('WORK_REPORT_REMINDER', 'Work report reminder'),
                ],
                max_length=40,
            ),
        ),
    ]
