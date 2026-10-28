"""Save field crops from one image for preprocessing/OCR diagnosis."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from main import load_image
from ocr.ocr_engine import EasyOCREngine
from ocr.alignment import AlignmentError,inspect_alignment,_fit_dark_band_transition
from ocr.preprocessing import preprocess
from roi.auto_roi import AutoROIDetector
from roi.fixed_roi import FixedROI


def save_image(path: Path,image) -> None:
    extension=path.suffix or '.png'
    ok,encoded=cv2.imencode(extension,image)
    if not ok:
        raise RuntimeError(f'Cannot encode {path}')
    encoded.tofile(str(path))


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('roi',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--auto',action='store_true')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    source=load_image(args.roi)
    alignment_payload=None
    alignment_reloaded_payload=None
    alignment_transition=None
    alignment_transition_before_preprocess=None
    alignment_transition_debug=None
    alignment_source=None
    reference_height=None
    if args.auto:
        manifest=json.loads((ROOT/'config/sample_rois.json').read_text(encoding='utf-8'))
        detector=AutoROIDetector(ROOT/'data/test_images/date1.jpg',
                                 FixedROI.from_dict(manifest['date1.jpg']))
        detected=detector.detect(source)
        if not detected.found:
            raise RuntimeError('AUTO ROI failed: '+detected.message)
        alignment_source=detector.rectify(source,detected,margin_bottom=1.05)
        reference_height=detector.reference_roi.height
        save_image(args.output/'alignment_roi.png',alignment_source)
        alignment_reloaded_immediate=load_image(args.output/'alignment_roi.png')
        alignment_roundtrip=dict(
            equal=bool(np.array_equal(alignment_source,alignment_reloaded_immediate)),
            maximum_difference=int(np.max(np.abs(
                alignment_source.astype(np.int16)-alignment_reloaded_immediate.astype(np.int16)))),
            source_min=int(alignment_source.min()),source_max=int(alignment_source.max()),
            reloaded_min=int(alignment_reloaded_immediate.min()),
            reloaded_max=int(alignment_reloaded_immediate.max()))
        before_gray=cv2.cvtColor(alignment_source,cv2.COLOR_BGR2GRAY)
        before_top=max(0,round(reference_height*1.12))
        before_bottom=min(before_gray.shape[0],round(reference_height*1.85))
        before_zone=cv2.GaussianBlur(before_gray[before_top:before_bottom],(5,5),0)
        before_threshold,before_mask=cv2.threshold(
            before_zone,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
        before_coverage=(before_mask>0).mean(axis=1)
        before_offset=max(5,round(reference_height*.035))
        before_scores=[]
        for before_y in range(2*before_offset,len(before_coverage)-2*before_offset):
            before_above=max(before_coverage[before_y-before_offset],
                             before_coverage[before_y-2*before_offset])
            before_below=max(before_coverage[before_y+before_offset],
                             before_coverage[before_y+2*before_offset])
            before_scores.append((float(before_below-before_above),
                                  float(before_below),before_y))
        alignment_transition_debug=dict(threshold=float(before_threshold),
                                        best=max(before_scores))
        alignment_transition_before_preprocess=_fit_dark_band_transition(
            before_zone,before_mask,reference_height)
        source=detector.rectify(source,detected)
        save_image(args.output/'rectified_roi.png',source)
    variants,fields,metrics=preprocess(source)
    if alignment_source is not None:
        alignment_gray=cv2.cvtColor(alignment_source,cv2.COLOR_BGR2GRAY)
        alignment_top=max(0,round(reference_height*1.12))
        alignment_bottom=min(alignment_gray.shape[0],round(reference_height*1.85))
        alignment_zone=cv2.GaussianBlur(
            alignment_gray[alignment_top:alignment_bottom],(5,5),0)
        _,alignment_mask=cv2.threshold(
            alignment_zone,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
        alignment_transition=_fit_dark_band_transition(
            alignment_zone,alignment_mask,reference_height)
        try:
            alignment_payload,overlay=inspect_alignment(
                alignment_source,metrics['angle'],reference_height,3.)
            save_image(args.output/'alignment_overlay.png',overlay)
        except AlignmentError as exc:
            alignment_payload=dict(found=False,error_reason=str(exc))
        try:
            alignment_reloaded_payload,_=inspect_alignment(
                load_image(args.output/'alignment_roi.png'),metrics['angle'],
                reference_height,3.)
        except AlignmentError as exc:
            alignment_reloaded_payload=dict(found=False,error_reason=str(exc))
    engine=EasyOCREngine()
    for name,(left,top,right,bottom) in fields.items():
        save_image(args.output/f'{name}.png',variants['gray'][top:bottom,left:right])
    joined=engine._join_fields(variants['gray'],fields,('date','dm','ab','time'),
                               trim_names=('date','time'),padding=2,gap=12)
    save_image(args.output/'numeric_joined.png',joined)
    padding,gap=(2,12) if metrics['numeric_variant']=='gray' else (6,18)
    readings=engine.read_fields_fast(variants['gray'],fields,
                                     variants[metrics['numeric_variant']],variants['otsu'],
                                     variants[metrics['dm_variant']],padding,gap)
    otsu_anchor_readings={}
    for name in ('nsx_anchor','dm_anchor'):
        left,top,right,bottom=fields[name]
        value=engine._verify_anchor(variants['otsu'][top:bottom,left:right],name)
        otsu_anchor_readings[name]=dict(text=value.text,confidence=value.confidence)
    standard={}
    for variant_name,variant in variants.items():
        variant_joined=engine._join_fields(
            variant,fields,('date','dm','ab','time'),
            trim_names=('date','time'),padding=6,gap=18)
        save_image(args.output/f'numeric_{variant_name}.png',variant_joined)
        standard[variant_name]=engine.reader.recognize(
            variant_joined,detail=1,allowlist='0123456789:',paragraph=False,
            decoder='greedy',workers=0)
    payload=dict(fields=fields,metrics=metrics,alignment=alignment_payload,
                 alignment_reloaded=alignment_reloaded_payload,
                 alignment_transition=alignment_transition,
                 alignment_transition_before_preprocess=alignment_transition_before_preprocess,
                 alignment_transition_debug=alignment_transition_debug,
                 alignment_roundtrip=alignment_roundtrip if alignment_source is not None else None,
                 readings={name:dict(text=value.text,confidence=value.confidence)
                           for name,value in readings.items()},
                 otsu_anchor_readings=otsu_anchor_readings,
                 standard_numeric_readings=standard)
    (args.output/'diagnostic.json').write_text(
        json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
