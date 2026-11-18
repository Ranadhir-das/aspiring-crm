"""
Management command to dispatch attendance reminder notifications.

Schedules:
- 09:30 AM Asia/Kolkata: Shift login reminder for scheduled employees who haven't started.
- 06:30 PM Asia/Kolkata: Shift logout reminder for employees with open unended shifts.

Supports manual dispatch, automated cron/scheduler dispatch, and dry-run preview.
"""
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from apps.notifications.attendance_reminders import (
    REMINDER_LOGIN,
    REMINDER_LOGOUT,
    send_attendance_reminders,
)


class Command(BaseCommand):
    help = (
        "Idempotently dispatches employee shift login and logout reminder notifications "
        "based on current Asia/Kolkata shift states, weekly off, holidays, and leaves."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--type",
            dest="reminder_type",
            type=str,
            default="AUTO",
            choices=["AUTO", "LOGIN", "LOGOUT"],
            help="Type of reminder to dispatch: LOGIN (shift start), LOGOUT (shift end), or AUTO (inferred from time).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Simulate execution and display eligible recipients without persisting notifications.",
        )
        parser.add_argument(
            "--now",
            dest="now_override",
            type=str,
            default=None,
            help="Optional ISO datetime override (e.g. '2026-11-03T09:30:00+05:30') for evaluation.",
        )
        parser.add_argument(
            "--user-id",
            dest="user_id",
            type=int,
            default=None,
            help="Optional user ID filter to target a specific employee.",
        )

    def handle(self, *args, **options):
        reminder_type = options["reminder_type"]
        dry_run = options["dry_run"]
        now_str = options.get("now_override")
        user_id = options.get("user_id")

        now_dt = None
        if now_str:
            try:
                now_dt = datetime.fromisoformat(now_str)
            except ValueError as e:
                raise CommandError(f"Invalid ISO format for --now ('{now_str}'): {e}")

        prefix = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(f"{prefix}Evaluating attendance reminders (mode: {reminder_type})...")

        try:
            summary = send_attendance_reminders(
                reminder_type=reminder_type,
                now=now_dt,
                dry_run=dry_run,
                user_id=user_id,
            )
        except Exception as exc:
            raise CommandError(f"Failed to execute attendance reminders: {exc}") from exc

        self.stdout.write(
            f"{prefix}Attendance Reminders Execution Summary:\n"
            f"  - Status: {summary['status']}\n"
            f"  - Resolved Type: {summary['reminder_type']}\n"
            f"  - Target Date: {summary['target_date']}\n"
            f"  - Local Time: {summary['local_time']}\n"
            f"  - Eligible Evaluated: {summary['eligible_checked']}\n"
            f"  - Dispatched: {summary['sent_count']}\n"
            f"  - Skipped (Week Off): {summary['skipped_week_off']}\n"
            f"  - Skipped (Holiday): {summary['skipped_holiday']}\n"
            f"  - Skipped (Leave): {summary['skipped_leave']}\n"
            f"  - Skipped (Already Started): {summary['skipped_already_started']}\n"
            f"  - Skipped (Already Ended): {summary['skipped_already_ended']}\n"
            f"  - Skipped (Not Started): {summary['skipped_not_started']}\n"
            f"  - Skipped (Already Notified): {summary['skipped_already_notified']}"
        )

        if summary.get("recipient_ids"):
            self.stdout.write(f"  - Recipient IDs: {summary['recipient_ids']}")
        if summary.get("detail"):
            self.stdout.write(f"  - Detail: {summary['detail']}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run completed. No notifications were sent."))
        elif summary["sent_count"] > 0:
            self.stdout.write(self.style.SUCCESS(f"Successfully sent {summary['sent_count']} reminder notification(s)."))
        else:
            self.stdout.write(self.style.SUCCESS("Execution finished with 0 reminders required."))

