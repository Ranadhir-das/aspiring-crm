from django.core.management.base import BaseCommand
from apps.followups.notifications import check_and_notify_due_followups


class Command(BaseCommand):
    help = "Check and dispatch notification alerts for due follow-ups"

    def handle(self, *args, **options):
        count = check_and_notify_due_followups()
        self.stdout.write(self.style.SUCCESS(f"Dispatched alerts for {count} due follow-up(s)."))
