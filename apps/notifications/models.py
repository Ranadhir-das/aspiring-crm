from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Type(models.TextChoices):
        LEAD_ASSIGNED = 'LEAD_ASSIGNED', 'Lead assigned'
        TEAM_CHAT = 'TEAM_CHAT', 'Team chat message'
        NOTICE = 'NOTICE', 'Notice published'
        FOLLOWUP_DUE = 'FOLLOWUP_DUE', 'Follow-up due'

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='notifications')
    type = models.CharField(max_length=40, choices=Type.choices)
    title = models.CharField(max_length=255)
    body = models.TextField()
    data = models.JSONField(default=dict)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['recipient', 'is_read'], name='notification_owner_unread'),
            models.Index(fields=['recipient', '-created_at', '-id'], name='notification_owner_recent'),
        ]

    def __str__(self):
        return f'{self.type} #{self.pk}'
