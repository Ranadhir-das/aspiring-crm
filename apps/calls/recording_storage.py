from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateRecordingStorage(FileSystemStorage):
    """Never use MEDIA_ROOT or expose a storage URL for cellular recordings."""
    def __init__(self):
        super().__init__(location=Path(settings.BASE_DIR) / 'private-call-recordings',
                         file_permissions_mode=0o600, directory_permissions_mode=0o700)

    def url(self, name):
        raise ValueError('Recordings are only available through authenticated playback.')


def recording_storage():
    return PrivateRecordingStorage()
