"""
Management command to idempotently close overdue active and paused WorkSessions
at their respective Asia/Kolkata midnight boundaries.
"""
from django.core.management.base import BaseCommand, CommandError
from apps.activity.services import recover_overdue_sessions


class Command(BaseCommand):
    help = (
        "Idempotently closes overdue active/paused work sessions at their respective "
        "Asia/Kolkata local midnight boundaries. Safe to rerun and resilient against "
        "multi-day scheduler outages."
    )

    def handle(self, *args, **options):
        self.stdout.write("Running midnight session recovery...")
        summary = recover_overdue_sessions()

        self.stdout.write(
            f"Candidate session(s) checked: {summary['checked']}\n"
            f"Overdue session(s) identified: {summary['overdue']}\n"
            f"Successfully closed: {summary['closed']}\n"
            f"Skipped / unchanged: {summary['skipped']}"
        )

        if summary["errors"]:
            self.stderr.write(self.style.ERROR(f"Encountered {len(summary['errors'])} error(s) during recovery:"))
            for err in summary["errors"]:
                self.stderr.write(self.style.ERROR(f"  - {err}"))
            raise CommandError(f"Midnight session recovery completed with {len(summary['errors'])} anomaly error(s).")

        self.stdout.write(self.style.SUCCESS("Midnight recovery completed with zero errors."))
