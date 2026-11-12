"""Fine-tune PP-OCR's CTC head with manually verified DATE samples on CPU."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from random import Random
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
import torch

from main import ROOT, load_image
from ocr.date_parser import parse_date
from ocr.pipeline import candidate_raw
from ocr.ppocr import CUSTOM_MODEL_NAME, MODEL_NAME, PPOCRReader, prepare_stamp_lines, stamp_fields
from training_samples import default_training_directory


def read_samples(directory):
    train, validation = [], []
    skipped = 0
    for path in sorted(directory.glob('*/sample.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not record.get('label_text'):
                continue
            label = parse_date(record['label_text'])
            lines, _, _ = prepare_stamp_lines(load_image(path.with_name('roi.png')))
        except (OSError, ValueError):
            skipped += 1
            continue
        text = f'NSX {label.nsx_raw} DM {label.dm}\n{label.ab} {label.time}'
        # Keep captures of the same print in the same split, including future captures.
        split = validation if sha256(text.encode()).digest()[0] % 5 == 0 else train
        split.append((lines, text))
    return train, validation, skipped


def get_ctc_head(model):
    multiply, add, identity, softmax = model.graph.node[-4:]
    if [node.op_type for node in (multiply, add, identity, softmax)] != ['MatMul', 'Add', 'Identity', 'Softmax']:
        raise ValueError('Không tìm thấy lớp CTC trong model PP-OCRv5')
    if (add.input[0] != multiply.output[0] or identity.input[0] != add.output[0]
            or softmax.input[0] != identity.output[0] or softmax.output[0] != model.graph.output[0].name):
        raise ValueError('Đầu ra CTC khác cấu trúc PP-OCRv5 dự kiến')
    params = {tensor.name: tensor for tensor in model.graph.initializer}
    return multiply.input[0], params[multiply.input[1]], params[add.input[1]]


def fit_head(reader, feature_name, weight, bias, samples, epochs):
    char_ids = {char: index for index, char in enumerate(reader.characters) if index}
    examples = []
    for lines, text in samples:
        features = reader.session.run([feature_name], {reader.input_name: reader.input_batch(lines)})[0]
        targets = [[char_ids[char] for char in line] for line in text.splitlines()]
        examples.append((torch.from_numpy(features), torch.tensor(targets[0] + targets[1]),
                         torch.tensor([len(target) for target in targets])))
    optimizer = torch.optim.AdamW((weight, bias), lr=0.0005, weight_decay=0)
    criterion = torch.nn.CTCLoss(zero_infinity=True)
    rng = Random(0)
    for epoch in range(epochs):
        rng.shuffle(examples)
        total = 0.0
        for features, targets, lengths in examples:
            probabilities = (features @ weight + bias).log_softmax(-1).transpose(0, 1)
            input_lengths = torch.full((features.shape[0],), features.shape[1], dtype=torch.long)
            loss = criterion(probabilities, targets, input_lengths, lengths)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += loss.item()
        print(f'Vòng {epoch+1}/{epochs} · CTC loss {total/len(examples):.4f}', flush=True)


def evaluate(reader, samples, threshold):
    results = []
    for lines, label in samples:
        fields = stamp_fields(reader.read_lines(lines))
        results.append(bool(fields) and candidate_raw(fields) == label
                       and min(value['confidence'] for value in fields.values()) >= threshold)
    return results


def train_model(directory, models, epochs, threshold):
    train, validation, skipped = read_samples(directory)
    print(f'{len(train)} mẫu học · {len(validation)} mẫu kiểm tra · {skipped} nhãn thiếu ảnh ROI hợp lệ', flush=True)
    if not train or not validation:
        raise ValueError('Cần thêm mẫu đã gán nhãn với các mã in khác nhau để tách tập học và tập kiểm tra.')
    torch.set_num_threads(2)
    destination = models / CUSTOM_MODEL_NAME
    current = destination if destination.is_file() else models / MODEL_NAME
    model = onnx.load(current)
    feature_name, head_weight, head_bias = get_ctc_head(model)
    weight = torch.nn.Parameter(torch.from_numpy(numpy_helper.to_array(head_weight).copy()))
    bias = torch.nn.Parameter(torch.from_numpy(numpy_helper.to_array(head_bias).copy()))
    model.graph.output.append(helper.make_tensor_value_info(feature_name, TensorProto.FLOAT,
                                                            [None, None, weight.shape[0]]))
    feature_reader = PPOCRReader(model.SerializeToString())
    fit_head(feature_reader, feature_name, weight, bias, train, epochs)
    del feature_reader
    del model.graph.output[-1]
    head_weight.CopyFrom(numpy_helper.from_array(weight.detach().numpy(), head_weight.name))
    head_bias.CopyFrom(numpy_helper.from_array(bias.detach().numpy(), head_bias.name))
    onnx.checker.check_model(model)
    payload = model.SerializeToString()
    before = evaluate(PPOCRReader(current), validation, threshold)
    after = evaluate(PPOCRReader(payload), validation, threshold)
    print(f'Đọc đúng trên tập kiểm tra ở ngưỡng {threshold:.0%} · {sum(before)}/{len(before)} → {sum(after)}/{len(after)}')
    if sum(after) <= sum(before) or any(old and not new for old, new in zip(before, after)):
        print('Giữ model hiện tại.')
        return False
    with TemporaryDirectory(prefix='.ppocr-', dir=models) as work:
        pending = Path(work) / CUSTOM_MODEL_NAME
        pending.write_bytes(payload)
        pending.replace(destination)
    print(f'Đã lưu model mới vào {destination}. Mở lại OCR DATE để nạp model.')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, help='Thư mục chứa các mẫu đã gán nhãn')
    parser.add_argument('--epochs', type=int, default=3)
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error('--epochs phải lớn hơn 0')
    try:
        config_path = ROOT / 'config/config.json'
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
        directory = args.folder or Path(config.get('training_directory') or default_training_directory())
        threshold = float(config.get('inspection', {}).get('minimum_confidence', .8))
        train_model(directory, ROOT / 'data/models', args.epochs, threshold)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
