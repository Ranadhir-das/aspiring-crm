from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL


def sender_name(user):
    return user.get_full_name() or user.username


class ChatChannel(models.Model):
    class Kind(models.TextChoices):
        GENERAL = 'GENERAL', 'General'
        GROUP = 'GROUP', 'Group'

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.GROUP)
    description = models.CharField(max_length=255, blank=True)
    # Membership only matters for GROUP channels — GENERAL is implicitly everyone
    # (see ChatChannel.visible_to) so it never needs syncing when employees join.
    members = models.ManyToManyField(USER, related_name='chat_channels', blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @staticmethod
    def visible_to(user):
        return ChatChannel.objects.filter(
            models.Q(kind=ChatChannel.Kind.GENERAL) | models.Q(members=user)
        ).distinct()

    def is_member(self, user):
        return self.kind == self.Kind.GENERAL or self.members.filter(pk=user.pk).exists()


class ChatMessage(models.Model):
    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='chat_messages')
    text = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def as_payload(self):
        return {
            'id': self.pk,
            'channel': self.channel_id,
            'sender_id': self.sender_id,
            'sender_name': sender_name(self.sender),
            'text': self.text,
            'created_at': self.created_at.isoformat(),
        }
