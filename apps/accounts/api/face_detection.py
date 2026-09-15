import base64
import binascii
import io
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import ValidationError
from rest_framework.exceptions import APIException
from django.conf import settings
from pathlib import Path


class FaceServiceUnavailable(APIException):
    status_code = 503
    default_detail = 'Face verification is unavailable. Ask your administrator to install the face models.'


def face_feature(photo):
    """YuNet landmarks + aligned SFace embedding; all inference stays on this server."""
    import cv2
    import numpy as np
    directory = Path(settings.FACE_MODEL_DIR)
    detector_path = directory / 'face_detection_yunet_2023mar.onnx'
    recognizer_path = directory / 'face_recognition_sface_2021dec.onnx'
    if not detector_path.is_file() or not recognizer_path.is_file():
        raise FaceServiceUnavailable()
    try:
        image = cv2.imdecode(np.frombuffer(photo, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValidationError('The stored photo is invalid. Please submit a new enrollment photo.')
        detector = cv2.FaceDetectorYN.create(str(detector_path), '', (image.shape[1], image.shape[0]), 0.9)
        _, faces = detector.detect(image)
        if faces is None or len(faces) != 1 or min(faces[0][2:4]) < 70:
            raise ValidationError('Show exactly one clear face, looking at the camera in good light.')
        recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), '')
        return recognizer.feature(recognizer.alignCrop(image, faces[0])).copy()
    except cv2.error as exc:
        raise FaceServiceUnavailable() from exc


def verify_face_photo(photo, reference):
    import numpy as np
    current = face_feature(photo).ravel()
    approved = face_feature(reference).ravel()
    score = float(np.dot(current, approved) / (np.linalg.norm(current) * np.linalg.norm(approved)))
    if not np.isfinite(score):
        raise FaceServiceUnavailable()
    return score, score >= settings.FACE_MATCH_THRESHOLD


def validate_face_photo(encoded):
    if not isinstance(encoded, str) or len(encoded) > 4_000_000:
        raise ValidationError('Choose a photo smaller than 3 MB.')
    try:
        raw = base64.b64decode(encoded, validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw))
            # Phone JPEGs can exceed 20 MP even at low compression quality.
            # Decode them at reduced resolution before allocating an RGB copy.
            pixel_limit = 64_000_000 if image.format == 'JPEG' else 20_000_000
            if image.width * image.height > pixel_limit:
                raise ValidationError('Photo resolution is too large.')
            image.draft('RGB', (1000, 1000))
            image.thumbnail((1000, 1000))
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.load()
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValidationError('This is not a valid photo.')
    import cv2
    import numpy as np
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    if detector.empty():
        raise ValidationError('Face detection is unavailable. Contact your administrator.')
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(70, 70))
    if len(faces) != 1:
        raise ValidationError('Show exactly one face, looking at the camera in good light. Please retake the photo.')
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=75)
    return output.getvalue()
