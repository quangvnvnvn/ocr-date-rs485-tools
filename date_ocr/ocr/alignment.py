"""Measure DATE print angle against the edge of the dark band below it."""
from __future__ import annotations

import math

import cv2
import numpy as np


class AlignmentError(ValueError):
    pass


def _parallel_dark_coverage(mask,line,offset):
    """Measure how much of a line parallel to an edge lies inside dark ink."""
    x1,y1,x2,y2=line
    count=max(32,min(256,abs(x2-x1)+1))
    xs=np.linspace(x1,x2,count)
    ys=np.linspace(y1,y2,count)+offset
    xs=np.clip(np.rint(xs).astype(np.int32),0,mask.shape[1]-1)
    ys=np.clip(np.rint(ys).astype(np.int32),0,mask.shape[0]-1)
    return float(np.mean(mask[ys,xs]>0))


def _fit_dark_band_transition(zone,dark_mask,reference_height):
    """Deterministically fit the upper edge of a broad dark band."""
    height,width=zone.shape
    offset=max(5,round(reference_height*.035))
    if height<=4*offset:
        return None
    coverage=np.mean(dark_mask>0,axis=1)
    scores=[]
    for y in range(2*offset,height-2*offset):
        above=max(coverage[y-offset],coverage[y-2*offset])
        below=max(coverage[y+offset],coverage[y+2*offset])
        scores.append((float(below-above),float(below),y))
    contrast,dark_below,center=max(scores)
    if dark_below<.45 or contrast<.18:
        return None

    radius=max(12,round(reference_height*.11))
    low=max(1,center-radius);high=min(height-1,center+radius)
    if high-low<8:
        return None
    downward=-cv2.Sobel(zone,cv2.CV_32F,0,1,ksize=3)
    xs=np.arange(width,dtype=np.int32)
    ys=np.argmax(downward[low:high],axis=0).astype(np.int32)+low
    strengths=downward[ys,xs]
    keep=strengths>=8.
    x=xs[keep].astype(np.float64);y=ys[keep].astype(np.float64)
    if len(x)<width*.35 or x.max()-x.min()<width*.45:
        return None
    for _ in range(2):
        slope,intercept=np.polyfit(x,y,1)
        residual=np.abs(y-(slope*x+intercept))
        inliers=residual<=max(3.,float(np.percentile(residual,70)))
        if int(inliers.sum())<width*.30:
            return None
        x=x[inliers];y=y[inliers]
    slope,intercept=np.polyfit(x,y,1)
    angle=math.degrees(math.atan(float(slope)))
    if abs(angle)>15:
        return None
    x1=int(round(x.min()));x2=int(round(x.max()))
    y1=int(round(slope*x1+intercept));y2=int(round(slope*x2+intercept))
    line=(x1,y1,x2,y2)
    above=max(_parallel_dark_coverage(dark_mask,line,-offset),
              _parallel_dark_coverage(dark_mask,line,-2*offset))
    below=max(_parallel_dark_coverage(dark_mask,line,offset),
              _parallel_dark_coverage(dark_mask,line,2*offset))
    dark_side=max(above,below);final_contrast=dark_side-min(above,below)
    if below<.45 or dark_side<.45 or final_contrast<.18:
        return None
    length=math.hypot(x2-x1,y2-y1)
    weight=length*(.5+dark_side+final_contrast)
    return (float(angle),float(weight),line,float(length),dark_side,final_contrast)


def detect_reference_line(image,reference_height):
    """Find an edge whose one side is the wide dark band below DATE.

    Requiring a broad dark side prevents package borders, text baselines and
    thin illustration lines from being accepted as the reference.
    """
    if image is None or image.size==0:
        raise AlignmentError('Ảnh kiểm tra dải tối bị rỗng')
    if not isinstance(reference_height,(int,float)) or reference_height<=0:
        raise AlignmentError('Chiều cao vùng DATE không hợp lệ')
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY) if image.ndim==3 else image
    height,width=gray.shape
    # The AUTO-ROI alignment image extends roughly one DATE-height below the
    # OCR crop.  Start near the band itself so dark package artwork above it
    # cannot dominate Otsu thresholding; either wide band edge has the same
    # reference angle.
    top=max(0,round(reference_height*1.30))
    bottom=min(height,round(reference_height*2.10))
    if bottom-top<32:
        raise AlignmentError('Ảnh không chứa đủ đoạn thẳng phía dưới DATE')
    zone=cv2.GaussianBlur(gray[top:bottom],(5,5),0)
    _,dark_mask=cv2.threshold(zone,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    candidates=[];sample_offset=max(5,round(reference_height*.035))
    transition=_fit_dark_band_transition(zone,dark_mask,reference_height)
    if transition is not None:
        angle,weight,line,length,darkness,contrast=transition
        x1,y1,x2,y2=line
        candidates.append((angle,weight,(x1,y1+top,x2,y2+top),
                           length,darkness,contrast))
        edges=np.zeros_like(zone)
    else:
        # Hough is a fallback for older images where the band is only partly
        # visible.  The deterministic gradient fit above is faster and avoids
        # scheduling/RNG variation on the normal industrial-camera path.
        edges=cv2.Canny(zone,8,30)
        cv2.setRNGSeed(0)
        lines=cv2.HoughLinesP(edges,1,np.pi/720,
                              threshold=max(25,round(width*.04)),
                              minLineLength=max(60,round(width*.18)),maxLineGap=70)
        if lines is not None:
            for x1,y1,x2,y2 in lines[:,0]:
                if x2<x1:
                    x1,y1,x2,y2=x2,y2,x1,y1
                angle=math.degrees(math.atan2(y2-y1,x2-x1))
                length=math.hypot(x2-x1,y2-y1)
                if abs(angle)<=15:
                    local_line=(int(x1),int(y1),int(x2),int(y2))
                    above=max(_parallel_dark_coverage(dark_mask,local_line,-sample_offset),
                              _parallel_dark_coverage(dark_mask,local_line,-2*sample_offset))
                    below=max(_parallel_dark_coverage(dark_mask,local_line,sample_offset),
                              _parallel_dark_coverage(dark_mask,local_line,2*sample_offset))
                    dark_side=max(above,below);contrast=dark_side-min(above,below)
                    if dark_side>=.45 and contrast>=.18:
                        weight=length*(.5+dark_side+contrast)
                        candidates.append((float(angle),float(weight),
                            (int(x1),int(y1+top),int(x2),int(y2+top)),
                            float(length),dark_side,contrast))
    if not candidates:
        raise AlignmentError('Không tìm thấy mép dải tối phía dưới DATE')
    ordered=sorted(candidates,key=lambda item:item[0])
    halfway=sum(item[1] for item in ordered)/2;accumulated=0.;selected=ordered[-1]
    for item in ordered:
        accumulated+=item[1]
        if accumulated>=halfway:
            selected=item;break
    angle=selected[0]
    near=[item for item in candidates if abs(item[0]-angle)<=.5]
    representative=max(near,key=lambda item:item[3])[2]
    longest=max(item[3] for item in near);darkness=max(item[4] for item in near)
    confidence=min(1.,.7*longest/max(1.,width)+.3*darkness)
    return float(angle),representative,float(confidence),edges


def inspect_alignment(image,date_angle,reference_height,maximum_deviation):
    """Return a fail-closed angle measurement plus a visual debug overlay."""
    for value,label in ((date_angle,'góc DATE'),
                        (maximum_deviation,'độ lệch tối đa')):
        if type(value) not in (int,float) or not math.isfinite(value):
            raise AlignmentError(f'{label} không hợp lệ')
    if not 0<maximum_deviation<=45:
        raise AlignmentError('Độ lệch tối đa phải trong khoảng (0, 45] độ')
    line_angle,line,confidence,_=detect_reference_line(image,reference_height)
    deviation=abs(float(date_angle)-line_angle)
    passed=deviation<=maximum_deviation
    overlay=(cv2.cvtColor(image,cv2.COLOR_GRAY2BGR) if image.ndim==2 else image.copy())
    color=(30,170,40) if passed else (30,30,220)
    cv2.line(overlay,line[:2],line[2:],color,4,cv2.LINE_AA)
    width=overlay.shape[1]
    left=round(width*.08);right=round(width*.48);middle_y=round(reference_height*.42)
    delta=math.tan(math.radians(float(date_angle)))*(right-left)
    cv2.line(overlay,(left,round(middle_y-delta/2)),
             (right,round(middle_y+delta/2)),(230,130,20),3,cv2.LINE_AA)
    text=(f'DATE {date_angle:+.2f} deg | LINE {line_angle:+.2f} deg | '
          f'DEV {deviation:.2f}/{maximum_deviation:.2f} deg | '
          f'{"PASS" if passed else "FAIL"}')
    cv2.rectangle(overlay,(0,0),(width,min(34,overlay.shape[0]-1)),(245,245,245),-1)
    cv2.putText(overlay,text,(8,24),cv2.FONT_HERSHEY_SIMPLEX,.58,color,2,cv2.LINE_AA)
    measurement=dict(found=True,date_angle=round(float(date_angle),3),
        reference_angle=round(line_angle,3),deviation=round(deviation,3),
        maximum_deviation=round(float(maximum_deviation),3),
        reference_type='dark_band_edge_below_date',
        reference_confidence=round(confidence,4),passed=passed,
        reference_line=list(line))
    return measurement,overlay


def fixed_alignment_region(image,roi,bottom_extension=1.05):
    """Extend a manual DATE ROI downward without changing its coordinate frame."""
    x,y,width,height=roi.bounds(image.shape)
    bottom=min(image.shape[0],y+round(height*(1+bottom_extension)))
    if bottom<=y:
        raise AlignmentError('Vùng kiểm tra góc nằm ngoài ảnh')
    return image[y:bottom,x:x+width],height
