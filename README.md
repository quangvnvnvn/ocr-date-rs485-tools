# OCR DATE — Hikrobot và RS485

Giao diện mới chia thành **Chạy thật**, **Chạy thử**, **Test chức năng** và
**Cài đặt**. Kết quả và ảnh kiểm tra dùng chung bên dưới; chuyển tab không tự ARM.
Tab Chạy thật ưu tiên ảnh lớn và cho chọn NSX/AB/DM, khóa thông số khi ARM;
thông số và nút kết nối camera nằm trong Cài đặt. Lịch sử ẩn ở tab Chạy thật.

**[Tải bản giao diện 4 tab (2026.10.29.3)](https://github.com/quangvnvnvn/ocr-date-rs485-tools/releases/tag/ocr-date-v2026.10.29.3)**

**[Tải driver camera MVS 5.1.0](https://github.com/quangvnvnvn/ocr-date-rs485-tools/releases/download/ocr-date-v2026.10.29.1/MVS_STD_5.1.0_260909.exe)** (~776 MB).
Cài driver USB3/GigE phù hợp và Python SDK/Development trước khi dùng camera.

Ứng dụng Windows kiểm tra ngày sản xuất trên bao bì, so sánh góc DATE với dải tối
phía dưới và điều khiển ngõ DO RS485 để đẩy sản phẩm NG.

## Tải bản cài

**[Bản phát hành OCR DATE 2026.10.29.3](https://github.com/quangvnvnvn/ocr-date-rs485-tools/releases/tag/ocr-date-v2026.10.29.3)**

- [ZIP chứa bộ cài và hướng dẫn](https://github.com/quangvnvnvn/ocr-date-rs485-tools/releases/download/ocr-date-v2026.10.29.3/OCR_DATE_BAN_CAI_DAT_Windows_64bit_2026-10-29.zip)
- [Tải trực tiếp Setup EXE](https://github.com/quangvnvnvn/ocr-date-rs485-tools/releases/download/ocr-date-v2026.10.29.3/OCR_DATE_Setup_2026-10-29.exe)

Giải nén ZIP, chạy `OCR_DATE_Setup_2026-10-29.exe`, chọn **Next → Install**.
Bộ cài tạo lối tắt **OCR DATE - Hikrobot RS485** trên Desktop và có sẵn runtime,
model OCR. Không cần cài Python hay GPU rời. Các mục **Source code (zip/tar.gz)**
do GitHub tạo chỉ chứa mã nguồn, không phải bộ cài.

Camera thật cần **HIKROBOT MVS 64-bit và Python SDK (MvImport)**. USB-RS485 cần
driver phù hợp với bộ chuyển đổi đang dùng. Cài mới để RS485 tắt và delay 0 ms;
cài đè giữ cấu hình đã lưu. Nút **TEST NG → XI LANH** phát xung DO thật khi bấm.

## Chức năng

- Ảnh tĩnh, camera live chỉnh lens, Hikrobot trigger ngoài và trigger test.
- AUTO ROI, OCR NSX/DM/AB/giờ, so sánh ngày và giới hạn lệch góc.
- Delay từ kết quả NG đến lệnh DO theo ms; thời gian xung và cấu hình COM/slave/DO.
- Lưu cấu hình, báo cáo ảnh và lịch sử kiểm tra; kiểm tra lỗi trước khi trả PASS.

Mục tiêu OCR là 100 ms từ khi nhận buffer ảnh đến kết quả. Đã qua 46 kiểm thử,
thử cài/cập nhật/gỡ bộ cài và OCR mô phỏng 48,89 ms trên máy phát triển. Cần đo
lại trên máy đích và hiệu chuẩn camera, trigger, RS485, xi lanh tại dây chuyền.

## Mã nguồn

| Thư mục | Nội dung |
| --- | --- |
| [date_ocr](date_ocr/README.md) | Ứng dụng OCR, camera, RS485, kiểm thử và đóng gói |
| [date_ocr/installer](date_ocr/installer) | Script Inno Setup và hướng dẫn cài đặt |
| [serial_io_test](serial_io_test) | Công cụ kiểm tra COM/RS485 riêng |

Repository lưu mã nguồn, kiểm thử và tài liệu. Runtime, model lớn, dữ liệu debug
và file build được loại khỏi Git; bản cài đầy đủ nằm trong **Releases**.
