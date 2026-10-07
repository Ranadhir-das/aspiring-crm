import os
import re
from django.core.exceptions import ValidationError
from django.utils.text import get_valid_filename

# Maximum file size: 25 MB
MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    # Images
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    # Videos
    '.mp4': 'video/mp4',
    '.mov': 'video/quicktime',
    '.webm': 'video/webm',
    # PDF
    '.pdf': 'application/pdf',
}

DANGEROUS_EXTENSIONS = {
    '.exe', '.bat', '.cmd', '.sh', '.bin', '.dll', '.com', '.msi',
    '.php', '.py', '.js', '.vbs', '.scr', '.pif', '.jar', '.apk'
}


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    """Strip directory components, null bytes and dangerous chars, bounded to max_length."""
    if not filename:
        return 'attachment'
    basename = os.path.basename(filename.replace('\\', '/'))
    basename = re.sub(r'[\x00-\x1f\x7f]', '', basename)
    clean = get_valid_filename(basename)
    if not clean:
        clean = 'attachment'
    if len(clean) > max_length:
        stem, ext = os.path.splitext(clean)
        keep = max_length - len(ext)
        clean = f"{stem[:keep]}{ext}" if keep > 0 else clean[:max_length]
    return clean


def validate_file_signature(content: bytes, ext: str) -> str:
    """
    Validate magic bytes for the given extension.
    Returns the authoritative MIME type or raises ValidationError.
    """
    if not content:
        raise ValidationError('File is empty.')

    if ext in ('.jpg', '.jpeg'):
        if len(content) < 3 or not content.startswith(b'\xff\xd8\xff'):
            raise ValidationError('Invalid JPEG file content.')
        return 'image/jpeg'

    if ext == '.png':
        if len(content) < 8 or not content.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValidationError('Invalid PNG file content.')
        return 'image/png'

    if ext == '.webp':
        if len(content) < 12 or not content.startswith(b'RIFF') or content[8:12] != b'WEBP':
            raise ValidationError('Invalid WebP file content.')
        return 'image/webp'

    if ext == '.pdf':
        header = content.lstrip(b'\xef\xbb\xbf\r\n\t ')
        if len(header) < 5 or not header.startswith(b'%PDF-'):
            raise ValidationError('Invalid PDF file content. File header does not match %PDF.')
        return 'application/pdf'

    if ext == '.mp4':
        # ISO Base Media File Format box structure: size (4 bytes), type (4 bytes) e.g. b'ftyp'
        if len(content) < 8 or b'ftyp' not in content[:16]:
            raise ValidationError('Invalid MP4 video file content.')
        return 'video/mp4'

    if ext == '.mov':
        if len(content) < 8 or not (b'ftyp' in content[:16] or b'moov' in content[:32] or b'mdat' in content[:32] or b'wide' in content[:32]):
            raise ValidationError('Invalid QuickTime MOV video file content.')
        return 'video/quicktime'

    if ext == '.webm':
        # EBML Header: \x1a\x45\xdf\xa3
        if len(content) < 4 or not content.startswith(b'\x1a\x45\xdf\xa3'):
            raise ValidationError('Invalid WebM video file content.')
        return 'video/webm'

    raise ValidationError(f'Unsupported file format: {ext}')


def validate_attachment(uploaded_file, max_size=MAX_ATTACHMENT_SIZE_BYTES):
    """
    Validates uploaded file against allowed extensions, file size,
    and inspects magic bytes.
    Returns (clean_name, mime_type, file_size).
    """
    if not uploaded_file:
        raise ValidationError('No file provided.')

    original_name = getattr(uploaded_file, 'name', '') or 'attachment'
    clean_name = sanitize_filename(original_name)
    _, ext = os.path.splitext(clean_name.lower())

    if ext in DANGEROUS_EXTENSIONS:
        raise ValidationError(f'File type {ext} is dangerous and prohibited.')

    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(f'Unsupported file type {ext}. Supported types: Images (JPG, PNG, WebP), Video (MP4, MOV, WebM), PDF.')

    file_size = uploaded_file.size
    if file_size > max_size:
        raise ValidationError(f'File size ({file_size / (1024 * 1024):.1f} MB) exceeds maximum limit of {max_size / (1024 * 1024):.0f} MB.')

    if file_size == 0:
        raise ValidationError('File is empty.')

    # Read first 64 bytes to inspect magic header
    pos = uploaded_file.tell() if hasattr(uploaded_file, 'tell') else 0
    header = uploaded_file.read(64)
    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(pos)

    mime_type = validate_file_signature(header, ext)
    return clean_name, mime_type, file_size
