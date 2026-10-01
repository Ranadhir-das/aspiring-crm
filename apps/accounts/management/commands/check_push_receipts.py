from django.core.management.base import BaseCommand
from apps.accounts.push_notifications import check_push_receipts


class Command(BaseCommand):
    help = 'Check Expo receipts and deactivate unregistered push devices (schedule every 15 minutes).'

    def handle(self, *args, **options):
        self.stdout.write(f'Checked {check_push_receipts()} push receipts.')
