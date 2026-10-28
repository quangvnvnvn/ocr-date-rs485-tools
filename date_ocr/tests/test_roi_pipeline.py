from pathlib import Path
import unittest
import numpy as np
from roi.fixed_roi import FixedROI
from roi.auto_roi import AutoROIDetector
from ocr.ocr_base import FieldReading
from ocr.pipeline import inspect_image, choose_candidate, FIELD_ORDER
from ocr.preprocessing import preprocess, LayoutError
from ocr.validator import Expected


class ROIAndPipelineTests(unittest.TestCase):
    def test_hikrobot_uneven_illumination_layout(self):
        """Real camera shadows must still yield the fixed 4+2 DATE fields."""
        from main import load_image
        root=Path(__file__).resolve().parents[1]
        fixture_dir=root/'tests/fixtures/hikrobot_uneven'
        for path in sorted(fixture_dir.glob('*.png')):
            with self.subTest(path=path.name):
                variants,fields,metrics=preprocess(load_image(path))
                self.assertEqual(set(fields),set(FIELD_ORDER))
                self.assertEqual(len(metrics['rows']),2)
                self.assertTrue(metrics['uneven_illumination'])
                self.assertEqual(metrics['numeric_variant'],'adaptive')

    def test_scaled_roi_and_roundtrip(self):
        roi=FixedROI(100,50,300,100,1000,500)
        self.assertEqual(roi.bounds((1000,2000,3)),(200,100,600,200))
        self.assertEqual(FixedROI.from_dict(roi.to_dict()),roi)
        self.assertEqual(roi.crop(np.zeros((1000,2000,3),np.uint8)).shape,(200,600,3))

    def test_invalid_roi(self):
        for args in ((-1,0,10,10,100,100),(0,0,0,10,100,100),(90,0,20,10,100,100)):
            with self.assertRaises(ValueError):
                FixedROI(*args)
        with self.assertRaises(ValueError):
            FixedROI.from_dict({**FixedROI(0,0,10,10,100,100).to_dict(),'width_ratio':.5})

    def test_blank_roi_rejected_before_engine(self):
        for image in (np.full((200,600,3),255,np.uint8),np.zeros((200,600,3),np.uint8)):
            report,_=inspect_image(image,FixedROI(0,0,600,200,600,200),None,Expected('12',1))
            self.assertEqual(report['validation']['result'],'FAIL_ROI_NOT_FOUND')

    def test_confident_disagreement(self):
        a={'name':'a','fields':{f:{'text':'1','confidence':.95} for f in FIELD_ORDER}}
        b={'name':'b','fields':{f:{'text':'1','confidence':.96} for f in FIELD_ORDER}}
        b['fields']['date']['text']='2'
        selected,conflict,_=choose_candidate([a,b],.8)
        self.assertTrue(conflict)
        self.assertEqual(selected['name'],'b')

    def test_low_candidate_cannot_override(self):
        a={'name':'a','fields':{f:{'text':'wrong','confidence':.5} for f in FIELD_ORDER}}
        b={'name':'b','fields':{f:{'text':'different','confidence':.9} for f in FIELD_ORDER}}
        self.assertEqual(choose_candidate([a,b],.8)[0]['name'],'b')

    def test_real_sample_geometry_and_fail_gates(self):
        import json
        from main import load_image
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'config/sample_rois.json').read_text())
        for name,config in manifest.items():
            image=load_image(root/'data/test_images'/name)
            variants,fields,metrics=preprocess(FixedROI.from_dict(config).crop(image))
            self.assertEqual(set(fields),set(FIELD_ORDER))
            self.assertEqual(len(metrics['rows']),2)
            self.assertFalse(metrics['uneven_illumination'])
            self.assertEqual(metrics['numeric_variant'],'gray')
        image=load_image(root/'data/test_images/date1.jpg')
        roi=FixedROI.from_dict(manifest['date1.jpg'])
        class BadEngine:
            def read_field(self,image,field,decoder='greedy'):
                raise RuntimeError('engine failure')
        self.assertEqual(inspect_image(image,roi,BadEngine(),Expected('12',1))[0]['validation']['result'],'FAIL_OCR')
        class AnchorLow:
            def read_field(self,image,field,decoder='greedy'):
                values=dict(nsx_anchor='NSX',date='220826',dm_anchor='DM',dm='1',ab='12',time='12:52')
                return FieldReading(values[field],.4 if field=='nsx_anchor' else .99)
        self.assertEqual(inspect_image(image,roi,AnchorLow(),Expected('12',1))[0]['validation']['result'],
                         'FAIL_OCR_LOW_CONFIDENCE')

    def test_auto_roi_finds_all_real_samples(self):
        import json
        from main import load_image
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'config/sample_rois.json').read_text())
        detector=AutoROIDetector(root/'data/test_images/date1.jpg',
                                 FixedROI.from_dict(manifest['date1.jpg']))
        for name,truth in manifest.items():
            image=load_image(root/'data/test_images'/name)
            result=detector.detect(image)
            self.assertTrue(result.found,name)
            self.assertGreaterEqual(result.confidence,.80,name)
            x,y,w,h=result.bbox;tx,ty,tw,th=(truth[k] for k in ('x','y','width','height'))
            intersection=max(0,min(x+w,tx+tw)-max(x,tx))*max(0,min(y+h,ty+th)-max(y,ty))
            union=w*h+tw*th-intersection
            self.assertGreater(intersection/union,.55,name)

    def test_auto_roi_rejects_non_product(self):
        import json
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'config/sample_rois.json').read_text())
        detector=AutoROIDetector(root/'data/test_images/date1.jpg',
                                 FixedROI.from_dict(manifest['date1.jpg']))
        self.assertFalse(detector.detect(np.full((1000,1000,3),255,np.uint8)).found)
        random=np.random.default_rng(42).integers(0,256,(1000,1000,3),dtype=np.uint8)
        self.assertFalse(detector.detect(random).found)

    def test_auto_roi_handles_small_rotation_scale_and_shift(self):
        import json,cv2
        from main import load_image
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'config/sample_rois.json').read_text())
        image=load_image(root/'data/test_images/date1.jpg')
        detector=AutoROIDetector(root/'data/test_images/date1.jpg',
                                 FixedROI.from_dict(manifest['date1.jpg']))
        height,width=image.shape[:2]
        for angle,scale,dx,dy in ((-5,.95,40,60),(6,1.03,-30,-50)):
            matrix=cv2.getRotationMatrix2D((width/2,height/2),angle,scale)
            matrix[:,2]+=(dx,dy)
            transformed=cv2.warpAffine(image,matrix,(width,height),borderValue=(40,40,40))
            result=detector.detect(transformed)
            self.assertTrue(result.found)
            self.assertGreaterEqual(result.confidence,.80)

    def test_auto_roi_rectifies_upside_down_product(self):
        import json,cv2
        from main import load_image
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'config/sample_rois.json').read_text())
        image=load_image(root/'data/test_images/date1.jpg')
        detector=AutoROIDetector(root/'data/test_images/date1.jpg',
                                 FixedROI.from_dict(manifest['date1.jpg']))
        upside_down=cv2.rotate(image,cv2.ROTATE_180)
        result=detector.detect(upside_down)
        self.assertTrue(result.found)
        rectified=detector.rectify(upside_down,result)
        variants,fields,metrics=preprocess(rectified)
        self.assertEqual(set(fields),set(FIELD_ORDER))
        self.assertEqual(len(metrics['rows']),2)
        self.assertGreater(rectified.shape[1],rectified.shape[0])


if __name__=='__main__':
    unittest.main()
