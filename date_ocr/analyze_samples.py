"""Exploratory measurement only; reviewed ROI coordinates are in display scale."""
from pathlib import Path
import json
import cv2
import numpy as np

BOXES = [(530,805,485,185), (565,730,485,180), (600,810,465,165),
         (580,750,470,170), (700,605,500,180)]


def runs(mask):
    indices = np.flatnonzero(mask)
    if not len(indices):
        return []
    return [(int(g[0]), int(g[-1])+1) for g in np.split(indices, np.where(np.diff(indices)>1)[0]+1)]


def prepare(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    h,w = gray.shape
    best = None
    for angle in np.arange(-10,10.01,.25):
        matrix = cv2.getRotationMatrix2D((w/2,h/2),float(angle),1)
        mask = cv2.warpAffine(255-binary,matrix,(w,h))
        score = float(np.square(np.sum(mask/255, axis=1)).sum())
        if best is None or score > best[0]:
            best = (score,float(angle),matrix)
    corrected = cv2.warpAffine(gray,best[2],(w,h),borderValue=255)
    _, binary = cv2.threshold(corrected,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    rows = runs(np.sum(binary<128,axis=1)>w*.035)
    rows = [(a,b) for a,b in rows if b-a>15]
    return corrected,binary,best[1],rows


def split_fields(binary, rows):
    fields = {}
    for row, (a,b) in enumerate(rows):
        spans = runs(np.sum(binary[a:b]<128,axis=0)>3)
        groups = []
        for left,right in spans:
            if groups and left-groups[-1][1] < (b-a)*.45:
                groups[-1][1]=right
            else:
                groups.append([left,right])
        names = ['nsx_anchor','date','dm_anchor','dm'] if row==0 else ['ab','time']
        if len(groups)!=len(names):
            raise ValueError(f'Unexpected field structure: {groups}')
        for name,(left,right) in zip(names,groups):
            fields[name]=(max(0,left-8),max(0,a-8),min(binary.shape[1],right+8),min(binary.shape[0],b+8))
    return fields


if __name__ == '__main__':
    import easyocr
    import torch
    torch.set_num_threads(4)
    reader = easyocr.Reader(['en'],gpu=False,detector=False,download_enabled=False)
    out = Path('data/debug/exploration'); out.mkdir(parents=True,exist_ok=True)
    result = []
    for i,box in enumerate(BOXES,1):
        image=cv2.imread(f'C:/Users/admin/Downloads/date{i}.jpg')
        x,y,w,h = [round(v*1926/1368) for v in box]
        roi=image[y:y+h,x:x+w]
        gray,binary,angle,rows=prepare(roi)
        cv2.imwrite(str(out/f'date{i}_roi.png'),roi)
        cv2.imwrite(str(out/f'date{i}_binary.png'),binary)
        record=dict(image=f'date{i}.jpg',bbox=[x,y,w,h],angle=angle,rows=rows,ocr=[])
        fields=split_fields(binary,rows)
        for variant,array in [('gray',gray),('otsu',binary)]:
            for n,(a,b) in enumerate(rows):
                line=array[max(0,a-8):b+8]
                response=reader.recognize(line,detail=1,allowlist='NSXDM0123456789: ',paragraph=False)
                record['ocr'].append(dict(variant=variant,line=n,result=[(r[1],float(r[2])) for r in response]))
            for name,(l,t,r,b) in fields.items():
                allowed='ABCDEFGHIJKLMNOPQRSTUVWXYZ' if 'anchor' in name else ('0123456789:' if name=='time' else '0123456789')
                response=reader.recognize(array[t:b,l:r],detail=1,allowlist=allowed,paragraph=False)
                record['ocr'].append(dict(variant=variant,field=name,result=[(r[1],float(r[2])) for r in response]))
        print(json.dumps(record),flush=True)
        result.append(record)
    (out/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
