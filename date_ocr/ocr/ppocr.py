"""Read complete inkjet DATE lines with the PP-OCR mobile CTC recognizer."""
import sys

import cv2
import numpy as np

from .date_parser import FIRST_LINE, SECOND_LINE
from .ocr_engine import application_root
from .preprocessing import preprocess_lines


MODEL_NAME = 'en_PP-OCRv5_rec_mobile.onnx'


def read_stamp(reader, crop):
    gray, binary, boxes, angle = preprocess_lines(crop)
    contrast = cv2.createCLAHE(clipLimit=2., tileGridSize=(8, 8)).apply(gray)
    lines = [contrast[top:bottom, left:right] for left, top, right, bottom in boxes]
    output = reader.read_lines(lines)
    raw = '\n'.join(text for text, _ in output)
    fields = {}
    for pattern, (text, scores) in zip((FIRST_LINE, SECOND_LINE), output):
        match = pattern.fullmatch(text)
        if match is None:
            return raw, {}, {}, {}
        for name, value in match.groupdict().items():
            left, right = match.span(name)
            fields[name] = dict(text=value, confidence=float(min(scores[left:right])))
    variants = dict(gray=gray, otsu=binary, clahe=contrast)
    metrics = dict(angle=angle, rows=[(top, bottom) for _, top, _, bottom in boxes])
    return raw, fields, variants, metrics


class PPOCRReader:
    def __init__(self, model_path):
        vendor = application_root() / 'packaging_onnx'
        if vendor.is_dir() and str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=options,
                                            providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        alphabet = self.session.get_modelmeta().custom_metadata_map['character'].splitlines()
        self.characters = ['', *alphabet, ' ']
        if len(self.characters) != self.session.get_outputs()[0].shape[-1]:
            raise ValueError('Từ điển ký tự PP-OCR không khớp model')
        self.read_lines((np.full((48, 400), 255, np.uint8), np.full((48, 220), 255, np.uint8)))

    def read_lines(self, lines):
        widths = [min(640, max(16, round(48 * row.shape[1] / row.shape[0]))) for row in lines]
        width = (max(widths) + 7) // 8 * 8
        batch = np.zeros((len(lines), 3, 48, width), np.float32)
        for index, (row, content_width) in enumerate(zip(lines, widths)):
            gray = cv2.resize(row, (content_width, 48))
            batch[index, :, :, :content_width] = gray.astype(np.float32) / 127.5 - 1
        probabilities = self.session.run(None, {self.input_name: batch})[0]
        return [self._decode(line) for line in probabilities]

    def _decode(self, probabilities):
        labels = probabilities.argmax(axis=1)
        starts = np.r_[True, labels[1:] != labels[:-1]]
        times = np.flatnonzero(starts & (labels != 0))
        return ''.join(self.characters[labels[t]] for t in times), probabilities[times, labels[times]]
