"""Export EasyOCR's English generation-2 recognizer for the light CPU runtime."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'packaging_onnx'
if VENDOR.exists():
    sys.path.insert(0,str(VENDOR))

import torch
import easyocr


class ExportModel(torch.nn.Module):
    """Equivalent CTC forward without dynamic AdaptiveAvgPool2d."""
    def __init__(self,model):
        super().__init__()
        self.feature=model.FeatureExtraction
        self.sequence=model.SequenceModeling
        self.prediction=model.Prediction

    def forward(self,image):
        visual=self.feature(image).permute(0,3,1,2)
        visual=visual.mean(dim=3)
        contextual=self.sequence(visual)
        return self.prediction(contextual.contiguous())


def main() -> int:
    output=ROOT/'data/models/english_g2_recognizer.onnx'
    metadata=ROOT/'data/models/english_g2_recognizer.json'
    reader=easyocr.Reader(['en'],gpu=False,detector=False,quantize=False,
                          download_enabled=False,
                          model_storage_directory=str(ROOT/'data/models'),verbose=False)
    model=ExportModel(reader.recognizer.eval()).eval()
    image=torch.zeros((1,1,64,512),dtype=torch.float32)
    torch.onnx.export(model,image,str(output),opset_version=17,
        input_names=['image'],output_names=['logits'],
        dynamic_axes={'image':{0:'batch',3:'width'},
                      'logits':{0:'batch',1:'steps'}},dynamo=False)
    characters=reader.character
    numeric_characters=['']+list('0123456789:')
    payload={
        'model':'english_g2_recognizer.onnx',
        'input_height':64,
        'characters':characters,
        'numeric_characters':numeric_characters,
        'numeric_indices':[0]+[characters.index(char)+1 for char in numeric_characters[1:]],
        'opset':17,
    }
    metadata.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(output)
    print(metadata)
    print(f'bytes={output.stat().st_size}')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
