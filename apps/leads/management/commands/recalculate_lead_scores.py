from django.core.management.base import BaseCommand

from apps.leads.models import Lead
from apps.leads.scoring import backfill_lead_scores


class Command(BaseCommand):
    help = 'Recalculate 100-point AI lead scores and 5-tier temperatures for leads.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--lead-id',
            type=int,
            default=None,
            help='Recalculate a single lead ID instead of all leads.',
        )

    def handle(self, *args, **options):
        lead_id = options.get('lead_id')
        qs = Lead.objects.filter(pk=lead_id) if lead_id else Lead.objects.all()
        counts = backfill_lead_scores(qs)
        total = sum(counts.values())
        summary = ', '.join(f'{k}={v}' for k, v in counts.items())
        self.stdout.write(self.style.SUCCESS(f'Recalculated {total} leads ({summary})'))
