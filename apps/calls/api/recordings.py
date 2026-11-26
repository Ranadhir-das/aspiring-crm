import hashlib
from pathlib import Path
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calls.ai_transcription import should_auto_queue_recording
from apps.calls.audio_processing import (
    ALLOWED_AUDIO_EXTENSIONS,
    MAX_RECORDING_SIZE,
    process_uploaded_audio,
    validate_audio_header,
)
from apps.calls.models import CallRecording
from apps.calls.recordings import permitted_call, playback
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from .permissions import CanCreateCall


class RecordingUploadSerializer(serializers.Serializer):
    recording = serializers.FileField()
    duration_seconds = serializers.IntegerField(min_value=0, max_value=2147483647)
    source_type = serializers.ChoiceField(
        choices=CallRecording.SourceType.choices,
        required=False,
        default=CallRecording.SourceType.OEM_AUTO_SYNC,
    )
    original_filename = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        default='',
    )

    def validate_recording(self, file):
        ext = Path(file.name).suffix.lower()
        if ext not in ALLOWED_AUDIO_EXTENSIONS or not 12 <= file.size <= MAX_RECORDING_SIZE:
            raise serializers.ValidationError(
                f'Upload a supported audio recording ({", ".join(sorted(ALLOWED_AUDIO_EXTENSIONS))}) '
                f'between 12 bytes and {MAX_RECORDING_SIZE // (1024 * 1024)} MB.'
            )
        header = file.read(16)
        file.seek(0)
        if not validate_audio_header(ext, header):
            if ext == '.m4a':
                raise serializers.ValidationError('Invalid M4A container header.')
            raise serializers.ValidationError(f'Invalid {ext.upper().lstrip(".")} audio file header.')
        return file


def transcript_payload(recording):
    call = recording.call
    lead = getattr(call, 'lead', None)
    caller = getattr(call, 'caller', None)
    caller_name = ''
    if caller:
        caller_name = (caller.get_full_name() or caller.username or '').strip()
    local_started = timezone.localtime(call.started_at) if call.started_at else None
    audio_url = reverse('web:call-recording', args=[recording.call_id])
    lead_url = reverse('web:lead-detail', args=[call.lead_id]) if call.lead_id else ''
    return {
        'call_id': recording.call_id,
        'lead_id': call.lead_id,
        'lead_name': lead.name if lead else '',
        'lead_url': lead_url,
        'phone_number': (lead.phone if lead else call.phone_number) or '',
        'caller_name': caller_name,
        'direction': call.direction,
        'direction_display': call.get_direction_display(),
        'outcome': call.outcome,
        'outcome_display': call.get_outcome_display() if call.outcome else 'Recorded',
        'started_at_display': local_started.strftime('%d %b %Y, %I:%M %p') if local_started else '',
        'duration_seconds': recording.duration_seconds,
        'duration_display': recording.duration_display,
        'audio_url': audio_url,
        'download_filename': f'call-{recording.call_id}-recording{recording.download_extension}',
        'call_notes': call.notes or '',
        'transcript_status': recording.transcript_status,
        'transcript_status_display': recording.get_transcript_status_display(),
        'ai_language': recording.ai_language,
        'ai_sentiment': recording.ai_sentiment,
        'ai_sentiment_display': recording.get_ai_sentiment_display() if recording.ai_sentiment else '',
        'ai_summary': recording.ai_summary,
        'ai_key_points': recording.ai_key_points or [],
        'ai_objections': recording.ai_objections or [],
        'ai_next_action': recording.ai_next_action,
        'ai_provider': recording.ai_provider,
        'transcript_text': recording.transcript_text,
        'transcript_segments': recording.transcript_segments or [],
        'transcript_error': recording.transcript_error,
        'transcribed_at': recording.transcribed_at.isoformat() if recording.transcribed_at else None,
    }


def metadata(recording, *, uploaded_file_size=None, transcoded=False):
    payload = {
        'call_id': recording.call_id,
        'duration_seconds': recording.duration_seconds,
        'file_size': recording.file_size,
        'uploaded_file_size': uploaded_file_size if uploaded_file_size is not None else recording.file_size,
        'created_at': recording.created_at,
        'sha256': recording.sha256,
        'source_sha256': recording.source_sha256 or recording.sha256,
        'content_type': recording.content_type,
        'source_type': recording.source_type,
        'original_format': recording.original_format,
        'transcoded': transcoded,
        'transcript_status': recording.transcript_status,
        'ai_language': recording.ai_language,
        'ai_sentiment': recording.ai_sentiment,
        'ai_summary': recording.ai_summary,
        'ai_objections_count': recording.objections_count,
    }
    return payload


class CallRecordingView(APIView):
    permission_classes = (CanCreateCall,)
    parser_classes = (MultiPartParser, FormParser)

    def get(self, request, call_id):
        return playback(permitted_call(request.user, call_id), request=request)

    def post(self, request, call_id):
        # Check ownership before parsing/storing uploaded bytes.
        permitted_call(request.user, call_id)
        serializer = RecordingUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file = serializer.validated_data['recording']
        duration = serializer.validated_data['duration_seconds']
        source_type = serializer.validated_data['source_type']
        original_filename = (serializer.validated_data.get('original_filename') or file.name or '')[:255]
        uploaded_size = file.size

        raw_digest = hashlib.sha256()
        for chunk in file.chunks():
            raw_digest.update(chunk)
        source_checksum = raw_digest.hexdigest()
        file.seek(0)

        # Fast-path idempotency check BEFORE invoking ffmpeg transcoding on retries
        existing_fast = CallRecording.objects.filter(call_id=call_id).first()
        if existing_fast:
            matches_existing = (
                (existing_fast.sha256, existing_fast.file_size, existing_fast.duration_seconds)
                == (source_checksum, uploaded_size, duration)
                or (
                    bool(existing_fast.source_sha256)
                    and existing_fast.source_sha256 == source_checksum
                    and (duration == 0 or existing_fast.duration_seconds == duration)
                )
            )
            if matches_existing:
                if not existing_fast.file.storage.exists(existing_fast.file.name):
                    return Response({'detail': 'Stored recording is unavailable. Contact your administrator.'}, status=409)
                return Response(
                    metadata(
                        existing_fast,
                        uploaded_file_size=uploaded_size,
                        transcoded=existing_fast.original_format != existing_fast.download_extension.lstrip('.'),
                    ),
                    status=200,
                )
            return Response({'detail': 'This call already has a different recording. Replacement is not allowed.'}, status=409)

        processed = process_uploaded_audio(file, file.name)
        stored_bytes = processed.data
        stored_checksum = hashlib.sha256(stored_bytes).hexdigest()
        stored_size = len(stored_bytes)
        final_duration = duration if duration > 0 else (processed.probed_duration_seconds or 0)

        saved_file = None
        try:
            with transaction.atomic():
                call = permitted_call(request.user, call_id, lock=True)
                existing = CallRecording.objects.filter(call=call).first()
                if existing:
                    matches_existing = (
                        (existing.sha256, existing.file_size, existing.duration_seconds)
                        == (stored_checksum, stored_size, final_duration)
                        or (
                            bool(existing.source_sha256)
                            and existing.source_sha256 == source_checksum
                            and (duration == 0 or existing.duration_seconds == final_duration)
                        )
                    )
                    if matches_existing:
                        if not existing.file.storage.exists(existing.file.name):
                            return Response({'detail': 'Stored recording is unavailable. Contact your administrator.'}, status=409)
                        return Response(
                            metadata(existing, uploaded_file_size=uploaded_size, transcoded=processed.transcoded),
                            status=200,
                        )
                    return Response({'detail': 'This call already has a different recording. Replacement is not allowed.'}, status=409)

                recording = CallRecording(
                    call=call,
                    duration_seconds=final_duration,
                    file_size=stored_size,
                    sha256=stored_checksum,
                    source_sha256=source_checksum,
                    source_type=source_type,
                    original_filename=original_filename,
                    original_format=processed.original_format,
                    content_type=processed.content_type,
                )
                if should_auto_queue_recording(recording):
                    recording.transcript_status = CallRecording.TranscriptStatus.PENDING
                    recording.transcript_priority = 1
                recording.file.save(f'{uuid4().hex}{processed.extension}', ContentFile(stored_bytes), save=False)
                saved_file = recording.file
                recording.save()
            return Response(
                metadata(recording, uploaded_file_size=uploaded_size, transcoded=processed.transcoded),
                status=201,
            )
        except Exception:
            if saved_file:
                saved_file.storage.delete(saved_file.name)
            raise


class CallRecordingTranscriptView(APIView):
    """Fetch AI transcript + summary/key points/objections or queue (re-)analysis."""
    permission_classes = (CanCreateCall,)

    def get(self, request, call_id):
        call = permitted_call(request.user, call_id)
        recording = get_object_or_404(CallRecording, call=call)
        return Response(transcript_payload(recording), status=200)

    def post(self, request, call_id):
        call = permitted_call(request.user, call_id)
        recording = get_object_or_404(CallRecording, call=call)
        force = bool(request.data.get('force')) if isinstance(request.data, dict) else False
        if recording.transcript_status == CallRecording.TranscriptStatus.PROCESSING:
            return Response(transcript_payload(recording), status=202)
        if recording.transcript_status != CallRecording.TranscriptStatus.COMPLETED or force:
            recording.transcript_status = CallRecording.TranscriptStatus.PENDING
            recording.transcript_attempts = 0
            recording.transcript_priority = 2
            recording.transcript_error = ''
            recording.save(update_fields=[
                'transcript_status',
                'transcript_attempts',
                'transcript_priority',
                'transcript_error',
            ])
        return Response(transcript_payload(recording), status=202)
