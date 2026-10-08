from django.core.management.base import BaseCommand
from apps.accounts.models import EmployeeLocationPoint
from apps.accounts.location_service import retention_cutoff


class Command(BaseCommand):
    help = 'Remove location points older than 90 days in bounded batches. Schedule daily.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=5000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        cutoff = retention_cutoff()
        query = EmployeeLocationPoint.objects.filter(recorded_at__lt=cutoff)
        if options['dry_run']:
            self.stdout.write(f'{query.count()} location points eligible for cleanup.')
            return
        size = min(max(options['batch_size'], 1), 10000)
        count = 0
        while ids := list(query.order_by('recorded_at', 'pk').values_list('pk', flat=True)[:size]):
            deleted, _ = EmployeeLocationPoint.objects.filter(pk__in=ids, recorded_at__lt=cutoff).delete()
            count += deleted
        self.stdout.write(f'Removed {count} expired location points.')
