import hashlib
from pathlib import Path
from urllib.request import urlopen
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


MODELS = [
    ('face_detection_yunet', 'face_detection_yunet_2023mar.onnx', '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'),
    ('face_recognition_sface', 'face_recognition_sface_2021dec.onnx', '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'),
]


class Command(BaseCommand):
    help = 'Install OpenCV YuNet and SFace models with SHA-256 verification.'

    def handle(self, *args, **options):
        directory = Path(settings.FACE_MODEL_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        for folder, filename, expected in MODELS:
            destination = directory / filename
            if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
                self.stdout.write(f'{filename}: already verified')
                continue
            url = f'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/{folder}/{filename}'
            try:
                with urlopen(url, timeout=60) as response:
                    data = response.read(45_000_000)
            except OSError as exc:
                raise CommandError(f'Could not download {filename}: {exc}') from exc
            if hashlib.sha256(data).hexdigest() != expected:
                raise CommandError(f'Checksum failed for {filename}; no model installed.')
            temporary = destination.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(destination)
            self.stdout.write(self.style.SUCCESS(f'{filename}: installed and verified'))
