# Kết quả Phase 1 trên ảnh thật — 05/09/2026

## Phân tích ảnh

5 JPEG kích thước gốc 1926×2568. Quan sát cả 5 ảnh đều có nội dung
`NSX 220826 DM 1` / `12 12:52`. Ground truth này chỉ đối chiếu sau OCR,
không đưa vào crop, nhận dạng hay chọn candidate.

| Ảnh | ROI x,y,w,h trên ảnh gốc | Góc hiệu chỉnh | Cao chữ dòng 1/2 | Khoảng trống dòng |
|---|---|---:|---:|---:|
| date1 | 746,1133,683,260 | -5,00° | 66/61 px | 40 px |
| date2 | 795,1028,683,253 | -4,25° | 69/62 px | 39 px |
| date3 | 845,1140,655,232 | -3,25° | 64/57 px | 37 px |
| date4 | 817,1056,662,239 | -4,75° | 66/59 px | 39 px |
| date5 | 986,852,704,253 | -3,50° | 70/64 px | 41 px |

Đây là ROI khoanh riêng từng ảnh, chưa tự tìm DATE. Số đo dựa trên projection Otsu
sau deskew, không phải kích thước vật lý. Nét đen trung bình 25–27/255, nền 215–225/255.
Nền có mũi tên xám phía sau dòng giờ; không thấy chói che mất ký tự trong 5 ảnh.
Có nghiêng và thay đổi kích thước/vị trí nhẹ. Chưa đánh giá chống lóa trên ảnh khác.

Font inkjet có dot nhỏ, chữ số sát nhau, đặc biệt DATE. Tách field nguyên khối theo
khoảng trống giữa từ, không chia sáu chữ số bằng contour. Chữ đủ lớn, không phóng to.
Otsu loại phần lớn họa tiết xám; morphology không cần thiết.

## Engine và xử lý

Chọn EasyOCR English recognizer CPU, tắt detector; engine đã có local và thử thành công
trên cả 5 ảnh. Chỉ đưa sáu crop vào recognizer. Không tải CRAFT, YOLO hoặc Paddle,
không OCR toàn bao bì. Engine nằm sau OCRBase, tách parser, validator và ROI.
Chưa benchmark đối đầu Tesseract/Paddle nên không khẳng định EasyOCR tốt nhất tuyệt đối.

Tài liệu: [EasyOCR recognize/allowlist/offline model](https://www.jaided.ai/easyocr/documentation/).
Tesseract là lựa chọn có thể thử sau nếu cần giảm dependency;
[tài liệu Tesseract](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html) cũng hướng dẫn
crop, deskew và cấu hình segmentation/whitelist.

OCR cả dòng có lỗi AB=42 hoặc thừa 1 cuối giờ. Sau tách 4+2 nhóm, kết quả tốt hơn.
NSX/DM anchor vẫn được đọc thực tế. Không giới hạn OCR DM chỉ còn 1/2/3 để tránh ép
chữ số ngoài danh sách vào đáp án.

| Candidate theo field | Đúng nguyên chuỗi /5 | Cả 6 phần ≥0,80 /5 | Điểm nhỏ nhất | OCR trung bình/candidate |
|---|---:|---:|---:|---:|
| Gray | 5 | 4 | 0,7715 | 133 ms |
| Otsu | 5 | 5 | 0,8073 | 111 ms |
| CLAHE | 5 | 4 | 0,6090 | 103 ms |
| Adaptive | 4 | 2 | 0,5195 | 107 ms |
| Morphology 2×2 | 5 | 1 | 0,6076 | 112 ms |

Adaptive đọc giờ date3 thành 12852, mất dấu hai chấm. Giữ gray+Otsu, không chọn dựa vào
parser hợp lệ hoặc khớp expected. Hai candidate đủ ngưỡng mà đọc khác nhau sẽ FAIL.
Candidate thấp không được PASS dù chuỗi hợp lệ.

## Kết quả pipeline chính

Expected AB=12, DM=1, ngưỡng 0,80, kiểm tra khớp ngày OFF; ngày vẫn phải hợp lệ.
Tất cả đọc `NSX 220826 DM 1` / `12 12:52`: ngày 22/08/2026, DM=1, AB=12, giờ 12:52.

| Ảnh | Candidate | Overall confidence | Kết quả | Xử lý |
|---|---|---:|---|---:|
| date1 | gray | 0,9894 | PASS | 434 ms |
| date2 | gray | 0,9973 | PASS | 283 ms |
| date3 | otsu | 0,8955 | PASS | 292 ms |
| date4 | gray | 0,8594 | PASS | 323 ms |
| date5 | gray | 0,9989 | PASS | 284 ms |

Overall là minimum DATE/DM/AB/TIME; anchor phải vượt ngưỡng riêng. Thời gian một lượt CPU,
không gồm khởi tạo model/ghi toàn bộ debug, không phải benchmark realtime.
Điểm chưa hiệu chuẩn thành xác suất đúng. Tỷ lệ PASS không phải accuracy.

## Kiểm chứng và giới hạn

- 19 unit test đạt: parser, ngày nhuận, giờ, cấu trúc, confidence thiếu/NaN/thấp,
  expected, ROI scale/save, ảnh trắng, mâu thuẫn candidate, anchor thấp, lỗi engine.
- 5 ảnh lỗi giả lập từ date1 đều FAIL: xóa DATE/DM/TIME/dấu hai chấm và ảnh trắng.
  Chúng chỉ kiểm tra từ chối, không phải ảnh lỗi in thật.
- CLI ảnh/thư mục và xuất HTML/CSV/JSON đã chạy. Lỗi đường dẫn Unicode Windows đã sửa.
  Khoanh ROI bằng chuột chưa được thao tác trực tiếp.
- Trình duyệt tích hợp chặn file URL nên chưa kiểm tra trực quan HTML trong trình duyệt;
  đã kiểm tra trực tiếp ảnh ROI/processed và ảnh tổng hợp. HTML có thể mở trên máy.
- Chưa có ngày/AB/DM khác, ảnh nhòe/lóa/in thiếu thật hay tập kiểm định độc lập.
  Chưa đánh giá false PASS trên hàng lỗi, chưa camera/sensor và chưa chạy nhiều giờ.

Phần OCR core của Phase 1 đã chạy trên 5 ảnh cung cấp; chưa đủ bằng chứng sản xuất.
