"""Fast, measured preprocessing for the configured two-line inkjet layout."""
import cv2
import numpy as np


class LayoutError(ValueError):
    pass


def runs(mask):
    indices=np.flatnonzero(mask)
    if not len(indices):
        return []
    return [(int(g[0]),int(g[-1])+1) for g in
            np.split(indices,np.where(np.diff(indices)>1)[0]+1)]


def estimate_skew(binary):
    """Estimate the inkjet baseline from long text components in about 1 ms.

    Closing only joins neighbouring dots horizontally.  It avoids the previous
    81 full-resolution rotations, which cost almost the entire 100 ms budget.
    """
    h,w=binary.shape
    joined=cv2.morphologyEx(255-binary,cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT,(25,3)))
    contours,_=cv2.findContours(joined,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    angles=[]
    for contour in contours:
        x,y,width,height=cv2.boundingRect(contour)
        if width<=w*.25 or not (10<height<h*.50) or y<=2 or y+height>=h-2:
            continue
        (_, _),(rw,rh),angle=cv2.minAreaRect(contour)
        if rw<rh:
            angle-=90
        if -10<=angle<=10:
            angles.append((float(angle),width))
    if not angles:
        raise LayoutError('Cannot estimate DATE text angle')
    return float(np.average([angle for angle,_ in angles],
                            weights=[width for _,width in angles]))


def _field_groups(components,expected_count,row_height,image_width):
    """Split a text row at its largest whitespace gaps.

    Components are used only to locate the fixed fields.  The OCR engine still
    has to read every field with sufficient confidence, so missing or damaged
    print cannot be turned into a PASS by this geometry step.
    """
    intervals=[]
    for component in sorted(components,key=lambda item:item['x']):
        left=component['x'];right=left+component['width']
        if intervals and left<=intervals[-1][1]+2:
            intervals[-1][1]=max(intervals[-1][1],right)
        else:
            intervals.append([left,right])
    if len(intervals)<expected_count:
        raise LayoutError(f'Expected {expected_count} fields, found fewer text groups')
    gaps=[(intervals[index+1][0]-intervals[index][1],index)
          for index in range(len(intervals)-1)]
    split_count=expected_count-1
    selected=sorted(gaps,reverse=True)[:split_count]
    minimum_gap=max(4,int(round(row_height*.12)),int(round(image_width*.006)))
    if len(selected)!=split_count or any(gap<minimum_gap for gap,_ in selected):
        raise LayoutError(f'Cannot separate {expected_count} DATE fields safely')
    split_after={index for _,index in selected}
    groups=[];start=intervals[0][0]
    for index,(_,right) in enumerate(intervals):
        if index in split_after:
            groups.append([start,right])
            start=intervals[index+1][0]
    groups.append([start,intervals[-1][1]])
    minimum_width=max(5,int(round(image_width*.015)))
    if len(groups)!=expected_count or any(right-left<minimum_width for left,right in groups):
        raise LayoutError(f'Invalid geometry for {expected_count} DATE fields')
    return groups


def _local_ink_layout(corrected):
    """Locate inkjet text under glare, shadows and printed background art."""
    h,w=corrected.shape
    kernel_size=max(15,int(round(h*.075)))
    if kernel_size%2==0:
        kernel_size+=1
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(kernel_size,kernel_size))
    blackhat=cv2.morphologyEx(corrected,cv2.MORPH_BLACKHAT,kernel)
    _,ink=cv2.threshold(blackhat,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)

    count,_,stats,centroids=cv2.connectedComponentsWithStats(ink,8)
    minimum_height=max(8,int(round(h*.08)))
    components=[]
    for index in range(1,count):
        x,y,width,height,area=(int(value) for value in stats[index])
        if width<2 or height<minimum_height or area<20:
            continue
        if x<=2 or y<=2 or x+width>=w-2 or y+height>=h-2:
            continue
        components.append(dict(x=x,y=y,width=width,height=height,
                               cx=float(centroids[index][0]),cy=float(centroids[index][1])))
    if len(components)<6:
        raise LayoutError('Not enough DATE ink components')

    ordered=sorted(components,key=lambda item:item['cy'])
    center_gaps=[ordered[index+1]['cy']-ordered[index]['cy']
                 for index in range(len(ordered)-1)]
    split_index=int(np.argmax(center_gaps))+1
    median_height=float(np.median([item['height'] for item in components]))
    if (not center_gaps or center_gaps[split_index-1]<max(4,median_height*.25)
            or split_index<2 or len(ordered)-split_index<2):
        raise LayoutError('Cannot separate the two DATE lines safely')
    rows_components=[ordered[:split_index],ordered[split_index:]]
    rows_components.sort(key=lambda row:np.mean([item['cy'] for item in row]))

    fields={};rows=[]
    row_names=(('nsx_anchor','date','dm_anchor','dm'),('ab','time'))
    for components_in_row,names in zip(rows_components,row_names):
        top=min(item['y'] for item in components_in_row)
        bottom=max(item['y']+item['height'] for item in components_in_row)
        row_height=bottom-top
        if row_height<=15 or row_height>=h*.55:
            raise LayoutError('Invalid DATE line height')
        groups=_field_groups(components_in_row,len(names),row_height,w)
        for name,(left,right) in zip(names,groups):
            if min(left,top,w-right,h-bottom)<4:
                raise LayoutError('Text touches ROI border; select a wider ROI')
            fields[name]=(max(0,left-8),max(0,top-8),min(w,right+8),min(h,bottom+8))
        rows.append((top,bottom))
    if rows[1][0]<=rows[0][0] or rows[1][0]-rows[0][1]<-median_height*.25:
        raise LayoutError('DATE lines overlap unexpectedly')
    binary=np.where(ink>0,0,255).astype(np.uint8)
    return binary,fields,rows


def _global_threshold_layout(corrected):
    """Return the legacy layout when global thresholding is structurally clean.

    Uniform handheld samples are read best from corrected gray.  Camera frames
    with glare or the dark printed arrow fail this inexpensive structural
    check and use adaptive thresholding instead.
    """
    h,w=corrected.shape
    _,binary=cv2.threshold(corrected,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    rows=[(top,bottom) for top,bottom in
          runs(np.sum(binary<128,axis=1)>w*.035) if bottom-top>15]
    if len(rows)!=2:
        return None
    fields={}
    row_names=(('nsx_anchor','date','dm_anchor','dm'),('ab','time'))
    for (top,bottom),names in zip(rows,row_names):
        spans=runs(np.sum(binary[top:bottom]<128,axis=0)>3)
        groups=[]
        for left,right in spans:
            if groups and left-groups[-1][1]<(bottom-top)*.45:
                groups[-1][1]=right
            else:
                groups.append([left,right])
        if len(groups)!=len(names):
            return None
        for name,(left,right) in zip(names,groups):
            if min(left,top,w-right,h-bottom)<4:
                return None
            fields[name]=(max(0,left-8),max(0,top-8),
                          min(w,right+8),min(h,bottom+8))
    return binary,fields,rows


def preprocess(image):
    if image is None or image.size==0 or min(image.shape[:2])<32:
        raise LayoutError('Empty or too small ROI')
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY) if image.ndim==3 else image.copy()
    _,binary=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    h,w=gray.shape
    angle=estimate_skew(binary)
    matrix=cv2.getRotationMatrix2D((w/2,h/2),angle,1)
    corrected=cv2.warpAffine(gray,matrix,(w,h),borderValue=255)
    global_layout=_global_threshold_layout(corrected)
    global_layout_clean=global_layout is not None
    if global_layout_clean:
        otsu,fields,rows=global_layout
    else:
        otsu,fields,rows=_local_ink_layout(corrected)
    clahe=cv2.createCLAHE(clipLimit=2.0,tileGridSize=(8,8)).apply(corrected)
    adaptive=cv2.adaptiveThreshold(clahe,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY,31,11)
    # Debug candidate only; not used to join characters in the selected pipeline.
    morphology=cv2.morphologyEx(otsu,cv2.MORPH_CLOSE,np.ones((2,2),np.uint8))
    return dict(gray=corrected,otsu=otsu,clahe=clahe,adaptive=adaptive,
                morphology=morphology),fields,dict(angle=angle,rows=rows,
                character_heights=[b-a for a,b in rows],line_gap=rows[1][0]-rows[0][1],
                numeric_variant='gray' if global_layout_clean else 'adaptive',
                dm_variant='gray' if global_layout_clean else 'otsu',
                uneven_illumination=not global_layout_clean)
