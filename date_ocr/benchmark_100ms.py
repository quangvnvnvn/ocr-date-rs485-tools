"""Repeat the real AUTO ROI + OCR decision path and enforce the 100 ms budget."""
from pathlib import Path
import argparse
import json
import sys
import time
import numpy as np

from main import load_image,write_json
from roi.fixed_roi import FixedROI
from roi.auto_roi import AutoROIDetector
from ocr.ocr_engine import EasyOCREngine
from ocr.pipeline import inspect_image
from ocr.validator import Expected
from ocr.alignment import inspect_alignment,AlignmentError


ROOT=Path(__file__).resolve().parent
EXPECTED_RAW='NSX 220826 DM 1\n12 12:52'


def percentile(values,number):
    return round(float(np.percentile(values,number)),2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations',type=int,default=10)
    parser.add_argument('--deadline-ms',type=float,default=100.)
    parser.add_argument('--output',type=Path,default=ROOT/'data/debug/benchmark_100ms.json')
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

    manifest=json.loads((ROOT/'config/sample_rois.json').read_text(encoding='utf-8'))
    images=[(f'date{i}.jpg',load_image(ROOT/'data/test_images'/f'date{i}.jpg'))
            for i in range(1,6)]
    engine=EasyOCREngine()
    detector=AutoROIDetector(ROOT/'data/test_images/date1.jpg',
                             FixedROI.from_dict(manifest['date1.jpg']))
    expected=Expected('12',1,.70)
    measurements=[]
    for _ in range(args.iterations):
        for name,image in images:
            started=time.perf_counter()
            auto_started=time.perf_counter()
            auto=detector.detect(image)
            auto_ms=(time.perf_counter()-auto_started)*1000
            if auto.found:
                inspection_image=detector.rectify(image,auto)
                alignment_image=detector.rectify(image,auto,margin_bottom=1.05)
                height,width=inspection_image.shape[:2]
                roi=FixedROI(0,0,width,height,width,height)
            else:
                inspection_image=image;roi=None;alignment_image=None
            report,_=inspect_image(inspection_image,roi,engine,expected,args.deadline_ms)
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
            total_ms=(time.perf_counter()-started)*1000
            measurements.append(dict(image=name,total_ms=round(total_ms,2),
                auto_roi_ms=round(auto_ms,2),preprocess_ms=report['stage_ms'].get('preprocess'),
                ocr_ms=report['stage_ms'].get('ocr'),alignment_ms=round(alignment_ms,2),
                angle_deviation=(report.get('alignment') or {}).get('deviation'),
                result=report['validation']['result'],
                raw=report['validation'].get('raw'),confidence=report['validation'].get('overall_confidence'),
                deadline_met=total_ms<=args.deadline_ms))

    totals=[row['total_ms'] for row in measurements]
    summary=dict(deadline_ms=args.deadline_ms,measurements=len(measurements),
        deadline_met=sum(row['deadline_met'] for row in measurements),
        pass_count=sum(row['result']=='PASS' for row in measurements),
        exact_raw=sum(row['raw']==EXPECTED_RAW for row in measurements),
        total_ms=dict(p50=percentile(totals,50),p95=percentile(totals,95),
                      maximum=round(max(totals),2)),measurements_detail=measurements)
    write_json(args.output,summary)
    print(json.dumps({key:value for key,value in summary.items() if key!='measurements_detail'},
                     ensure_ascii=False,indent=2))
    return 0 if (summary['deadline_met']==summary['measurements']==summary['pass_count']==
                 summary['exact_raw']) else 1


if __name__=='__main__':
    raise SystemExit(main())
