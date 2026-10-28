"""Regression benchmark for the 2026-09-06 handheld-image set."""
from datetime import date
from pathlib import Path
import argparse
import gc
import json
import sys
import time

import numpy as np

from main import load_image,write_json
from roi.auto_roi import AutoROIDetector
from roi.fixed_roi import FixedROI
from ocr.ocr_engine import EasyOCREngine
from ocr.pipeline import inspect_image
from ocr.validator import Expected
from ocr.alignment import inspect_alignment,AlignmentError


ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data/validation/testdate_20260906'


def percentile(values,number):
    return round(float(np.percentile(values,number)),2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations',type=int,default=5)
    parser.add_argument('--deadline-ms',type=float,default=100.)
    parser.add_argument('--output',type=Path,
                        default=ROOT/'data/debug/benchmark_validation.json')
    args=parser.parse_args()
    if args.iterations<1 or args.deadline_ms<=0:
        parser.error('iterations and deadline must be positive')
    if sys.platform=='win32':
        try:
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(),0x00000080)
        except Exception:
            pass

    truth=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
    images={name:load_image(DATA/name) for name in truth}
    roi_manifest=json.loads((ROOT/'config/sample_rois.json').read_text(encoding='utf-8'))
    detector=AutoROIDetector(ROOT/'data/test_images/date1.jpg',
                             FixedROI.from_dict(roi_manifest['date1.jpg']))
    engine=EasyOCREngine()
    expected=Expected('13',1,.70,True,date(2026,8,22))
    rows=[]
    for iteration in range(args.iterations):
        for name,ground_truth in truth.items():
            image=images[name]
            gc_was_enabled=gc.isenabled()
            if gc_was_enabled:gc.disable()
            started=time.perf_counter()
            stage=time.perf_counter()
            auto=detector.detect(image)
            auto_ms=(time.perf_counter()-stage)*1000
            if auto.found:
                stage=time.perf_counter()
                inspection=detector.rectify(image,auto)
                alignment_image=detector.rectify(image,auto,margin_bottom=1.05)
                rectify_ms=(time.perf_counter()-stage)*1000
                height,width=inspection.shape[:2]
                roi=FixedROI(0,0,width,height,width,height)
            else:
                inspection=image;roi=None;rectify_ms=0.;alignment_image=None
            report,_=inspect_image(inspection,roi,engine,expected,args.deadline_ms)
            alignment_started=time.perf_counter()
            if alignment_image is not None and report.get('metrics',{}).get('angle') is not None:
                try:
                    alignment,_=inspect_alignment(alignment_image,report['metrics']['angle'],
                        detector.reference_roi.height,expected.maximum_angle_deviation)
                    report['alignment']=alignment
                    if not alignment['passed'] and report['validation']['result']=='PASS':
                        report['validation'].update(result='FAIL_DATE_ALIGNMENT',
                            error_reason='DATE angle deviation exceeded configured limit')
                except AlignmentError as exc:
                    report['alignment']=dict(found=False,passed=False,error_reason=str(exc))
                    if report['validation']['result']=='PASS':
                        report['validation'].update(result='FAIL_REFERENCE_LINE_NOT_FOUND',
                                                    error_reason=str(exc))
            alignment_ms=(time.perf_counter()-alignment_started)*1000
            elapsed=(time.perf_counter()-started)*1000
            if gc_was_enabled:gc.enable()
            result=report['validation']['result']
            raw=report['validation'].get('raw','')
            expected_result=ground_truth['expected_result']
            exact=(raw==ground_truth['raw']) if expected_result=='PASS' else (result==expected_result)
            rows.append(dict(iteration=iteration+1,image=name,result=result,raw=raw,
                expected_result=expected_result,exact=exact,total_ms=round(elapsed,2),
                auto_roi_ms=round(auto_ms,2),rectify_ms=round(rectify_ms,2),
                preprocess_ms=report['stage_ms'].get('preprocess'),
                ocr_ms=report['stage_ms'].get('ocr'),
                alignment_ms=round(alignment_ms,2),
                angle_deviation=(report.get('alignment') or {}).get('deviation'),
                deadline_met=elapsed<=args.deadline_ms))

    times=[row['total_ms'] for row in rows]
    summary=dict(images=len(images),iterations=args.iterations,measurements=len(rows),
        exact=sum(row['exact'] for row in rows),
        expected_pass=sum(row['expected_result']=='PASS' and row['result']=='PASS' for row in rows),
        expected_reject=sum(row['expected_result']!='PASS' and row['result']==row['expected_result'] for row in rows),
        deadline_met=sum(row['deadline_met'] for row in rows),deadline_ms=args.deadline_ms,
        total_ms=dict(p50=percentile(times,50),p95=percentile(times,95),maximum=round(max(times),2)),
        measurements_detail=rows)
    write_json(args.output,summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='measurements_detail'},
                     ensure_ascii=False,indent=2))
    return 0 if summary['exact']==summary['deadline_met']==summary['measurements'] else 1


if __name__=='__main__':
    raise SystemExit(main())
