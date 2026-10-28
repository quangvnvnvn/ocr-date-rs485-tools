from .ocr_base import OCRBase, FieldReading
from pathlib import Path
import json
import math
import os
import re
import sys
import cv2
import numpy as np

from .preprocessing import preprocess
from roi.fixed_roi import FixedROI


def application_root():
    """Find external models/config beside the portable executable."""
    if getattr(sys,'frozen',False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


class EasyOCREngine(OCRBase):
    def __init__(self, model_directory=None):
        model_directory=Path(model_directory) if model_directory else application_root()/'data/models'
        requested_backend=os.environ.get('OCR_BACKEND','onnx').strip().lower()
        if requested_backend not in {'onnx','torch'}:
            requested_backend='onnx'
        onnx_model=model_directory/'english_g2_recognizer.onnx'
        onnx_metadata=model_directory/'english_g2_recognizer.json'
        if requested_backend=='onnx' and onnx_model.exists() and onnx_metadata.exists():
            self._init_onnx(onnx_model,onnx_metadata)
        else:
            self._init_torch(model_directory)
        self.hog=cv2.HOGDescriptor((128,64),(16,16),(8,8),(8,8),9)
        self.anchor_templates={'nsx_anchor':[],'dm_anchor':[]}
        self._load_fast_templates()

    def _init_onnx(self,model_path,metadata_path):
        vendor=application_root()/'packaging_onnx'
        if vendor.exists() and str(vendor) not in sys.path:
            sys.path.insert(0,str(vendor))
        import onnxruntime as ort
        try:requested_threads=int(os.environ.get('OCR_ONNX_THREADS','4'))
        except ValueError:requested_threads=4
        options=ort.SessionOptions()
        options.intra_op_num_threads=min(max(1,requested_threads),
                                         max(1,os.cpu_count() or 1))
        options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session=ort.InferenceSession(str(model_path),sess_options=options,
            providers=['CPUExecutionProvider'])
        metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
        self.backend='onnx'
        self.device='onnx-cpu'
        self.numeric_characters=metadata['numeric_characters']
        self.numeric_indices=np.asarray(metadata['numeric_indices'],dtype=np.int64)
        self.numeric_collates={}
        self.torch=None
        self.reader=None

    def _init_torch(self,model_directory):
        import torch
        # Keep the CPU pool small and deterministic for the 100 ms decision path.
        # The environment override is used by the local calibration benchmark.
        # Six workers match the physical cores of the validated Hikrobot host.
        # Using more logical threads occasionally caused a 100+ ms scheduler
        # spike even though the same frame normally completed in about 75 ms.
        try:requested_threads=int(os.environ.get('OCR_TORCH_THREADS','6'))
        except ValueError:requested_threads=6
        torch.set_num_threads(min(max(1,requested_threads),
                                  max(1,os.cpu_count() or 1)))
        try:torch.set_num_interop_threads(1)
        except RuntimeError:pass
        requested_device=os.environ.get('OCR_DEVICE','auto').strip().lower()
        if requested_device not in {'auto','cpu','cuda'}:
            requested_device='auto'
        use_cuda=(requested_device=='cuda' or
                  (requested_device=='auto' and torch.cuda.is_available()))
        if requested_device=='cuda' and not torch.cuda.is_available():
            raise RuntimeError('OCR_DEVICE=cuda nhưng máy không có CUDA khả dụng')
        if use_cuda:
            # Input widths vary slightly from frame to frame.  cuDNN benchmark
            # would profile each new shape inside the 100 ms decision window.
            torch.backends.cudnn.benchmark=False
            torch.backends.cuda.matmul.allow_tf32=False
            torch.backends.cudnn.allow_tf32=False
        import easyocr
        self.reader=easyocr.Reader(['en'],gpu=use_cuda,detector=False,
                                  download_enabled=False,
                                  model_storage_directory=str(model_directory),
                                  verbose=False)
        self.backend='torch'
        self.torch=torch
        self.numeric_characters=['']+list('0123456789:')
        self.device=self.reader.device
        self.numeric_indices=torch.tensor(
            [0]+[self.reader.character.index(char)+1
                 for char in self.numeric_characters[1:]],dtype=torch.long,
                 device=self.device)
        self.numeric_collates={}

    def read_field(self,image,field,decoder='greedy'):
        if self.reader is None:
            raise RuntimeError(
                'ONNX Lite chi ho tro duong doc DATE co dinh; '
                'hay dung read_fields_fast()')
        # Do not restrict DM to 123 here: a printed 5 must remain observable
        # and be rejected by the parser instead of forced to an allowed digit.
        allowed = ('ABCDEFGHIJKLMNOPQRSTUVWXYZ' if field.endswith('_anchor') else
                   '0123456789:' if field=='time' else '0123456789')
        readings=self.reader.recognize(image,detail=1,allowlist=allowed,
                                       paragraph=False,decoder=decoder,
                                       beamWidth=5,workers=0)
        if len(readings)!=1:
            return FieldReading('',0.0)
        return FieldReading(readings[0][1],float(readings[0][2]))

    def _anchor_feature(self,image):
        _,binary=cv2.threshold(image,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
        ys,xs=np.nonzero(binary)
        if not len(xs):
            return np.zeros(self.hog.getDescriptorSize(),np.float32)
        glyph=binary[ys.min():ys.max()+1,xs.min():xs.max()+1]
        scale=min(120/glyph.shape[1],56/glyph.shape[0])
        glyph=cv2.resize(glyph,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
        canvas=np.zeros((64,128),np.uint8)
        top=(64-glyph.shape[0])//2;left=(128-glyph.shape[1])//2
        canvas[top:top+glyph.shape[0],left:left+glyph.shape[1]]=glyph
        feature=self.hog.compute(canvas).ravel()
        length=float(np.linalg.norm(feature))
        return feature/length if length else feature

    def _load_fast_templates(self):
        root=application_root()
        manifest=json.loads((root/'config/sample_rois.json').read_text(encoding='utf-8'))
        warm=None
        for name,roi_data in manifest.items():
            path=root/'data/test_images'/name
            try:
                image=cv2.imdecode(np.fromfile(str(path),dtype=np.uint8),cv2.IMREAD_COLOR)
                variants,fields,metrics=preprocess(FixedROI.from_dict(roi_data).crop(image))
            except Exception:
                continue
            for field in self.anchor_templates:
                left,top,right,bottom=fields[field]
                self.anchor_templates[field].append(
                    self._anchor_feature(variants['otsu'][top:bottom,left:right]))
            if warm is None:
                warm=(variants['gray'],fields,variants[metrics['numeric_variant']],
                      variants['otsu'],variants[metrics['dm_variant']],
                      2 if metrics['numeric_variant']=='gray' else 6,
                      12 if metrics['numeric_variant']=='gray' else 18)
        calibration=root/'data/calibration'
        if calibration.exists():
            for field in self.anchor_templates:
                for path in sorted(calibration.glob(f'*_{field}.png')):
                    image=cv2.imdecode(np.fromfile(str(path),dtype=np.uint8),
                                       cv2.IMREAD_GRAYSCALE)
                    if image is not None and image.size:
                        self.anchor_templates[field].append(self._anchor_feature(image))
        if not all(self.anchor_templates.values()):
            raise ValueError('Cannot build fast NSX/DM anchor templates')
        # Pay the first model inference cost while the GUI says it is loading,
        # before the first sensor/image deadline begins.
        if warm is not None:
            self.read_fields_fast(*warm)

    @staticmethod
    def _join_fields(image,fields,names,trim_names=(),padding=0,gap=0):
        crops=[]
        for name in names:
            left,top,right,bottom=fields[name]
            crop=image[top:bottom,left:right]
            if name in trim_names:
                _,mask=cv2.threshold(crop,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
                ys,xs=np.nonzero(mask)
                if len(xs):
                    crop=crop[ys.min():ys.max()+1,xs.min():xs.max()+1]
            if padding:
                crop=cv2.copyMakeBorder(crop,padding,padding,padding,padding,
                                        cv2.BORDER_CONSTANT,value=255)
            crops.append(crop)
        height=max(crop.shape[0] for crop in crops)
        crops=[cv2.copyMakeBorder(crop,0,height-crop.shape[0],0,0,
                                  cv2.BORDER_CONSTANT,value=255) for crop in crops]
        if not gap:
            return np.hstack(crops)
        separator=np.full((height,gap),255,np.uint8)
        joined=crops[0]
        for crop in crops[1:]:
            joined=np.hstack((joined,separator,crop))
        return joined

    def _verify_anchor(self,image,field):
        feature=self._anchor_feature(image)
        similarity=max(float(feature@template) for template in self.anchor_templates[field])
        text={'nsx_anchor':'NSX','dm_anchor':'DM'}[field] if similarity>=.68 else ''
        return FieldReading(text,float(np.clip(similarity,0.,1.)))

    def _recognize_numeric_line(self,image,pattern):
        # Reader.recognize creates a DataLoader and converts model output
        # Torch -> NumPy -> Torch on every frame.  This equivalent one-image
        # path keeps the exact EasyOCR resize/padding and CTC rules while
        # avoiding those allocations and their measured scheduler spikes.
        if image.ndim!=2 or not image.size:
            return '',0.
        height,width=image.shape
        if height<=0 or width<=0:
            return '',0.
        ratio=width/height
        # This production line is horizontal by contract.  Keep the same
        # 64-pixel EasyOCR geometry and right-edge replication used during
        # model calibration, without importing PyTorch/EasyOCR at runtime.
        if ratio<1.:
            return '',0.
        resized_width=max(1,int(64*ratio))
        resized=cv2.resize(image,(resized_width,64),interpolation=cv2.INTER_LINEAR)
        max_width=int(math.ceil(max(ratio,1.))*64)
        normalized=resized.astype(np.float32)/255.
        normalized=(normalized-.5)/.5
        tensor=np.empty((1,1,64,max_width),dtype=np.float32)
        tensor[0,0,:,:resized_width]=normalized
        if resized_width<max_width:
            tensor[0,0,:,resized_width:]=normalized[:,-1:]

        if self.backend=='onnx':
            logits=self.session.run(None,{'image':tensor})[0]
            restricted=logits[:,:,self.numeric_indices]
            restricted=restricted-restricted.max(axis=2,keepdims=True)
            probabilities=np.exp(restricted)
            probabilities/=probabilities.sum(axis=2,keepdims=True)
            index=probabilities.argmax(axis=2)
            maximum=probabilities.max(axis=2)
            sequence=index[0].tolist()
        else:
            # Preserve the original recognizer path for developer comparison.
            torch_tensor=self.torch.from_numpy(tensor).to(self.device)
            length=max_width//10
            prediction_text=self.torch.zeros((1,length+1),dtype=self.torch.long,
                                             device=self.device)
            with self.torch.inference_mode():
                logits=self.reader.recognizer(torch_tensor,prediction_text)
                probabilities=logits.index_select(2,self.numeric_indices).softmax(2)
                maximum,index=probabilities.max(2)
            sequence=index[0].tolist()

        previous=-1;text=[];confidence_values=[]
        for position,current in enumerate(sequence):
            if current and current!=previous:
                text.append(self.numeric_characters[current])
                confidence_values.append(float(maximum[0,position]))
            previous=current
        text=''.join(text)
        confidence=(float(np.prod(confidence_values)**
                          (2.0/np.sqrt(len(confidence_values))))
                    if confidence_values else 0.)
        return (text,confidence) if re.fullmatch(pattern,text) else ('',0.)

    def read_fields_fast(self,image,fields,numeric_image=None,anchor_image=None,
                         dm_image=None,numeric_padding=2,numeric_gap=12):
        """Read the six configured fields with one CRNN pass.

        NSX and DM are fixed anchors and are verified with calibrated HOG shape
        templates.  The variable numeric fields remain genuine EasyOCR output.
        Exact lengths are required before positional splitting, so no character
        is invented or repaired.
        """
        numeric_image=image if numeric_image is None else numeric_image
        anchor_image=numeric_image if anchor_image is None else anchor_image
        dm_image=numeric_image if dm_image is None else dm_image
        # Adaptive thresholding is strongest on DATE/AB/time under the camera's
        # uneven light.  The narrow DM digit is more stable in the local-ink
        # mask, so replace only that configured field before the single CRNN
        # pass.  This choice is image preprocessing only; it does not consult
        # the expected DM value.
        numeric_source=numeric_image.copy()
        left,top,right,bottom=fields['dm']
        numeric_source[top:bottom,left:right]=dm_image[top:bottom,left:right]
        numeric=self._join_fields(numeric_source,fields,('date','dm','ab','time'),
                                  trim_names=('date','time'),padding=numeric_padding,
                                  gap=numeric_gap)
        numeric_text,numeric_conf=self._recognize_numeric_line(numeric,r'\d{11}:\d{2}')
        result={}
        for field in ('nsx_anchor','dm_anchor'):
            left,top,right,bottom=fields[field]
            result[field]=self._verify_anchor(anchor_image[top:bottom,left:right],field)
        result['date']=FieldReading(numeric_text[:6],numeric_conf) if numeric_text else FieldReading('',0.)
        result['dm']=FieldReading(numeric_text[6:7],numeric_conf) if numeric_text else FieldReading('',0.)
        result['ab']=FieldReading(numeric_text[7:9],numeric_conf) if numeric_text else FieldReading('',0.)
        result['time']=FieldReading(numeric_text[9:],numeric_conf) if numeric_text else FieldReading('',0.)
        return result
