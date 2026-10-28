"""Print reference-band Hough candidates for one rectified alignment image."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import cv2

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from main import load_image
from ocr.alignment import _parallel_dark_coverage


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('image',type=Path)
    parser.add_argument('--reference-height',type=float,default=329.)
    args=parser.parse_args()
    gray=cv2.cvtColor(load_image(args.image),cv2.COLOR_BGR2GRAY)
    height,width=gray.shape
    top=max(0,round(args.reference_height*1.12))
    bottom=min(height,round(args.reference_height*1.85))
    zone=cv2.GaussianBlur(gray[top:bottom],(5,5),0)
    threshold,dark_mask=cv2.threshold(zone,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    offset=max(5,round(args.reference_height*.035));experiments=[]
    for low,high,hough_ratio,length_ratio,max_gap in (
            (35,100,.10,.35,35),(15,50,.06,.25,50),(8,30,.04,.18,70)):
        edges=cv2.Canny(zone,low,high)
        lines=cv2.HoughLinesP(edges,1,math.pi/720,
                              threshold=max(25,round(width*hough_ratio)),
                              minLineLength=max(60,round(width*length_ratio)),
                              maxLineGap=max_gap)
        rows=[]
        for raw in ([] if lines is None else lines[:,0]):
            x1,y1,x2,y2=(int(value) for value in raw)
            if x2<x1:x1,y1,x2,y2=x2,y2,x1,y1
            angle=math.degrees(math.atan2(y2-y1,x2-x1))
            if abs(angle)>15:continue
            line=(x1,y1,x2,y2)
            above=max(_parallel_dark_coverage(dark_mask,line,-offset),
                      _parallel_dark_coverage(dark_mask,line,-2*offset))
            below=max(_parallel_dark_coverage(dark_mask,line,offset),
                      _parallel_dark_coverage(dark_mask,line,2*offset))
            rows.append(dict(line=line,angle=angle,length=math.hypot(x2-x1,y2-y1),
                             above=above,below=below,dark_side=max(above,below),
                             contrast=max(above,below)-min(above,below)))
        rows.sort(key=lambda row:row['length'],reverse=True)
        experiments.append(dict(settings=[low,high,hough_ratio,length_ratio,max_gap],
                                edge_pixels=int((edges>0).sum()),candidates=rows[:12]))
    print(json.dumps(dict(shape=[height,width],zone=[top,bottom],otsu=threshold,
                          experiments=experiments),indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
