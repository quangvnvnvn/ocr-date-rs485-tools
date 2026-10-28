"""Lightweight product-feature based DATE ROI detection (no full-label OCR)."""
from dataclasses import dataclass
from pathlib import Path
import cv2
import numpy as np
from .fixed_roi import FixedROI


@dataclass(frozen=True)
class AutoROIResult:
    found: bool
    bbox: tuple | None
    confidence: float
    inliers: int
    message: str
    quad: tuple | None = None


class AutoROIDetector:
    """Locate the configured DATE ROI by matching stable package artwork.

    The detector never OCRs the full label. ORB matches product features against a
    reviewed reference image, then a RANSAC homography projects the saved DATE box.
    """
    def __init__(self, reference_image, reference_roi, *, min_inliers=35,
                 min_inlier_ratio=.20, ratio_test=.72, working_max_dimension=600):
        self.reference_path=Path(reference_image)
        self.reference_roi=reference_roi
        self.min_inliers=min_inliers
        self.min_inlier_ratio=min_inlier_ratio
        self.ratio_test=ratio_test
        self.working_max_dimension=working_max_dimension
        try:
            reference=cv2.imdecode(np.fromfile(str(self.reference_path),dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
        except (OSError,ValueError):
            reference=None
        if reference is None:
            raise ValueError(f'Cannot load AUTO ROI reference: {self.reference_path}')
        self.reference_shape=reference.shape
        self.reference_scale=min(1.,working_max_dimension/max(reference.shape))
        reference_small=cv2.resize(reference,None,fx=self.reference_scale,
                                   fy=self.reference_scale,interpolation=cv2.INTER_AREA)
        self.orb=cv2.ORB_create(nfeatures=1000,scaleFactor=1.2,nlevels=8,
                                edgeThreshold=19,fastThreshold=12)
        self.reference_keypoints,self.reference_descriptors=self.orb.detectAndCompute(reference_small,None)
        if self.reference_descriptors is None or len(self.reference_keypoints)<self.min_inliers:
            raise ValueError('AUTO ROI reference has too few product features')
        self.matcher=cv2.BFMatcher(cv2.NORM_HAMMING)

    def detect(self,image):
        if image is None or image.size==0:
            return AutoROIResult(False,None,0.,0,'Ảnh rỗng')
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY) if image.ndim==3 else image
        target_scale=min(1.,self.working_max_dimension/max(gray.shape))
        working=cv2.resize(gray,None,fx=target_scale,fy=target_scale,
                           interpolation=cv2.INTER_AREA)
        keypoints,descriptors=self.orb.detectAndCompute(working,None)
        if descriptors is None or len(keypoints)<self.min_inliers:
            return AutoROIResult(False,None,0.,0,'Không đủ đặc trưng sản phẩm')
        pairs=self.matcher.knnMatch(self.reference_descriptors,descriptors,k=2)
        good=[first for first,second in pairs if first.distance<self.ratio_test*second.distance]
        if len(good)<self.min_inliers:
            return AutoROIResult(False,None,min(1.,len(good)/self.min_inliers),0,
                                 'Không khớp đúng mẫu bao bì')
        src=np.float32([self.reference_keypoints[m.queryIdx].pt for m in good])
        dst=np.float32([keypoints[m.trainIdx].pt for m in good])
        matrix,mask=cv2.findHomography(src,dst,cv2.RANSAC,3.0)
        if matrix is None or mask is None:
            return AutoROIResult(False,None,0.,0,'Không ước lượng được vị trí sản phẩm')
        inliers=int(mask.sum());ratio=inliers/len(good)
        if inliers<self.min_inliers or ratio<self.min_inlier_ratio:
            confidence=min(inliers/self.min_inliers,ratio/self.min_inlier_ratio)*.5
            return AutoROIResult(False,None,float(confidence),inliers,'Độ tin cậy vị trí quá thấp')
        x,y,w,h=(self.reference_roi.x,self.reference_roi.y,
                 self.reference_roi.width,self.reference_roi.height)
        scale=self.reference_scale
        corners=np.float32([[[x*scale,y*scale],[(x+w)*scale,y*scale],
                             [(x+w)*scale,(y+h)*scale],[x*scale,(y+h)*scale]]])
        projected=cv2.perspectiveTransform(corners,matrix)[0]/target_scale
        if not np.isfinite(projected).all():
            return AutoROIResult(False,None,0.,inliers,'Tọa độ AUTO ROI không hợp lệ')
        # Reject mirrored, extremely distorted or implausibly sized projections.
        area=abs(float(cv2.contourArea(projected)))
        image_area=float(gray.shape[0]*gray.shape[1])
        widths=[np.linalg.norm(projected[(i+1)%4]-projected[i]) for i in (0,2)]
        heights=[np.linalg.norm(projected[(i+1)%4]-projected[i]) for i in (1,3)]
        aspect=(sum(widths)/2)/max(1.,sum(heights)/2)
        if not (.015<=area/image_area<=.12 and 1.7<=aspect<=4.5):
            return AutoROIResult(False,None,0.,inliers,'Hình học vùng DATE không phù hợp')
        xx,yy,ww,hh=cv2.boundingRect(projected)
        margin_x=round(ww*.03);margin_y=round(hh*.05)
        xx=max(0,xx-margin_x);yy=max(0,yy-margin_y)
        right=min(gray.shape[1],xx+ww+2*margin_x)
        bottom=min(gray.shape[0],yy+hh+2*margin_y)
        ww=right-xx;hh=bottom-yy
        try:
            FixedROI(xx,yy,ww,hh,gray.shape[1],gray.shape[0])
        except ValueError as exc:
            return AutoROIResult(False,None,0.,inliers,str(exc))
        match_score=min(1.,inliers/180.)
        ratio_score=min(1.,ratio/.55)
        confidence=float(round(.65*match_score+.35*ratio_score,4))
        quad=tuple(tuple(float(value) for value in point) for point in projected)
        return AutoROIResult(True,(xx,yy,ww,hh),confidence,inliers,
                             'Đã bám đúng mẫu bao bì',quad)

    def rectify(self,image,result,margin_left=.03,margin_right=.10,
                margin_top=.06,margin_bottom=.06):
        """Warp a detected DATE quadrilateral into the calibrated orientation.

        The DATE value is close to the right edge of the saved reference ROI, so
        that side deliberately gets more tolerance.  Keeping the left/bottom
        margins small prevents neighbouring package copy from entering the two
        inkjet lines.
        """
        if not result.found or result.quad is None:
            raise ValueError('AUTO ROI result cannot be rectified')
        quad=np.float32(result.quad)
        tl,tr,br,bl=quad
        source=np.float32([
            tl-margin_left*(tr-tl)-margin_top*(bl-tl),
            tr+margin_right*(tr-tl)-margin_top*(br-tr),
            br+margin_right*(br-bl)+margin_bottom*(br-tr),
            bl-margin_left*(br-bl)+margin_bottom*(bl-tl),
        ])
        width=round(self.reference_roi.width*(1+margin_left+margin_right))
        height=round(self.reference_roi.height*(1+margin_top+margin_bottom))
        destination=np.float32([[0,0],[width-1,0],[width-1,height-1],[0,height-1]])
        matrix=cv2.getPerspectiveTransform(source,destination)
        return cv2.warpPerspective(image,matrix,(width,height),
                                   flags=cv2.INTER_LINEAR,borderValue=(255,255,255))
