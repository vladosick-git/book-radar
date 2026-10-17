"""Stage 13: text recognition of ad images with a local Tesseract (no network, no paid API).

Enabled by RADAR_OCR=tesseract. The binary is taken from RADAR_TESSERACT_CMD, PATH or the default Windows
install folder. Languages: RADAR_OCR_LANGS (default rus+eng; the traineddata files must be installed), optionally
from RADAR_TESSDATA_DIR (passed as --tessdata-dir: no administrator rights are needed to add a language).
Recognition runs on the saved crop of an image block (img, CSS background, canvas, closed frame). An AT
image-only caption is also refreshed when its OCR pipeline revision changes. A missing engine is reported
as a status, never as a collection error.
"""
import os
import csv
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from PIL import Image

from . import config

IMAGE_SOURCES = ('img', 'css', 'canvas', 'iframe')
SPARSE_REVISION = 'native-image-2x-v1'
STATUSES = {'done': 'распознано', 'empty': 'текста нет', 'off': 'OCR выключен', 'missing': 'Tesseract не найден',
            'error': 'ошибка распознавания', 'timeout': 'распознавание не уложилось во время'}
WINDOWS_DEFAULT = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tesseract-OCR' / 'tesseract.exe'


def enabled():
    return config.OCR == 'tesseract'


def command():
    for candidate in (config.TESSERACT_CMD, shutil.which('tesseract'), str(WINDOWS_DEFAULT) if os.name == 'nt' else ''):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def data_args():
    return ['--tessdata-dir', config.TESSDATA_DIR] if config.TESSDATA_DIR else []


def engine():
    """Public engine state for the panel: no paths beyond the binary name."""
    cmd = command()
    info = {'mode': config.OCR, 'enabled': enabled(), 'available': bool(cmd), 'langs': config.OCR_LANGS, 'version': None, 'installed_langs': [],
            'missing_langs': []}
    if cmd:
        try:
            version = subprocess.run([cmd, '--version'], capture_output=True, timeout=10)
            info['version'] = (version.stdout or version.stderr).decode('utf-8', 'replace').splitlines()[0].strip()
            langs = subprocess.run([cmd, *data_args(), '--list-langs'], capture_output=True, timeout=10)
            info['installed_langs'] = [line.strip() for line in langs.stdout.decode('utf-8', 'replace').splitlines()[1:] if line.strip()]
            info['missing_langs'] = [lang for lang in config.OCR_LANGS.split('+') if lang and lang not in info['installed_langs']]
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    return info


def tidy(text):
    # Vertical bars at the edges are the frame of the crop read as text, not content.
    lines = [re.sub(r'\s+', ' ', line).strip().strip('|').strip() for line in text.splitlines()]
    return '\n'.join(line for line in lines if len(line) > 1)[:4000]


def sparse_text(tsv):
    """Discard low-confidence artwork fragments in a sparse banner, preserving recognised words."""
    lines = {}
    for word in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        try:
            confidence = float(word.get('conf', '-1'))
        except (TypeError, ValueError):
            continue
        text = (word.get('text') or '').strip()
        if confidence >= 60 and any(char.isalnum() for char in text):
            key = tuple(word.get(k) for k in ('page_num', 'block_num', 'par_num', 'line_num'))
            lines.setdefault(key, []).append(text)
    return tidy('\n'.join(' '.join(words) for words in lines.values()))


def prepare_sparse_image(path, directory):
    """Increase small artwork captions for the OCR engine, without altering the saved evidence."""
    with Image.open(path) as image:
        if image.width <= 800 and image.height <= 800:
            image.load()
            image = image.convert('RGB').resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
            prepared = Path(directory) / 'ocr-2x.png'
            image.save(prepared)
            return prepared
    return Path(path)


def recognize(path, *, psm=6):
    """(text, status). Never raises: OCR is an enrichment of a finding, not a condition of collection."""
    if not enabled():
        return '', 'off'
    cmd = command()
    if not cmd:
        return '', 'missing'
    try:
        with tempfile.TemporaryDirectory(prefix='radar-ocr-') as temporary:
            prepared = prepare_sparse_image(path, temporary) if psm == 11 else path
            options = ['-c', 'tessedit_create_tsv=1', '-c', 'tessedit_create_txt=0'] if psm == 11 else []
            result = subprocess.run([cmd, *data_args(), str(prepared), 'stdout', '-l', config.OCR_LANGS, '--psm', str(psm), *options], capture_output=True, timeout=config.OCR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return '', 'timeout'
    except (OSError, ValueError, Image.DecompressionBombError):
        return '', 'error'
    if result.returncode != 0:
        return '', 'error'
    output = result.stdout.decode('utf-8', 'replace')
    text = sparse_text(output) if psm == 11 else tidy(output)
    return text, 'done' if text else 'empty'
