# OCR DATE — ảnh tĩnh + camera Hikrobot trigger cứng

Luồng CPU Lite đã đọc đúng 45/45 frame từ camera Hikrobot serial
`DA9982258`: `NSX 220826 DM 1` / `05 17:59`. Cả 45 frame đều tìm được
dải tối phía dưới và đạt giới hạn lệch góc 3°. Đây là replay trên ảnh
đã chụp; vẫn phải nghiệm thu với trigger, lens, đèn và tốc độ dây chuyền thật.
GUI hỗ trợ ảnh tĩnh, mô phỏng trigger và camera công nghiệp Hikrobot qua MVS SDK.
PLC/cảm biến kích trực tiếp ngõ Line0/Line1 của camera. Sản xuất chỉ ARM,
nhận frame và xử lý; software trigger chỉ có nút test một frame cho kỹ thuật viên.

## Chế độ quyết định 100 ms

Luồng mặc định dùng ONNX Runtime CPU 4 luồng, không cần GPU rời/CUDA/PyTorch,
phù hợp cấu hình mục tiêu Core i5 Gen 8 và RAM 8 GB. Hạn chót 100 ms tính từ khi
đã có frame trong bộ nhớ đến lúc có PASS/FAIL. Bám ROI chạy trên ảnh thu nhỏ,
vùng DATE được nắn phối cảnh về đúng chiều, góc chữ được tìm bằng hình học nét in
và bốn trường số được ghép theo vị trí đã hiệu chuẩn để mô hình OCR chỉ chạy một lần. NSX/DM cố định được xác nhận bằng HOG từ ảnh hiệu
chuẩn. Góc DATE còn được so sánh với mép của dải ngang tối ngay phía dưới. Bộ dò
bắt buộc một phía của mép phải là mảng tối rộng, nên không nhận nhầm viền bao bì,
nét chữ hoặc đường minh họa mảnh làm chuẩn.
Chuỗi OCR vẫn phải đúng độ dài, dấu hai chấm, parser, confidence và điều
kiện AB/DM/ngày; phần mềm không điền ký tự thiếu.

Benchmark ONNX CPU trên 13 ảnh cầm tay, lặp 10 lần và có cả phép đo độ lệch góc:
130/130 cho đúng kết quả mong đợi, 130/130 dưới 100 ms; p50 39,83 ms,
p95 45,22 ms, lớn nhất 47,72 ms. Bài đo riêng 5 ảnh mẫu lặp 50 lần:
250/250 PASS và dưới 100 ms; p50 41,81 ms, p95 48,94 ms, lớn nhất
97,60 ms. Mười hai ảnh
có đủ dữ liệu đều đọc đúng; ảnh xoay 180 độ đã bị cắt mất số DM ở mép ảnh nên được
từ chối thay vì điền DM từ thiết lập. Đây là số đo trên máy hiện tại, với model đã nạp
và ảnh đã giải mã; cần đo lại khi đổi CPU, camera hoặc độ phân giải.

## Chạy ngay trên máy này

Mở `OCR_DATE.exe` hoặc lối tắt **OCR DATE** trên Desktop để dùng giao diện.

### Bản cài đặt cho máy khác

Giải nén `OCR_DATE_BAN_CAI_DAT_Windows_64bit_2026-10-29.zip`, chạy
`OCR_DATE_Setup_2026-10-29.exe` rồi chọn Next → Install. Bộ cài chứa ứng dụng,
runtime và model; tự tạo lối tắt **OCR DATE - Hikrobot RS485** trên Desktop.
Cài mới dùng thư mục `D:\Apps\OCR_DATE` nếu có ổ D:, nếu không dùng
`%LOCALAPPDATA%\Programs\OCR_DATE`; có thể chọn thư mục khác có quyền ghi.
Cài đè giữ `config/config.json`; gỡ ứng dụng giữ cấu hình và báo cáo đã tạo.
Camera thật cần cài thêm HIKROBOT MVS 64-bit cùng Python SDK của hãng.

Tạo bộ cài bằng `./build_installer.ps1` (cần Inno Setup 6); dùng
`-SkipPortableBuild` khi payload portable hiện tại đã được kiểm tra.
Kiểm tra cài mới/cài đè/gỡ bằng `./tools/test_installer.ps1` khi chưa có bản cài
đăng ký trên máy. Kiểm thử này không mở camera hay phát tín hiệu RS485.

Lệnh test CLI cho phát triển (không mở giao diện):

```powershell
python main.py --folder data/test_images --roi-manifest config/sample_rois.json --ab 12 --dm 1 --minimum-confidence 0.70
```

Mỗi lần chạy tạo thư mục riêng trong data/debug, chứa report.html, results.json,
results.csv và ảnh debug từng mẫu. Mở HTML bằng trình duyệt trên máy.
Đường dẫn tiếng Việt được hỗ trợ. Mã thoát 0 khi tất cả PASS, 1 khi có ảnh FAIL.
Đã kiểm tra 39 unit test và các cổng lỗi hình học/format/confidence/độ lệch góc/camera;
cả 5 ảnh giả lập đều bị từ chối. Chúng không thay thế ảnh lỗi thực tế.

## Python và cài đặt

Đã chạy bằng Python 3.12.4, ONNX Runtime 1.30.0 CPU, OpenCV headless
4.14.0 và NumPy 2.5.3. Model `english_g2_recognizer.onnx` (~15 MB) nằm trong
`data/models`. Engine không gọi Cloud OCR và không tải model khi chạy.

Nếu cài môi trường mới, có internet ở bước cài dependency:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --folder data/test_images --roi-manifest config/sample_rois.json --ab 12 --dm 1
```

Các lệnh cài trên dành riêng cho .venv. Môi trường mới này chưa được kiểm tra trong lượt
triển khai hiện tại. Khi chuyển máy cần mang theo hai file model ONNX trong `data/models`; có thể
truyền --model-directory để chỉ thư mục model khác. Đây chưa phải bản EXE portable.

## Camera Hikrobot và trigger phần cứng

1. Cài **HIKROBOT MVS 64-bit** và Python SDK đúng kiến trúc Windows. MVS hiện không
   được đóng kèm vì là runtime của nhà sản xuất. Nếu cài ở đường dẫn khác mặc định,
   đặt biến môi trường `HIKROBOT_MVIMPORT` tới thư mục chứa
   `MvCameraControl_class.py`.
2. Dùng MVS để kiểm tra camera, focus, exposure và pixel format trước. Adapter nhận
   `Mono8`, các Bayer 8-bit thông dụng, `RGB8 Packed` và `BGR8 Packed`.
3. Đấu tín hiệu trigger theo đúng manual của chính xác model camera/I/O cable.
   Không tự cấp điện áp theo ví dụ chung. Trong GUI chọn serial (hoặc để trống nếu
   chỉ có một camera), chọn Line0/Line1 rồi bấm **KẾT NỐI + ARM**.
4. Phần mềm nạp nóng OCR và AUTO ROI trước, sau đó mới báo
   **ĐÃ ARM · CHỜ TRIGGER**. Mỗi tín hiệu ngoài tạo một frame và tự chạy kiểm tra;
   không cần bấm **KIỂM TRA OCR**.
5. Dùng **CAMERA LIVE** để chỉnh lens/exposure; khi dừng LIVE phần mềm tự
   khôi phục trigger ngoài. Nút **TRIGGER TEST (SOFTWARE)** chỉ phát một frame
   chẩn đoán rồi trả camera về Line0/Line1. Chế độ sản xuất không tự phát trigger.

Mỗi frame lưu số frame của SDK. Nếu số thứ tự nhảy cóc, frame hiện tại bị đánh
`FAIL_FRAME_GAP`; phần mềm không bỏ qua mất ảnh rồi báo PASS. Mô phỏng dùng đúng một
ô chờ và trả `FAIL_BUSY` nếu trigger mới tới khi ô đó còn đầy.

Mốc 100 ms được đo từ lúc MVS giao buffer ảnh cho tiến trình (kể cả sao chép/chuyển
pixel) đến lúc có quyết định. Exposure, thời gian truyền trước khi SDK giao buffer,
độ trễ PLC và cơ cấu chấp hành chưa nằm trong mốc này. Khi lắp camera phải benchmark
lại với đúng model, resolution, exposure, CPU, cáp và tần suất trigger thực tế.

Camera Hikrobot thật đã kết nối qua MVS, chụp và replay 45 frame. Phần còn lại
cần nghiệm thu tại dây chuyền là tín hiệu điện trigger, chu kỳ liên tục, lens/đèn,
exposure và thời gian trên đúng máy Core i5 Gen 8.

## Đẩy sản phẩm NG qua RS485

Giao diện có **Bật đẩy sản phẩm NG qua RS485**, cổng COM, địa chỉ slave, DO1–DO8,
**Delay sau NG (ms)** và **Xung (ms)**. Delay bắt đầu đúng lúc phần mềm có quyết
định OK/NG, trước khi gửi lệnh bật DO. Chỉ kết quả NG của frame do trigger cứng
tạo ra mới được lập lịch đẩy; nút trigger phần mềm để test không bật DO. Các giá
trị lưu trong `config/config.json` và được khôi phục khi mở lại.

Module mục tiêu là Waveshare Industrial 8-Ch Modbus RTU GPIO Extender, mặc định
9600 N8 1. DO1–DO8 dùng coil 0x0000–0x0007, lệnh Modbus 05, đọc lại bằng 01.
Ứng dụng kiểm tra kênh ở Normal mode, xác nhận DO OFF trước khi ARM camera,
kiểm tra phản hồi/CRC và đọc lại DO sau mỗi lần bật/tắt. Nếu lỗi giao tiếp hoặc
hai NG quá gần khiến xung chồng nhau, luồng camera dừng và cần xử lý lỗi trước
khi chạy tiếp. Nút **Kiểm tra kết nối RS485** chỉ gửi OFF và đọc lại, không bật DO.
Nút **TEST NG → XI LANH** giả lập một kết quả NG khi được bấm: chờ đúng
**Delay sau NG** rồi phát một xung DO thật theo thời gian **Xung**. Nút này dùng
để thử COM, DO, van và xi lanh khi không chạy camera; có thể bấm khi chế độ đẩy
tự động đang tắt. Không bấm nếu vùng đẩy chưa được chuẩn bị an toàn.

DO là ngõ open drain, tối đa 500 mA mỗi kênh theo tài liệu module. Đấu ngõ này
qua mạch/van điện từ phù hợp với nguồn ngoài và bảo vệ tải cảm; không nối thẳng
xi lanh khí vào DO. Chưa có số đo thời gian từ vị trí chụp tới vị trí đẩy nên
bản mặc định tắt chức năng đẩy và đặt delay bằng 0. Cần đo vận tốc băng tải,
khoảng cách, độ trễ xử lý/truyền RS485 và độ trễ van/xi lanh để hiệu chuẩn delay;
chỉ ARM đẩy khi delay nằm trong 1–60000 ms. `eject_events.jsonl` trong thư mục
debug camera ghi các lần ON/OFF hoặc lỗi. Chưa xác nhận I/O thực tế với module.

## Chọn và lưu ROI trên ảnh mới

```powershell
python main.py --image "D:\anh_mau\sample.jpg" --select-roi --save-roi --ab 12 --dm 1
```

Kéo chuột bao cả hai dòng DATE, chừa lề, tránh chữ nhãn xung quanh; Enter xác nhận,
C hủy lựa chọn. Preview được thu nhỏ cho vừa màn hình; tọa độ quy đổi về ảnh gốc.
Lưu cả pixel, kích thước tham chiếu và normalized coordinates vào config/config.json.
Lần chạy sau bỏ --select-roi sẽ dùng lại ROI. Chọn lại bằng cùng lệnh trên.
--reset-roi xóa ROI trong config; có thể kết hợp --select-roi --save-roi để thay mới.
Luồng chọn ROI bằng chuột đã có code nhưng chưa được kiểm thử thao tác trực tiếp.

Có thể dùng pixel chính xác thay cho chuột:

```powershell
python main.py --image data/test_images/date1.jpg --roi 746 1133 683 260 --save-roi --ab 12 --dm 1
```

config/sample_rois.json là annotation riêng từng mẫu, chỉ chứa tọa độ, không chứa
nội dung OCR/đáp án. Folder ảnh ở vị trí ổn định có thể dùng config ROI chung.
Đổi resolution chỉ scale ROI; không tự bù sản phẩm dịch chuyển hoặc thay đổi góc chụp.

## Operator setting và chống nhận nhầm

- AB đúng 2 chữ số ASCII, giữ số 0 đầu; DM là 1/2/3.
- --expected-date 2026-08-22 bật so sánh NSX; bỏ option chỉ kiểm tra ngày hợp lệ.
- --minimum-confidence 0.70 là ngưỡng đã hiệu chỉnh cho hai bộ ảnh hiện có.
- --max-angle-deviation 3.0 đặt độ lệch tối đa giữa DATE và mép dải ngang tối phía dưới.
  GUI có ô **Lệch góc tối đa** và lưu giá trị này cho lần mở sau.
- Confidence riêng DATE/DM/AB/TIME, overall bằng minimum bốn field; NSX/DM anchor
  cũng phải vượt ngưỡng. Điểm engine chưa hiệu chuẩn thành xác suất đúng.
- Chế độ 100 ms chỉ dùng gray và một lần nhận dạng chuỗi DATE+DM+AB+TIME.
  Chuỗi chỉ được tách khi đúng chính xác cấu trúc `ddddddddddd:dd`.
- Không sửa O/I/l/S/?, không điền ký tự từ ngày mong muốn.
- DM được OCR với 0–9 để không ép chữ số sai thành 1/2/3; parser từ chối ngoài 1–3.
- Anchor NSX/DM cố định được đối chiếu HOG với các ảnh hiệu chuẩn. Nếu hình dạng
  không khớp hoặc confidence thấp thì FAIL; chúng không được tìm bằng OCR toàn nhãn.
- Chỉ 2 dòng và nhóm 4+2 field phù hợp mới được OCR. Không tìm NSX bằng OCR toàn nhãn.
- Năm yy = 2000 + yy (2000–2099); ngày kiểm tra bằng datetime, giờ 00:00–23:59.
- DM ngoài 1–3 → FAIL_FORMAT; DM hợp lệ khác expected → FAIL_DM.

## Pipeline đã chọn

AUTO ROI thu nhỏ → RANSAC → nắn phối cảnh/xoay đúng chiều → grayscale → tìm góc bằng contour chữ → deskew →
Otsu tách line/field → một lần OCR số → kiểm tra anchor → parser → validator →
đo góc mép dải ngang tối phía dưới → so sánh độ lệch → kiểm tra hạn chót 100 ms.

Không upscale vì chữ gốc cao 57–70 px. Không nối dot hay chia từng chữ số đang chạm nhau;
recognizer đọc cả field DATE. CLAHE/adaptive/morphology đã benchmark và lưu ảnh debug,
không nằm trong shortlist chính. Xem PHASE1_REPORT.md để biết số đo và kết quả.

## Kiểm thử và debug

```powershell
python -m unittest discover -s tests -v
python benchmark_100ms.py --iterations 10
python benchmark_validation.py --iterations 10
python benchmark_samples.py
```

Benchmark tái dùng preprocessing/engine chính; ground truth chỉ so sánh sau OCR.
analyze_samples.py là thí nghiệm đọc cả dòng so với field, không phải luồng vận hành.
Ảnh debug: original, overlay, roi, gray, otsu, clahe, adaptive, morphology, final, fields.
JSON có candidate/raw/confidence, expected, tọa độ, góc DATE, góc thanh tham chiếu,
độ lệch, giới hạn và thời gian. Nếu thanh tham chiếu không tìm thấy thì kết quả là
`FAIL_REFERENCE_LINE_NOT_FOUND`; nếu độ lệch vượt giới hạn thì là
`FAIL_DATE_ALIGNMENT`.
Lỗi engine ghi traceback và FAIL_OCR. CLI tuần tự chưa có GUI cần giữ realtime.

Bộ dữ liệu hiện tại thiếu ngày/AB/DM khác và ảnh lỗi in thật; cần bổ sung để đánh giá
false PASS, hiệu chuẩn confidence và kiểm tra độc lập trước sản xuất. Trên 45
frame camera, dải chuẩn được tìm 45/45 và độ lệch lớn nhất là 2,44°; cổng lỗi đã được kiểm tra bằng giới hạn
ép 0,5° và ảnh tổng hợp, nhưng vẫn cần ảnh lỗi lệch DATE thật để nghiệm thu sản xuất.

## Giao diện Windows

Mở OCR_DATE.exe hoặc lối tắt OCR DATE trên Desktop. Lối tắt cũ OCR DATE - Test ảnh
cũng đã đổi sang giao diện. Với ảnh tĩnh: chọn ảnh, khoanh vùng DATE, nhập AB/DM,
bấm KIỂM TRA OCR. Với camera: chọn nguồn Hikrobot, serial/Line, rồi KẾT NỐI + ARM;
mỗi frame trigger ngoài được xử lý tự động.
Nút Thử 5 ảnh mẫu chạy cùng pipeline thật với các ROI đã thiết lập riêng.
Chọn một hàng trong bảng để xem lại ảnh và kết quả; phần xử lý chi tiết có thể ẩn/hiện.
OCR chạy nền; Dừng sẽ kết thúc sau ảnh hiện tại. Đóng cửa sổ khi đang chạy cũng chờ
ảnh hiện tại hoàn thành an toàn. Ảnh và báo cáo lưu trong data/debug/gui_*.

Trong workspace, `OCR_DATE.exe` là launcher mở Python local. Bản ZIP CPU Lite
đã kèm runtime độc lập; phải giải nén cả thư mục, không chép riêng một EXE.
Đã kiểm thử UI với 5 ảnh thật, ROI bằng sự kiện chuột và kiểm tra event loop vẫn phản hồi.

### AUTO ROI và chọn ngày NSX

`Chế độ ROI` mặc định là **Tự động (khuyên dùng)**. Phần mềm dùng đặc trưng ổn định
của mẫu bao bì và RANSAC homography để chiếu vùng DATE sang ảnh mới; không OCR toàn bộ
nhãn để tìm chữ NSX. AUTO ROI có confidence riêng, kiểm tra số inlier, hình học và sau
đó vẫn bắt buộc vùng tìm được có đúng cấu trúc hai dòng/4+2 field. Vùng được nắn về
khung chuẩn trước OCR, nên 12/12 ảnh cầm tay có đủ dữ liệu và ảnh thử xoay 180 độ
đều về đúng hướng; ảnh trắng và ảnh nhiễu bị từ chối. Đã thử thêm dịch chuyển,
scale 0,95–1,03 và xoay -5°/+6°. Kết quả này áp dụng cho cùng mẫu thiết kế bao bì; đổi thiết kế cần
hiệu chuẩn lại ảnh tham chiếu.

Chọn `Cố định / khoanh tay` khi vị trí sản phẩm ổn định hoặc AUTO không phù hợp.
Trong chế độ này các nút khoanh/lưu/xóa vùng mới được bật.

Chọn ngày cần kiểm tra ở ô **Ngày NSX cần kiểm tra**; ngày này không tự lấy theo ngày
hôm nay. Bấm **LƯU CẤU HÌNH** (hoặc `Ctrl+S`) để lưu AB, DM, ngưỡng tin cậy,
ngày NSX cần kiểm tra, chế độ ROI, giới hạn lệch góc, vùng ROI cố định và ảnh đang mở.
Lần mở sau giao diện tự khôi phục đúng các giá trị này. Khi đóng cửa sổ, phần mềm
cũng tự lưu cấu hình hiện tại. Cấu hình nằm tại `config/config.json`.

Chế độ nhanh không dùng AB/DM/ngày mong muốn để sửa hoặc lựa ký tự OCR. Nếu chuỗi
số sai độ dài, sai cấu trúc, anchor không khớp, confidence thấp hoặc quá 100 ms thì
kết quả là FAIL thay vì đoán.
