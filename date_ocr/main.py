"""Static-image CLI; the Windows GUI also supports hardware-triggered HIKROBOT frames."""
import argparse
from datetime import date,datetime
import csv
import html
import json
from pathlib import Path
import uuid
import sys
import time
import cv2
import numpy as np
from roi.fixed_roi import FixedROI
from ocr.validator import Expected
from ocr.ocr_engine import EasyOCREngine
from ocr.pipeline import inspect_image
from ocr.alignment import inspect_alignment,fixed_alignment_region,AlignmentError


def application_root():
    """Writable application directory for source and portable EXE runs."""
    if getattr(sys,'frozen',False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT=application_root()
EXTENSIONS={'.jpg','.jpeg','.png','.bmp'}


def load_image(path):
    image=cv2.imdecode(np.fromfile(str(path),dtype=np.uint8),cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'Cannot decode image: {path}')
    return image


def save_image(path,image):
    ok,encoded=cv2.imencode('.png',image)
    if not ok:
        raise ValueError(f'Cannot encode {path}')
    encoded.tofile(str(path))


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)


def save_report(out,records):
    esc=lambda value:html.escape(str(value))
    sections=[]
    for index,record in enumerate(records):
        validation=record['validation']; folder=f'{index+1:03d}_{Path(record["image"]).stem}'
        visuals=''.join(f'<figure><figcaption>{esc(name)}</figcaption><img src="{folder}/{name}.png"></figure>'
                        for name in record['artifacts'])
        fields=''
        for candidate in record.get('candidates',[]):
            rows=''.join(f'<tr><td>{esc(name)}</td><td>{esc(v["text"])}</td><td>{v["confidence"]:.6f}</td></tr>'
                         for name,v in candidate['fields'].items())
            fields+=f'<h3>{esc(candidate["name"])}</h3><pre>{esc(candidate["raw"])}</pre><table><tr><th>Field</th><th>OCR raw</th><th>Confidence</th></tr>{rows}</table>'
        sections.append(f'<section><h2>{esc(record["image"])} — {esc(validation["result"])}</h2>'
                        f'<p>Selected: {esc(record.get("selected","none"))} · {record["processing_ms"]} ms</p>'
                        f'<pre>{esc(json.dumps(validation,ensure_ascii=False,indent=2))}</pre>'
                        f'<div class="images">{visuals}</div>{fields}</section>')
    passes=sum(r['validation']['result']=='PASS' for r in records)
    document='''<!doctype html><html lang="vi"><meta charset="utf-8"><title>OCR DATE — Phase 1</title>
<style>body{font:16px system-ui;margin:32px;background:#f3f5f7;color:#162331}section{background:white;padding:24px;margin:24px 0;border-radius:12px}h1,h2{color:#174e67}.images{display:flex;flex-wrap:wrap;gap:12px}figure{margin:0;max-width:720px}img{max-width:100%;max-height:420px;object-fit:contain}table{border-collapse:collapse}td,th{padding:8px 18px;border:1px solid #ccd4da}pre{white-space:pre-wrap;background:#edf2f5;padding:12px}</style>
<h1>OCR DATE — kiểm thử ảnh tĩnh</h1>
<p>ROI được khoanh riêng từng ảnh, không phải AUTO/HYBRID. Chỉ OCR vùng DATE. Confidence là điểm của engine, chưa hiệu chuẩn thành xác suất đúng.</p>
<p>Gray và Otsu được OCR; CLAHE/adaptive/morphology chỉ để xem debug. Bảng raw giữ nguyên kết quả từng field, chuỗi hai dòng ghép theo layout.</p>'''
    document+=f'<p>Tổng: {len(records)} · PASS: {passes} · FAIL: {len(records)-passes}. Tỷ lệ PASS không phải accuracy.</p>'
    document+=''.join(sections)+'</html>'
    (out/'report.html').write_text(document,encoding='utf-8')
    write_json(out/'results.json',records)
    with (out/'results.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['image','date','dm','ab','time','confidence','angle_deviation_deg','result','error','processing_ms'])
        for r in records:
            v=r['validation']; p=v.get('parsed') or {}
            writer.writerow([r['image'],p.get('nsx_raw'),p.get('dm'),p.get('ab'),p.get('time'),
                             v.get('overall_confidence'),(r.get('alignment') or {}).get('deviation'),
                             v['result'],v['error_reason'],r['processing_ms']])


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--image',type=Path)
    source.add_argument('--folder',type=Path)
    parser.add_argument('--select-roi',action='store_true')
    parser.add_argument('--roi',nargs=4,type=int,metavar=('X','Y','W','H'))
    parser.add_argument('--roi-manifest',type=Path,help='Per-image reviewed ROI JSON, no ground-truth text')
    parser.add_argument('--save-roi',action='store_true')
    parser.add_argument('--reset-roi',action='store_true')
    parser.add_argument('--config',type=Path,default=ROOT/'config/config.json')
    parser.add_argument('--ab',required=True)
    parser.add_argument('--dm',type=int,required=True)
    parser.add_argument('--minimum-confidence',type=float,default=.8)
    parser.add_argument('--max-angle-deviation',type=float,default=3.0,
                        help='Maximum DATE-to-reference-line angle difference in degrees')
    parser.add_argument('--expected-date',type=date.fromisoformat,help='YYYY-MM-DD; enables NSX comparison')
    parser.add_argument('--model-directory',type=str)
    parser.add_argument('--output',type=Path,default=ROOT/'data/debug')
    args=parser.parse_args()
    try:
        expected=Expected(args.ab,args.dm,args.minimum_confidence,
                          args.expected_date is not None,args.expected_date,
                          args.max_angle_deviation)
    except ValueError as exc:
        parser.error(str(exc))
    if (args.select_roi or args.save_roi or args.roi) and args.folder:
        parser.error('Select/save/pixel ROI requires --image; use saved config or a manifest for folders')
    if args.select_roi and args.roi:
        parser.error('Use either --select-roi or --roi')
    config=json.loads(args.config.read_text(encoding='utf-8')) if args.config.exists() else {'roi_mode':'fixed','fixed_roi':None}
    if args.reset_roi:
        config['fixed_roi']=None
        write_json(args.config,config)
    manifest=json.loads(args.roi_manifest.read_text(encoding='utf-8')) if args.roi_manifest else None
    paths=[args.image] if args.image else sorted(p for p in args.folder.iterdir() if p.suffix.lower() in EXTENSIONS)
    if not paths:
        parser.error('No supported images found')
    output=args.output/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    engine=EasyOCREngine(args.model_directory)
    records=[]
    for index,path in enumerate(paths):
        folder=output/f'{index+1:03d}_{path.stem}'; folder.mkdir()
        try:
            image=load_image(path)
            roi_data=manifest.get(path.name) if manifest is not None else config.get('fixed_roi')
            roi=FixedROI.from_dict(roi_data) if roi_data else None
            if args.roi:
                roi=FixedROI(*args.roi,image.shape[1],image.shape[0])
            if args.select_roi:
                scale=min(1.,1200/image.shape[1],800/image.shape[0])
                preview=cv2.resize(image,None,fx=scale,fy=scale)
                picked=cv2.selectROI('Select DATE ROI - Enter confirms, C cancels',preview,False,False)
                cv2.destroyAllWindows()
                if picked[2]<=0 or picked[3]<=0:
                    raise ValueError('ROI selection cancelled')
                x,y,w,h=picked
                left=round(x*image.shape[1]/preview.shape[1]); top=round(y*image.shape[0]/preview.shape[0])
                right=round((x+w)*image.shape[1]/preview.shape[1]); bottom=round((y+h)*image.shape[0]/preview.shape[0])
                roi=FixedROI(left,top,right-left,bottom-top,image.shape[1],image.shape[0])
            if args.save_roi:
                if roi is None:
                    raise ValueError('No ROI to save')
                config['fixed_roi']=roi.to_dict()
                write_json(args.config,config)
            decision_started=time.perf_counter()
            record,artifacts=inspect_image(image,roi,engine,expected)
            alignment_started=time.perf_counter()
            if roi is not None and record.get('metrics',{}).get('angle') is not None:
                alignment_image,alignment_height=fixed_alignment_region(image,roi)
                try:
                    alignment,alignment_overlay=inspect_alignment(alignment_image,
                        record['metrics']['angle'],alignment_height,
                        expected.maximum_angle_deviation)
                    record['alignment']=alignment;artifacts['alignment']=alignment_overlay
                    if not alignment['passed'] and record['validation']['result']=='PASS':
                        record['validation'].update(result='FAIL_DATE_ALIGNMENT',
                            error_reason=(f'Độ lệch DATE {alignment["deviation"]:.2f}° vượt '
                                          f'giới hạn {alignment["maximum_deviation"]:.2f}°'))
                except AlignmentError as exc:
                    record['alignment']=dict(found=False,passed=False,
                        maximum_deviation=expected.maximum_angle_deviation,error_reason=str(exc))
                    artifacts['alignment']=alignment_image
                    if record['validation']['result']=='PASS':
                        record['validation'].update(result='FAIL_REFERENCE_LINE_NOT_FOUND',
                                                    error_reason=str(exc))
            record['stage_ms']['alignment']=round((time.perf_counter()-alignment_started)*1000,2)
            record['processing_ms']=round((time.perf_counter()-decision_started)*1000,2)
            record['deadline_met']=record['processing_ms']<=record['deadline_ms']
            if not record['deadline_met'] and record['validation']['result']=='PASS':
                record['validation'].update(result='FAIL_DEADLINE',
                    error_reason=f'Vượt thời hạn {record["deadline_ms"]} ms')
            overlay=image.copy()
            if roi:
                x,y,w,h=roi.bounds(image.shape)
                cv2.rectangle(overlay,(x,y),(x+w,y+h),(0,200,0),3)
            artifacts={'original':image,'overlay':overlay,**artifacts}
            if 'field_boxes' in record:
                field_overlay=cv2.cvtColor(artifacts['gray'],cv2.COLOR_GRAY2BGR)
                for name,(l,t,r,b) in record['field_boxes'].items():
                    cv2.rectangle(field_overlay,(l,t),(r,b),(0,180,0),1)
                    cv2.putText(field_overlay,name,(l,max(t,12)),cv2.FONT_HERSHEY_SIMPLEX,.35,(0,0,200),1)
                artifacts['fields']=field_overlay
            for name,array in artifacts.items():
                save_image(folder/f'{name}.png',array)
            record.update(image=path.name,source=str(path.resolve()),artifacts=list(artifacts))
        except Exception as exc:
            import traceback
            record={'image':path.name,'source':str(path.resolve()),'artifacts':[], 'processing_ms':0,
                    'traceback':traceback.format_exc(),'validation':{'result':'FAIL_OCR','raw':'','parsed':None,
                    'confidence':{},'overall_confidence':None,'error_reason':str(exc)}}
        write_json(folder/'result.json',record)
        records.append(record)
        print(f"{path.name}: {record['validation']['result']} confidence={record['validation'].get('overall_confidence')}",flush=True)
    save_report(output,records)
    print(f'Report: {output / "report.html"}')
    return 0 if all(r['validation']['result']=='PASS' for r in records) else 1


if __name__=='__main__':
    raise SystemExit(main())
