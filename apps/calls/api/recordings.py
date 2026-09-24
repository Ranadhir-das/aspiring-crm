import hashlib
from pathlib import Path
from uuid import uuid4

from django.db import transaction
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calls.models import CallRecording
from apps.calls.recordings import permitted_call, playback
from .permissions import CanCreateCall


class RecordingUploadSerializer(serializers.Serializer):
    recording = serializers.FileField()
    duration_seconds = serializers.IntegerField(min_value=0, max_value=2147483647)

    def validate_recording(self, file):
        if Path(file.name).suffix.lower() != '.m4a' or not 12 <= file.size <= 200 * 1024 * 1024:
            raise serializers.ValidationError('Upload an .m4a file between 12 bytes and 200 MB.')
        header = file.read(12)
        file.seek(0)
        if header[4:8] != b'ftyp':
            raise serializers.ValidationError('Invalid M4A container header.')
        return file


def metadata(recording):
    return {'call_id': recording.call_id, 'duration_seconds': recording.duration_seconds,
            'file_size': recording.file_size, 'created_at': recording.created_at,
            'sha256': recording.sha256}


class CallRecordingView(APIView):
    permission_classes = (CanCreateCall,)
    parser_classes = (MultiPartParser, FormParser)

    def get(self, request, call_id):
        return playback(permitted_call(request.user, call_id))

    def post(self, request, call_id):
        # Check ownership before parsing/storing uploaded bytes.
        permitted_call(request.user, call_id)
        serializer = RecordingUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file = serializer.validated_data['recording']
        duration = serializer.validated_data['duration_seconds']
        digest = hashlib.sha256()
        for chunk in file.chunks():
            digest.update(chunk)
        checksum = digest.hexdigest()
        file.seek(0)
        saved_file = None
        try:
            with transaction.atomic():
                call = permitted_call(request.user, call_id, lock=True)
                existing = CallRecording.objects.filter(call=call).first()
                if existing:
                    if (existing.sha256, existing.file_size, existing.duration_seconds) == (checksum, file.size, duration):
                        if not existing.file.storage.exists(existing.file.name):
                            return Response({'detail': 'Stored recording is unavailable. Contact your administrator.'}, status=409)
                        return Response(metadata(existing), status=200)
                    return Response({'detail': 'This call already has a different recording. Replacement is not allowed.'}, status=409)
                recording = CallRecording(call=call, duration_seconds=duration, file_size=file.size, sha256=checksum)
                recording.file.save(f'{uuid4().hex}.m4a', file, save=False)
                saved_file = recording.file
                recording.save()
            return Response(metadata(recording), status=201)
        except Exception:
            if saved_file:
                saved_file.storage.delete(saved_file.name)
            raise
