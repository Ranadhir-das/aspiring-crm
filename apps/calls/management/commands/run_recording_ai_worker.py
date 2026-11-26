"""Dedicated background worker for Call Recording AI Transcription & Summarization.

Usage:
  # Continuous systemd daemon (low-priority, RAM-safe with idle model unload):
  python manage.py run_recording_ai_worker

  # Process current pending queue once and exit:
  python manage.py run_recording_ai_worker --once --limit 20

  # Backfill existing NONE recordings into PENDING and process them:
  python manage.py run_recording_ai_worker --backfill --once --limit 5

  # Immediately process one specific recording or call:
  python manage.py run_recording_ai_worker --call-id 19084
"""
from __future__ import annotations

from datetime import timedelta
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from apps.calls.ai_transcription import (
    process_recording_ai,
    resolve_ai_provider,
    unload_local_model_if_idle,
)
from apps.calls.models import CallRecording

MAX_ATTEMPTS = 3


class Command(BaseCommand):
    help = 'Transcribe (Hindi/English/Hinglish) and summarize queued call recordings in the background.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process available pending jobs and exit.')
        parser.add_argument('--backfill', action='store_true', help='Mark existing unanalyzed recordings as PENDING first.')
        parser.add_argument('--limit', type=int, default=0, help='Maximum recordings to process before exiting (0 = unlimited).')
        parser.add_argument('--poll-seconds', type=float, default=1.5, help='Polling interval in daemon mode.')
        parser.add_argument('--recording-id', type=int, default=0, help='Process a single CallRecording by ID.')
        parser.add_argument('--call-id', type=int, default=0, help='Process a single CallRecording by Call ID.')

    def handle(self, *args, **options):
        stop_requested = False

        def _handle_stop(signum, frame):
            nonlocal stop_requested
            stop_requested = True
            self.stdout.write(self.style.WARNING(f'Received signal {signum}; finishing current job and exiting...'))

        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)

        provider = resolve_ai_provider()
        self.stdout.write(self.style.SUCCESS(f'Vaani Recording AI Worker started (provider={provider}).'))

        # Direct single-recording execution path
        rec_id = options['recording_id']
        call_id = options['call_id']
        if rec_id or call_id:
            qs = CallRecording.objects.select_related('call', 'call__lead', 'call__caller')
            rec = qs.filter(pk=rec_id).first() if rec_id else qs.filter(call_id=call_id).first()
            if not rec:
                self.stderr.write(self.style.ERROR('Recording not found.'))
                return
            self._process_single(rec)
            return

        if options['backfill']:
            queued = self._backfill_none_recordings()
            self.stdout.write(f'Backfill queued {queued} unanalyzed recording(s) into PENDING.')

        limit = int(options['limit'] or 0)
        once = bool(options['once'])
        poll_seconds = max(0.5, float(options['poll_seconds'] or 1.5))
        processed = 0

        # Recover any orphaned PROCESSING rows left behind by a prior crash/reboot on startup
        self._recover_orphaned_processing()

        while not stop_requested:
            if not once:
                close_old_connections()
            recording = self._claim_next_pending()
            if recording is None:
                if once:
                    break
                unload_local_model_if_idle()
                time.sleep(poll_seconds)
                continue

            self._process_single(recording)
            processed += 1
            if limit > 0 and processed >= limit:
                break

        self.stdout.write(self.style.SUCCESS(f'Worker finished. Processed {processed} recording(s).'))

    def _backfill_none_recordings(self) -> int:
        min_dur = int(getattr(settings, 'AI_TRANSCRIBE_MIN_DURATION_SECONDS', 5))
        return CallRecording.objects.filter(
            transcript_status=CallRecording.TranscriptStatus.NONE,
        ).filter(
            Q(duration_seconds__gte=min_dur) | Q(duration_seconds=0, file_size__gte=16_000)
        ).update(transcript_status=CallRecording.TranscriptStatus.PENDING)

    def _recover_orphaned_processing(self) -> None:
        recovered = CallRecording.objects.filter(
            transcript_status=CallRecording.TranscriptStatus.PROCESSING,
            transcript_attempts__lt=MAX_ATTEMPTS,
        ).update(transcript_status=CallRecording.TranscriptStatus.PENDING)
        if recovered:
            self.stdout.write(self.style.WARNING(f'Recovered {recovered} interrupted PROCESSING recording(s) back to PENDING.'))

    def _claim_next_pending(self) -> CallRecording | None:
        with transaction.atomic():
            rec = (
                CallRecording.objects.select_for_update(skip_locked=True, of=('self',))
                .filter(
                    transcript_status=CallRecording.TranscriptStatus.PENDING,
                    transcript_attempts__lt=MAX_ATTEMPTS,
                )
                .order_by('-transcript_priority', '-call__started_at', '-pk')
                .first()
            )
            if rec is None:
                return None
            rec.transcript_status = CallRecording.TranscriptStatus.PROCESSING
            rec.transcript_attempts = (rec.transcript_attempts or 0) + 1
            rec.transcript_error = ''
            rec.save(update_fields=['transcript_status', 'transcript_attempts', 'transcript_error'])
            rec_pk = rec.pk
        return CallRecording.objects.select_related('call', 'call__lead', 'call__caller').get(pk=rec_pk)

    def _process_single(self, rec: CallRecording) -> None:
        t0 = time.monotonic()
        self.stdout.write(f'Processing recording #{rec.pk} (call #{rec.call_id}, {rec.duration_seconds}s)...')
        try:
            res = process_recording_ai(rec)
            elapsed = time.monotonic() - t0
            self.stdout.write(
                self.style.SUCCESS(
                    f'Completed recording #{rec.pk} in {elapsed:.1f}s '
                    f'[lang={res["ai_language"]}, sentiment={res["ai_sentiment"]}, '
                    f'objections={len(res["ai_objections"])}, provider={res["ai_provider"]}]'
                )
            )
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Failed recording #{rec.pk}: {exc}'))
