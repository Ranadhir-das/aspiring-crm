from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from apps.accounts.models import User, CallerSession
from apps.accounts.api.session_service import close_session


class Command(BaseCommand):
    help = 'Close expired mobile sessions. Run every minute with your scheduler.'

    def handle(self, *args, **options):
        now = timezone.now()
        count = 0
        ids = CallerSession.objects.filter(logged_out_at__isnull=True, expires_at__lte=now).values_list('caller_id', flat=True).distinct()
        for user_id in list(ids):
            with transaction.atomic():
                User.objects.select_for_update().get(pk=user_id)
                for session in CallerSession.objects.select_for_update().filter(caller_id=user_id, logged_out_at__isnull=True, expires_at__lte=now):
                    close_session(session, now, 'EXPIRED')
                    count += 1
        self.stdout.write(f'Closed {count} expired sessions.')
