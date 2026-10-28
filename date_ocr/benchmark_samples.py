"""Compare preprocessors on reviewed sample ROIs; labels only score outputs."""
from pathlib import Path
import json
import time
import cv2
import numpy as np
from main import load_image,write_json
from roi.fixed_roi import FixedROI
from ocr.preprocessing import preprocess
from ocr.ocr_engine import EasyOCREngine
from ocr.pipeline import candidate_raw


def main():
    root=Path(__file__).resolve().parent
    manifest=json.loads((root/'config/sample_rois.json').read_text())
    engine=EasyOCREngine()
    output=[]
    for name,config in manifest.items():
        image=load_image(root/'data/test_images'/name)
        variants,fields,metrics=preprocess(FixedROI.from_dict(config).crop(image))
        mask=variants['otsu']<128
        metrics.update(foreground_mean=float(variants['gray'][mask].mean()),
                       background_mean=float(variants['gray'][~mask].mean()),
                       saturated_fraction=float(np.mean(variants['gray']>=250)))
        record=dict(image=name,metrics=metrics,candidates=[])
        for variant,array in variants.items():
            started=time.perf_counter()
            readings={}
            for field,(l,t,r,b) in fields.items():
                reading=engine.read_field(array[t:b,l:r],field)
                readings[field]=dict(text=reading.text,confidence=reading.confidence)
            raw=candidate_raw(readings)
            record['candidates'].append(dict(name=variant,raw=raw,fields=readings,
                minimum_all_six=min(v['confidence'] for v in readings.values()),
                exact_match=raw=='NSX 220826 DM 1\n12 12:52',
                ocr_ms=round((time.perf_counter()-started)*1000,2)))
        output.append(record)
    write_json(root/'data/debug/preprocessing_benchmark.json',output)
    for name in variants:
        candidates=[next(c for c in r['candidates'] if c['name']==name) for r in output]
        print(name,'exact',sum(c['exact_match'] for c in candidates),
              'confident',sum(c['minimum_all_six']>=.8 for c in candidates),
              'min',round(min(c['minimum_all_six'] for c in candidates),4),
              'mean_ms',round(np.mean([c['ocr_ms'] for c in candidates]),1))


if __name__=='__main__':
    main()
