from django.core.management.base import BaseCommand
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from apps.performance.services import award, score_call, score_followup
from django.utils import timezone


class Command(BaseCommand):
    help = 'Idempotently post missed follow-ups. Use --backfill to score existing calls, completed follow-ups and interested leads.'

    def add_arguments(self, parser):
        parser.add_argument('--backfill', action='store_true')

    def handle(self, *args, **options):
        now = timezone.now()
        followups = FollowUp.objects.filter(status='PENDING', scheduled_at__lt=now)
        if options['backfill']:
            for pk in Call.objects.order_by('started_at', 'pk').values_list('pk', flat=True).iterator():
                score_call(pk)
            for lead in Lead.objects.filter(status='INTERESTED', assigned_caller__isnull=False).iterator():
                award(caller_id=lead.assigned_caller_id, event='INTERESTED', key=f'lead:{lead.pk}:INTERESTED', reason='Backfill: existing interested lead (latest recorded update)', occurred_at=lead.updated_at, lead=lead)
            followups = FollowUp.objects.exclude(status='CANCELLED')
        for pk in followups.values_list('pk', flat=True).iterator():
            score_followup(pk, now=now)
        self.stdout.write(self.style.SUCCESS('Points reconciled. Existing event keys were preserved.'))
