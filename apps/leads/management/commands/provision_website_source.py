import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.leads.models import Service, WebsiteSource


class Command(BaseCommand):
    help = 'Provision AuthenticAttest without printing or accepting a secret on the command line.'

    @transaction.atomic
    def handle(self, *args, **options):
        service = Service.objects.filter(code='APOSTILLE', is_active=True).first()
        if not service:
            raise CommandError('An active APOSTILLE service is required.')
        source = WebsiteSource.objects.filter(code__iexact='AUTHENTIC_ATTEST').first()
        key = os.environ.get('AUTHENTIC_ATTEST_API_KEY', '').strip()
        if not source and not key:
            raise CommandError('Set AUTHENTIC_ATTEST_API_KEY in the process environment first.')
        if key and (len(key) < 32 or len(key) > 128):
            raise CommandError('Credential must contain 32 to 128 characters.')
        if key and WebsiteSource.objects.filter(api_key=key).exclude(pk=source.pk if source else None).exists():
            raise CommandError('Credential is already assigned to another source.')
        if source is None:
            source = WebsiteSource(name='AuthenticAttest', code='AUTHENTIC_ATTEST', api_key=key)
        elif key:
            source.api_key = key
        source.code = 'AUTHENTIC_ATTEST'
        source.default_service = service
        source.is_active = True
        source.save()
        source.allowed_services.set([service])
        self.stdout.write(self.style.SUCCESS('AUTHENTIC_ATTEST configured: default/allowed service APOSTILLE. Credential not displayed.'))
