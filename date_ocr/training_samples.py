"""Store test captures and OCR suggestions for later human labeling."""
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from main import ROOT, save_image, write_json


TRAINING_TARGET = 1000


def default_training_directory():
    drive = Path('D:/')
    return drive / 'OCR_DATE_Training' if drive.is_dir() else ROOT / 'data/training'


def save_training_sample(directory, image, roi, report, captured_at, capture_mode):
    directory.mkdir(parents=True, exist_ok=True)
    sample_name = f'{captured_at:%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}'
    destination = directory / sample_name
    validation = report['validation']
    metadata = {
        'captured_at': captured_at.isoformat(timespec='milliseconds'),
        'capture_mode': capture_mode,
        'camera_frame': report['image'],
        'original_image': 'original.png',
        'roi_image': 'roi.png' if roi is not None else None,
        'roi_xywh': report.get('bbox'),
        'search_roi_xywh': report.get('search_bbox'),
        'ocr_text': validation['raw'],
        'ppocr_raw': report.get('ppocr_attempt', {}).get('raw'),
        'ocr_confidence': validation['overall_confidence'],
        'inspection_result': validation['result'],
        'label_text': None,
    }
    with TemporaryDirectory(prefix='.capture-', dir=directory) as work:
        staged = Path(work) / 'sample'
        staged.mkdir()
        save_image(staged / 'original.png', image)
        if roi is not None:
            save_image(staged / 'roi.png', roi)
        write_json(staged / 'sample.json', metadata)
        staged.rename(destination)
    return destination
