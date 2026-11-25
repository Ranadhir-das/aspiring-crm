import hashlib
from pathlib import Path
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calls.audio_processing import (
    ALLOWED_AUDIO_EXTENSIONS,
    MAX_RECORDING_SIZE,
    process_uploaded_audio,
    validate_audio_header,
)
from apps.calls.models import CallRecording
from apps.calls.recordings import permitted_call, playback
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
