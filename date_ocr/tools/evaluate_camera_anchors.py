"""Measure fixed-anchor HOG similarity across saved camera ROIs."""
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
from ocr.preprocessing import preprocess


HOG=cv2.HOGDescriptor((128,64),(16,16),(8,8),(8,8),9)


def feature(image):
    _,binary=cv2.threshold(image,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    ys,xs=np.nonzero(binary)
    glyph=binary[ys.min():ys.max()+1,xs.min():xs.max()+1]
    scale=min(120/glyph.shape[1],56/glyph.shape[0])
    glyph=cv2.resize(glyph,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
    canvas=np.zeros((64,128),np.uint8)
    top=(64-glyph.shape[0])//2;left=(128-glyph.shape[1])//2
    canvas[top:top+glyph.shape[0],left:left+glyph.shape[1]]=glyph
    value=HOG.compute(canvas).ravel();length=float(np.linalg.norm(value))
    return value/length


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('capture_directory',type=Path)
    parser.add_argument('--write-calibration',type=Path)
    args=parser.parse_args()
    paths=[folder/'roi.png' for folder in sorted(args.capture_directory.iterdir())
           if folder.is_dir() and (folder/'roi.png').exists()]
    values=[]
    selected_indices=(0,35,37)
    if args.write_calibration:
        args.write_calibration.mkdir(parents=True,exist_ok=True)
    for index,path in enumerate(paths):
        variants,fields,_=preprocess(load_image(path))
        row={}
        for name in ('nsx_anchor','dm_anchor'):
            left,top,right,bottom=fields[name]
            crop=variants['otsu'][top:bottom,left:right]
            row[name]=feature(crop)
            if args.write_calibration and index in selected_indices:
                target=args.write_calibration/f'hikrobot_{index+1:03d}_{name}.png'
                ok,encoded=cv2.imencode('.png',crop)
                if not ok:
                    raise RuntimeError(f'Cannot encode {target}')
                encoded.tofile(str(target))
        values.append(row)
    templates=[values[index] for index in selected_indices]
    report={}
    for name in templates[0]:
        scores=[max(float(value[name]@template[name]) for template in templates)
                for value in values]
        report[name]=dict(min=min(scores),average=sum(scores)/len(scores),max=max(scores))
    print(json.dumps(report,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
