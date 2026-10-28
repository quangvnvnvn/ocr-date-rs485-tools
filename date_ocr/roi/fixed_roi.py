from dataclasses import dataclass, asdict
import math


@dataclass(frozen=True)
class FixedROI:
    x: int
    y: int
    width: int
    height: int
    source_width: int
    source_height: int

    def __post_init__(self):
        values = (self.x,self.y,self.width,self.height,self.source_width,self.source_height)
        if any(type(v) is not int for v in values):
            raise ValueError('ROI values must be integer pixels')
        if min(self.x,self.y)<0 or min(values[2:])<=0:
            raise ValueError('ROI dimensions must be positive')
        if self.x+self.width>self.source_width or self.y+self.height>self.source_height:
            raise ValueError('ROI outside source image')

    def to_dict(self):
        return {**asdict(self), 'x_ratio':self.x/self.source_width,
                'y_ratio':self.y/self.source_height,
                'width_ratio':self.width/self.source_width,
                'height_ratio':self.height/self.source_height}

    @classmethod
    def from_dict(cls, value):
        roi=cls(**{key:value[key] for key in cls.__dataclass_fields__})
        canonical=roi.to_dict()
        for key in ('x_ratio','y_ratio','width_ratio','height_ratio'):
            if key in value and (not math.isfinite(value[key]) or abs(value[key]-canonical[key])>1e-6):
                raise ValueError('Inconsistent normalized ROI')
        return roi

    def bounds(self, image_shape):
        h,w=image_shape[:2]
        x=round(self.x*w/self.source_width); y=round(self.y*h/self.source_height)
        right=round((self.x+self.width)*w/self.source_width)
        bottom=round((self.y+self.height)*h/self.source_height)
        if not (0<=x<right<=w and 0<=y<bottom<=h):
            raise ValueError('Scaled ROI outside image')
        return x,y,right-x,bottom-y

    def crop(self,image):
        x,y,w,h=self.bounds(image.shape)
        return image[y:y+h,x:x+w].copy()
