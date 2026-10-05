from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.chat.models import ChatMessage
from apps.web.models import Notice
from .team_events import notify_team_chat, notify_notice


@receiver(post_save, sender=ChatMessage, dispatch_uid='notification_new_team_chat')
def new_team_chat(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        transaction.on_commit(lambda pk=instance.pk: notify_team_chat(pk), robust=True)


@receiver(post_save, sender=Notice, dispatch_uid='notification_new_notice')
def new_notice(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        transaction.on_commit(lambda pk=instance.pk: notify_notice(pk), robust=True)
