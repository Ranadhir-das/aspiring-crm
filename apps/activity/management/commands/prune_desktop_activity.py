"""
Management command to safely aggregate and prune detailed 60-second PC activity buckets
older than a configurable retention period (default: 90 days).
"""
from django.core.management.base import BaseCommand, CommandError
from apps.activity.desktop_services import aggregate_and_prune_activity_buckets


class Command(BaseCommand):
    help = (
        "Aggregates raw 60-second PC activity buckets older than retention days "
        "into DesktopDailySummary and deletes raw buckets. Safe, idempotent, and transaction-safe."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Retention period in days. Detailed buckets older than this will be aggregated and pruned (default: 90).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the cleanup without modifying or deleting any database records.",
        )

    def handle(self, *args, **options):
        retention_days = options["days"]
        dry_run = options["dry_run"]

        if retention_days < 1:
            raise CommandError("The --days argument must be at least 1.")

        mode_label = "DRY-RUN" if dry_run else "LIVE"
        self.stdout.write(f"Executing PC activity data retention cleanup [{mode_label}] (retention: {retention_days} days)...")

        result = aggregate_and_prune_activity_buckets(
            retention_days=retention_days,
            dry_run=dry_run,
            actor=None,
        )

        status = result["status"]
        cutoff = result["cutoff_date"]
        agg_days = result["aggregated_days_count"]
        pruned_buckets = result["pruned_buckets_count"]
        pruned_apps = result["pruned_apps_count"]

        if status == "NOOP":
            self.stdout.write(self.style.SUCCESS(f"Zero raw buckets found older than {retention_days} days (cutoff: {cutoff}). Nothing to prune."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[DRY-RUN COMPLETE]\n"
                f"  Cutoff date: {cutoff}\n"
                f"  Employee days to aggregate: {agg_days}\n"
                f"  Raw 60s activity buckets to prune: {pruned_buckets}\n"
                f"  Foreground app usages to prune: {pruned_apps}\n"
                f"No records were modified or deleted."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"[CLEANUP COMPLETE]\n"
                f"  Cutoff date: {cutoff}\n"
                f"  Daily summaries created/updated: {agg_days}\n"
                f"  Raw 60s activity buckets pruned: {pruned_buckets}\n"
                f"  Foreground app usages pruned: {pruned_apps}\n"
                f"Historical reporting data successfully preserved in DesktopDailySummary."
            ))

