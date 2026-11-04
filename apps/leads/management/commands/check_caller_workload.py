"""
Management command to evaluate caller workload backlogs and synchronize low-workload alerts.

Usage:
    python manage.py check_caller_workload [--threshold 10] [--target 30]
"""
from django.core.management.base import BaseCommand
from apps.leads.workload import evaluate_all_callers_workload, get_workload_summary


class Command(BaseCommand):
    help = "Evaluate active caller workload backlogs, open low-workload alerts, and resolve stale alerts."

    def add_arguments(self, parser):
        parser.add_argument(
            '--threshold',
            type=int,
            default=None,
            help='Override the low-workload pending leads threshold (default from settings: 10)',
        )
        parser.add_argument(
            '--target',
            type=int,
            default=None,
            help='Override the target pending leads count (default from settings: 30)',
        )

    def handle(self, *args, **options):
        threshold = options.get('threshold')
        target = options.get('target')

        result = evaluate_all_callers_workload(threshold=threshold, target=target)
        summary = get_workload_summary(threshold=threshold, target=target)

        self.stdout.write(
            self.style.SUCCESS(
                f"Workload evaluation complete: {result['evaluated_callers']} callers evaluated, "
                f"{result['opened_alerts']} alerts opened, {result['resolved_alerts']} alerts resolved. "
                f"Total active OPEN alerts: {result['total_open_alerts']}."
            )
        )

        if summary['items']:
            self.stdout.write("\nCurrent caller backlogs:")
            for item in summary['items']:
                status_str = f"ALERT (Needs {item['suggested_count']})" if item['is_low'] else "OK"
                self.stdout.write(
                    f" - {item['caller_name']} (@{item['username']}): "
                    f"{item['pending_count']} pending / target {item['target_count']} [{status_str}]"
                )
