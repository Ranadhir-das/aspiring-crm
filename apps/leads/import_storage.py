import json
import os
import secrets
import tempfile
import time
from django.conf import settings
from django.core.files.base import ContentFile

IMPORT_TEMP_DIR_NAME = 'vaani_lead_imports'
MAX_TOKEN_AGE_SECONDS = 7200  # 2 hours


def _get_temp_dir():
    base_dir = getattr(settings, 'FILE_UPLOAD_TEMP_DIR', None) or tempfile.gettempdir()
    target_dir = os.path.join(base_dir, IMPORT_TEMP_DIR_NAME)
    os.makedirs(target_dir, exist_ok=True)
    try:
        os.chmod(target_dir, 0o777)
    except OSError:
        pass
    return target_dir


def cleanup_expired_import_files(max_age_seconds=MAX_TOKEN_AGE_SECONDS):
    """Purge staging files older than max_age_seconds."""
    try:
        temp_dir = _get_temp_dir()
        now = time.time()
        for fname in os.listdir(temp_dir):
            fpath = os.path.join(temp_dir, fname)
            if os.path.isfile(fpath):
                if now - os.path.getmtime(fpath) > max_age_seconds:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
    except Exception:
        pass


def save_import_preview_file(user_id, uploaded_file):
    """
    Saves an uploaded import file to temporary storage tied to user_id.
    Returns a secure token string.
    """
    cleanup_expired_import_files()
    filename = getattr(uploaded_file, 'name', 'leads.csv')
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ('.csv', '.xlsx'):
        raise ValueError('Unsupported file format. Please upload a CSV or XLSX file.')

    token = secrets.token_urlsafe(32)
    temp_dir = _get_temp_dir()
    file_path = os.path.join(temp_dir, f"{token}{ext}")
    meta_path = os.path.join(temp_dir, f"{token}.meta")

    # Read uploaded file content from start
    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(0)
    content = uploaded_file.read()
    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(0)

    if isinstance(content, str):
        content = content.encode('utf-8')

    with open(file_path, 'wb') as f:
        f.write(content)
    try:
        os.chmod(file_path, 0o666)
    except OSError:
        pass

    meta = {
        'token': token,
        'user_id': user_id,
        'filename': filename,
        'size': len(content),
        'created_at': time.time(),
        'ext': ext,
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f)
    try:
        os.chmod(meta_path, 0o666)
    except OSError:
        pass

    return token


def get_import_preview_file(token, user_id):
    """
    Retrieves the stored staging file for the given token and user_id.
    Returns a ContentFile or None if invalid/expired/unauthorized.
    """
    if not token or not isinstance(token, str):
        return None

    # Sanitize token: only alphanumeric, hyphen, underscore allowed
    clean_token = token.strip()
    if not clean_token.replace('-', '').replace('_', '').isalnum() or len(clean_token) < 20:
        return None

    temp_dir = _get_temp_dir()
    meta_path = os.path.join(temp_dir, f"{clean_token}.meta")
    if not os.path.exists(meta_path):
        return None

    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    except Exception:
        return None

    # Security check: must match the authenticated user
    if str(meta.get('user_id')) != str(user_id):
        return None

    # Expiration check
    if time.time() - meta.get('created_at', 0) > MAX_TOKEN_AGE_SECONDS:
        cleanup_import_preview_file(clean_token, user_id)
        return None

    ext = meta.get('ext', '.csv')
    file_path = os.path.join(temp_dir, f"{clean_token}{ext}")
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, 'rb') as f:
            content = f.read()
        return ContentFile(content, name=meta.get('filename', f"import{ext}"))
    except Exception:
        return None


def cleanup_import_preview_file(token, user_id=None):
    """Deletes temporary file and meta for the given token."""
    if not token or not isinstance(token, str):
        return
    clean_token = token.strip()
    if not clean_token.replace('-', '').replace('_', '').isalnum():
        return

    temp_dir = _get_temp_dir()
    meta_path = os.path.join(temp_dir, f"{clean_token}.meta")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            if user_id is not None and str(meta.get('user_id')) != str(user_id):
                return
            ext = meta.get('ext', '.csv')
            file_path = os.path.join(temp_dir, f"{clean_token}{ext}")
            if os.path.exists(file_path):
                os.remove(file_path)
            os.remove(meta_path)
        except Exception:
            pass
