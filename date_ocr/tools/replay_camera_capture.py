"""Replay saved camera frames through the same AUTO ROI/OCR runtime as the GUI."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from inspection_runtime import InspectionRuntime
from main import load_image
from ocr.validator import Expected


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('capture_directory',type=Path)
    parser.add_argument('--expected-ab',default='12')
    parser.add_argument('--expected-dm',type=int,default=1)
    parser.add_argument('--expected-date',type=date.fromisoformat,default=date(2026,8,22))
    parser.add_argument('--minimum-confidence',type=float,default=.80)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if sys.platform=='win32':
        try:
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(),0x00000080)
        except Exception:
            pass

    folders=sorted(path for path in args.capture_directory.iterdir()
                   if path.is_dir() and (path/'original.png').exists())
    runtime=InspectionRuntime()
    runtime.prepare(require_auto_roi=True,status=lambda message: print(message,flush=True))
    expected=Expected(args.expected_ab,args.expected_dm,args.minimum_confidence,
                      check_nsx_date=True,expected_date=args.expected_date)
    rows=[]
    for index,folder in enumerate(folders,1):
        original=folder/'original.png'
        report,_=runtime.inspect(load_image(original),None,'auto',expected,
                                 image_name=original.name,source='camera_replay')
        validation=report['validation']
        selected=report.get('selected')
        candidate=next((item for item in report.get('candidates',[])
                        if item.get('name')==selected),None)
        rows.append(dict(frame=folder.name,result=validation['result'],
                         raw=validation.get('raw',''),parsed=validation.get('parsed'),
                         overall_confidence=validation.get('overall_confidence'),
                         error_reason=validation.get('error_reason',''),
                         processing_ms=report['processing_ms'],
                         deadline_met=report['deadline_met'],
                         ocr_device=report.get('ocr_device'),
                         preprocess_ms=report['stage_ms'].get('preprocess'),
                         ocr_ms=report['stage_ms'].get('ocr'),
                         auto_roi_ms=report['stage_ms'].get('auto_roi'),
                         auto_roi_confidence=report.get('auto_roi',{}).get('confidence'),
                         alignment=report.get('alignment'),
                         candidate_fields=candidate.get('fields') if candidate else None,
                         candidate_raw=candidate.get('raw','') if candidate else ''))
        if index%5==0 or index==len(folders):
            print(f'{index}/{len(folders)} {validation["result"]} '
                  f'{report["processing_ms"]:.1f} ms',flush=True)

    result_counts=Counter(row['result'] for row in rows)
    unreadable={'FAIL_ROI_NOT_FOUND','FAIL_OCR','FAIL_OCR_LOW_CONFIDENCE'}
    alignment_rows=[row['alignment'] for row in rows if row.get('alignment')]
    deviations=[item['deviation'] for item in alignment_rows
                if item.get('found') and item.get('deviation') is not None]
    summary=dict(capture_directory=str(args.capture_directory.resolve()),
                 frames=len(rows),results=dict(sorted(result_counts.items())),
                 readable=sum(row['result'] not in unreadable and row['parsed'] is not None
                              for row in rows),
                 deadline_met=sum(row['deadline_met'] for row in rows),
                 ocr_devices=sorted({row['ocr_device'] for row in rows
                                     if row.get('ocr_device')}),
                 alignment_found=sum(bool(item.get('found')) for item in alignment_rows),
                 alignment_passed=sum(bool(item.get('passed')) for item in alignment_rows),
                 maximum_angle_deviation=max(deviations,default=None),
                 maximum_processing_ms=max((row['processing_ms'] for row in rows),default=0),
                 average_processing_ms=(sum(row['processing_ms'] for row in rows)/len(rows)
                                        if rows else 0),rows=rows)
    output=args.output or args.capture_directory/'replay_after_fix.json'
    output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in summary.items() if key!='rows'},
                     ensure_ascii=False,indent=2),flush=True)
    print(f'Report: {output.resolve()}',flush=True)
    return 0 if summary['readable']==summary['frames'] else 1


if __name__=='__main__':
    raise SystemExit(main())
