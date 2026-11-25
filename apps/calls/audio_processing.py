"""Multi-format Android OEM call recording validation and FFmpeg transcoding.

Accepts real-world audio formats produced by Samsung, Oppo, Realme, OnePlus,
Vivo, iQOO, Xiaomi, Redmi, Poco, and generic Android call recorders, validates
container magic bytes, and transcodes non-universal formats (AMR, AMR-WB, 3GP,
raw AAC, large WAV) into browser-universal, AI-transcription-ready 16kHz mono
AAC-LC (.m4a with +faststart) when ffmpeg is installed on the host.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger('apps.calls')

MAX_RECORDING_SIZE = 30 * 1024 * 1024  # 30 MB

ALLOWED_AUDIO_EXTENSIONS: dict[str, str] = {
    '.m4a': 'audio/mp4',
    '.mp4': 'audio/mp4',
    '.mp3': 'audio/mpeg',
    '.amr': 'audio/amr',
    '.awb': 'audio/amr-wb',
    '.3gp': 'audio/3gpp',
    '.3gpp': 'audio/3gpp',
    '.aac': 'audio/aac',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.oga': 'audio/ogg',
    '.opus': 'audio/ogg',
    '.webm': 'audio/webm',
}

# Formats that Chrome/Safari/Edge HTML5 <audio> either cannot decode natively
# or cannot seek reliably without remuxing into an MP4/M4A +faststart container.
TRANSCODE_REQUIRED_EXTENSIONS = {'.amr', '.awb', '.3gp', '.3gpp', '.aac', '.opus'}
# Also transcode large uncompressed WAV files (> 2 MB) to compact AAC-LC M4A.
WAV_TRANSCODE_THRESHOLD_BYTES = 2 * 1024 * 1024


@dataclass
class ProcessedAudio:
    data: bytes
    extension: str
    content_type: str
    original_format: str
    transcoded: bool
    probed_duration_seconds: int | None = None


def infer_content_type(filename: str | None, fallback: str = 'audio/mp4') -> str:
    if not filename:
        return fallback
    ext = Path(filename).suffix.lower()
    return ALLOWED_AUDIO_EXTENSIONS.get(ext, fallback)


def validate_audio_header(ext: str, header: bytes) -> bool:
    """Strict magic-byte validation per supported container/codec."""
    if len(header) < 12:
        return False

    ext = ext.lower()
    if ext in {'.m4a', '.mp4', '.3gp', '.3gpp'}:
        return header[4:8] == b'ftyp'
    if ext == '.mp3':
        if header[:3] == b'ID3':
            return True
        # Raw MPEG Layer III frame sync (11 set bits: 0xFF 0xE0..0xFF)
        return header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
    if ext == '.amr':
        return header.startswith(b'#!AMR\n') or header.startswith(b'#!AMR_MC1.0\n')
    if ext == '.awb':
        return header.startswith(b'#!AMR-WB\n') or header.startswith(b'#!AMR-WB_MC1.0\n')
    if ext == '.aac':
        if header[:3] == b'ID3':
            return True
        # ADTS sync word: 12 set bits (0xFFF)
        return header[0] == 0xFF and (header[1] & 0xF0) == 0xF0
    if ext == '.wav':
        return header[:4] == b'RIFF' and header[8:12] == b'WAVE'
    if ext in {'.ogg', '.oga', '.opus'}:
        return header[:4] == b'OggS'
    if ext == '.webm':
        return header[:4] == b'\x1a\x45\xdf\xa3'
    return False


def _probe_duration_seconds(file_path: str) -> int | None:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    try:
        res = subprocess.run(
            [
                ffprobe,
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            val = float(res.stdout.strip())
            if val > 0:
                return max(1, int(round(val)))
    except Exception as exc:
        logger.debug('ffprobe duration check skipped: %s', exc)
    return None


def process_uploaded_audio(uploaded_file: BinaryIO, original_name: str) -> ProcessedAudio:
    """Reads validated audio bytes and transcodes to faststart .m4a when needed.

    Fast path: `.m4a` uploads are kept untouched (0 CPU overhead and exact byte
    fidelity for backward-compatible clients). Non-browser OEM formats (`.amr`,
    `.awb`, `.3gp`, `.aac`, `.opus`, large `.wav`) are transcoded via local
    `ffmpeg` into speech-optimized 16kHz mono 48kbps AAC-LC `.m4a` with
    `+faststart` for instant CRM seeking and future AI speech-to-text pipelines.
    """
    ext = Path(original_name).suffix.lower() or '.m4a'
    original_format = ext.lstrip('.')
    uploaded_file.seek(0)
    raw_bytes = uploaded_file.read()
    uploaded_file.seek(0)

    needs_transcode = ext in TRANSCODE_REQUIRED_EXTENSIONS or (
        ext == '.wav' and len(raw_bytes) > WAV_TRANSCODE_THRESHOLD_BYTES
    )

    ffmpeg = shutil.which('ffmpeg') if needs_transcode else None
    if not needs_transcode or not ffmpeg:
        if needs_transcode and not ffmpeg:
            logger.warning(
                'ffmpeg not found on PATH; storing %s recording without transcoding.',
                ext,
            )
        return ProcessedAudio(
            data=raw_bytes,
            extension=ext,
            content_type=ALLOWED_AUDIO_EXTENSIONS.get(ext, 'audio/mp4'),
            original_format=original_format,
            transcoded=False,
            probed_duration_seconds=None,
        )

    tmp_in_path = None
    tmp_out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_in:
            tmp_in.write(raw_bytes)
            tmp_in_path = tmp_in.name
        with tempfile.NamedTemporaryFile(suffix='.m4a', delete=False) as tmp_out:
            tmp_out_path = tmp_out.name

        cmd = [
            ffmpeg,
            '-y',
            '-hide_banner',
            '-loglevel', 'error',
            '-i', tmp_in_path,
            '-vn',
            '-ac', '1',
            '-ar', '16000',
            '-c:a', 'aac',
            '-b:a', '48k',
            '-movflags', '+faststart',
            tmp_out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0 or not os.path.isfile(tmp_out_path) or os.path.getsize(tmp_out_path) < 12:
            logger.warning(
                'ffmpeg transcode failed for %s (rc=%s): %s; falling back to original bytes.',
                ext,
                result.returncode,
                (result.stderr or '')[:300],
            )
            return ProcessedAudio(
                data=raw_bytes,
                extension=ext,
                content_type=ALLOWED_AUDIO_EXTENSIONS.get(ext, 'audio/mp4'),
                original_format=original_format,
                transcoded=False,
                probed_duration_seconds=_probe_duration_seconds(tmp_in_path),
            )

        transcoded_bytes = Path(tmp_out_path).read_bytes()
        duration = _probe_duration_seconds(tmp_out_path)
        return ProcessedAudio(
            data=transcoded_bytes,
            extension='.m4a',
            content_type='audio/mp4',
            original_format=original_format,
            transcoded=True,
            probed_duration_seconds=duration,
        )
    except Exception as exc:
        logger.warning('Audio transcode exception for %s: %s; keeping original bytes.', ext, exc)
        return ProcessedAudio(
            data=raw_bytes,
            extension=ext,
            content_type=ALLOWED_AUDIO_EXTENSIONS.get(ext, 'audio/mp4'),
            original_format=original_format,
            transcoded=False,
            probed_duration_seconds=None,
        )
    finally:
        for p in (tmp_in_path, tmp_out_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
