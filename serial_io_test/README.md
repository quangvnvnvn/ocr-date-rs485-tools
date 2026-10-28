# RS485 I/O Tester

Ứng dụng Windows kiểm tra cổng RS485 qua COM8, mặc định 9600 baud, 8 data bits, parity None, 1 stop bit (8N1).

## Chức năng

- Modbus RTU: đọc coil/discrete input/holding register/input register (FC 01–04), ghi coil/register đơn (FC 05–06), ghi nhiều (FC 15–16), kiểm tra CRC và báo exception/timeout.
- HEX thô: gửi chuỗi byte HEX và thu byte phản hồi trong khoảng chờ cấu hình.
- Danh sách COM đang có; vẫn cho nhập COM8 thủ công.
- Có hộp xác nhận trước lệnh ghi Modbus.

## Cách dùng

1. Mở ứng dụng và chọn COM8.
2. Nhập Slave ID và địa chỉ theo manual thiết bị. Địa chỉ trong ứng dụng là 0-based; quy ước 00001/40001 có thể khác theo hãng.
3. Chọn function code rồi bấm Gửi lệnh. Ghi I/O yêu cầu xác nhận.
4. Tab HEX thô dùng để gửi frame tùy ý. CRC Modbus không tự được thêm vào.

Ứng dụng hỗ trợ Modbus RTU và byte thô, không tự dò giao thức, ID hoặc địa chỉ. Hãy kiểm tra dây A/B, nguồn thiết bị và bộ chuyển đổi RS485. Mỗi thao tác mở cổng, gửi lệnh, đọc phản hồi rồi đóng cổng.

## Chạy từ source

```powershell
python -m pip install -r requirements.txt
python app.py
```
