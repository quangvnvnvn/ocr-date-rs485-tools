from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
import serial
from serial.tools import list_ports

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def with_crc(data: bytes) -> bytes:
    crc = crc16_modbus(data)
    return data + bytes((crc & 0xFF, crc >> 8))


def check_crc(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16_modbus(frame[:-2]) == (frame[-2] | frame[-1] << 8)


def read_exact(port: serial.Serial, count: int) -> bytes:
    result = bytearray()
    deadline = time.monotonic() + port.timeout
    while len(result) < count and time.monotonic() < deadline:
        chunk = port.read(count - len(result))
        if chunk:
            result.extend(chunk)
    return bytes(result)


def modbus_exchange(port_name: str, slave: int, function: int, address: int,
                    quantity: int, values: list[int]) -> tuple[bytes, bytes]:
    if not 1 <= slave <= 247:
        raise ValueError("Slave ID phải trong khoảng 1–247.")
    if not 0 <= address <= 65535:
        raise ValueError("Địa chỉ phải trong khoảng 0–65535.")
    if function in (1, 2, 3, 4) and not 1 <= quantity <= (2000 if function < 3 else 125):
        raise ValueError("Số lượng đọc không hợp lệ (coils: 1–2000, registers: 1–125).")

    request = bytearray((slave, function, address >> 8, address & 0xFF))
    if function in (1, 2, 3, 4):
        request.extend((quantity >> 8, quantity & 0xFF))
    elif function == 5:
        if len(values) != 1 or values[0] not in (0, 1):
            raise ValueError("Ghi coil: nhập 0 hoặc 1.")
        request.extend((0xFF, 0) if values[0] else (0, 0))
    elif function == 6:
        if len(values) != 1 or not 0 <= values[0] <= 65535:
            raise ValueError("Ghi thanh ghi: nhập một giá trị 0–65535.")
        request.extend((values[0] >> 8, values[0] & 0xFF))
    elif function in (15, 16):
        if not values or len(values) != quantity:
            raise ValueError("Số giá trị cần ghi phải bằng số lượng.")
        if function == 15:
            if any(v not in (0, 1) for v in values):
                raise ValueError("Ghi nhiều coil: các giá trị chỉ được là 0 hoặc 1.")
            packed = bytearray((quantity + 7) // 8)
            for i, value in enumerate(values):
                packed[i // 8] |= value << (i % 8)
        else:
            if any(not 0 <= v <= 65535 for v in values):
                raise ValueError("Ghi nhiều thanh ghi: giá trị phải trong khoảng 0–65535.")
            packed = bytearray()
            for value in values:
                packed.extend((value >> 8, value & 0xFF))
        request.extend((quantity >> 8, quantity & 0xFF, len(packed)))
        request.extend(packed)
    else:
        raise ValueError("Function code không hỗ trợ.")
    request = with_crc(bytes(request))

    with serial.Serial(port_name, 9600, bytesize=8, parity=serial.PARITY_NONE,
                       stopbits=serial.STOPBITS_ONE, timeout=0.7, write_timeout=1) as port:
        port.reset_input_buffer()
        port.write(request)
        port.flush()
        head = read_exact(port, 2)
        if len(head) != 2:
            raise TimeoutError("Không nhận được phản hồi. Kiểm tra nguồn, dây A/B, Slave ID và thông số thiết bị.")
        if head[1] & 0x80:
            tail = read_exact(port, 3)
            response = head + tail
        elif function in (1, 2, 3, 4):
            size = read_exact(port, 1)
            if len(size) != 1:
                raise TimeoutError("Phản hồi Modbus không đủ byte.")
            response = head + size + read_exact(port, size[0] + 2)
        else:
            response = head + read_exact(port, 6)
    if len(response) < 5 or not check_crc(response):
        raise ValueError(f"Phản hồi thiếu hoặc CRC không hợp lệ: {response.hex(' ').upper() or '(rỗng)'}")
    if response[0] != slave:
        raise ValueError(f"Slave ID phản hồi là {response[0]}, mong đợi {slave}.")
    if response[1] & 0x80:
        raise ValueError(f"Thiết bị trả Modbus exception code 0x{response[2]:02X}.")
    return request, response


def raw_exchange(port_name: str, payload: bytes, wait_ms: int) -> tuple[bytes, bytes]:
    with serial.Serial(port_name, 9600, bytesize=8, parity=serial.PARITY_NONE,
                       stopbits=serial.STOPBITS_ONE, timeout=0.05, write_timeout=1) as port:
        port.reset_input_buffer()
        port.write(payload)
        port.flush()
        deadline = time.monotonic() + wait_ms / 1000
        received = bytearray()
        while time.monotonic() < deadline:
            chunk = port.read(max(1, port.in_waiting))
            if chunk:
                received.extend(chunk)
        return payload, bytes(received)


class Job(QObject):
    done = Signal(int, object, object)

    def __init__(self, job_id, operation):
        super().__init__()
        self.job_id = job_id
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
            self.done.emit(self.job_id, result, None)
        except Exception as exc:
            self.done.emit(self.job_id, None, str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RS485 I/O Tester | COM8 · 9600 8N1")
        self.resize(900, 690)
        self.setMinimumSize(760, 600)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f4f7fb; color: #172033; font-size: 10pt; }
            QGroupBox { background: white; border: 1px solid #d7dfeb; border-radius: 9px;
                        margin-top: 12px; padding: 14px 12px 12px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background: white; border: 1px solid #ccd5e2;
                        border-radius: 5px; padding: 6px; }
            QPushButton { background: #1769aa; border: 0; border-radius: 6px; color: white;
                          font-weight: 600; padding: 9px 15px; }
            QPushButton:hover { background: #0f568f; }
            QPushButton:disabled { background: #98a7b8; }
            QTabWidget::pane { border: 0; }
            QTabBar::tab { padding: 9px 16px; }
        """)

        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel("RS485 I/O TESTER")
        title.setFont(QFont("Segoe UI", 19, QFont.Weight.Bold))
        layout.addWidget(title)
        layout.addWidget(QLabel("Modbus RTU và truyền HEX thô · COM8 mặc định · 9600 baud · 8 data bits · None · 1 stop bit"))

        conn = QGroupBox("Cổng nối tiếp")
        row = QHBoxLayout(conn)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.refresh_ports()
        if self.port.findText("COM8") < 0:
            self.port.addItem("COM8")
        self.port.setCurrentText("COM8")
        self.refresh = QPushButton("Làm mới cổng")
        self.refresh.clicked.connect(self.refresh_ports)
        row.addWidget(QLabel("Cổng")); row.addWidget(self.port, 1); row.addWidget(QLabel("9600 · 8N1")); row.addWidget(self.refresh)
        layout.addWidget(conn)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.make_modbus_tab(), "Modbus RTU")
        self.tabs.addTab(self.make_raw_tab(), "HEX thô")
        layout.addWidget(self.tabs, 1)

        info = QLabel("RS485 là lớp vật lý. Modbus RTU cần đúng Slave ID và địa chỉ I/O của thiết bị; phần mềm không tự dò địa chỉ.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#526176; padding:4px")
        layout.addWidget(info)
        self.setCentralWidget(root)
        self.jobs = {}
        self.next_job_id = 1

    def refresh_ports(self):
        current = self.port.currentText() if hasattr(self, "port") else "COM8"
        ports = sorted({p.device for p in list_ports.comports()})
        self.port.clear()
        self.port.addItems(ports)
        if "COM8" not in ports:
            self.port.addItem("COM8")
        self.port.setCurrentText(current if current else "COM8")

    def make_modbus_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        box = QGroupBox("Thao tác Modbus")
        form = QGridLayout(box)
        self.slave = QSpinBox(); self.slave.setRange(1, 247); self.slave.setValue(1)
        self.function = QComboBox()
        self.functions = {
            "01 · Đọc Coil": 1, "02 · Đọc Discrete Input": 2,
            "03 · Đọc Holding Register": 3, "04 · Đọc Input Register": 4,
            "05 · Ghi 1 Coil": 5, "06 · Ghi 1 Register": 6,
            "15 · Ghi nhiều Coil": 15, "16 · Ghi nhiều Register": 16,
        }
        self.function.addItems(self.functions.keys())
        self.address = QSpinBox(); self.address.setRange(0, 65535)
        self.quantity = QSpinBox(); self.quantity.setRange(1, 2000); self.quantity.setValue(1)
        self.values = QLineEdit("1"); self.values.setPlaceholderText("vd: 1 hoặc 123,456")
        form.addWidget(QLabel("Slave ID"), 0, 0); form.addWidget(self.slave, 0, 1)
        form.addWidget(QLabel("Function"), 0, 2); form.addWidget(self.function, 0, 3)
        form.addWidget(QLabel("Địa chỉ (0-based)"), 1, 0); form.addWidget(self.address, 1, 1)
        form.addWidget(QLabel("Số lượng"), 1, 2); form.addWidget(self.quantity, 1, 3)
        form.addWidget(QLabel("Giá trị ghi"), 2, 0); form.addWidget(self.values, 2, 1, 1, 3)
        layout.addWidget(box)
        self.mb_button = QPushButton("Gửi lệnh Modbus")
        self.mb_button.clicked.connect(self.run_modbus)
        layout.addWidget(self.mb_button)
        self.mb_result = QPlainTextEdit(); self.mb_result.setReadOnly(True); self.mb_result.setPlaceholderText("Kết quả và dữ liệu phản hồi sẽ hiện ở đây.")
        layout.addWidget(self.mb_result, 1)
        return page

    def make_raw_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        box = QGroupBox("Gửi/nhận byte HEX")
        form = QFormLayout(box)
        self.raw_tx = QLineEdit("01 03 00 00 00 01 84 0A")
        self.raw_wait = QSpinBox(); self.raw_wait.setRange(50, 5000); self.raw_wait.setValue(500); self.raw_wait.setSuffix(" ms")
        form.addRow("Dữ liệu gửi (HEX)", self.raw_tx)
        form.addRow("Thời gian chờ", self.raw_wait)
        layout.addWidget(box)
        self.raw_button = QPushButton("Gửi HEX")
        self.raw_button.clicked.connect(self.run_raw)
        layout.addWidget(self.raw_button)
        self.raw_result = QPlainTextEdit(); self.raw_result.setReadOnly(True); self.raw_result.setPlaceholderText("Byte gửi và byte nhận sẽ hiện ở đây.")
        layout.addWidget(self.raw_result, 1)
        return page

    def launch_job(self, button, output, operation, formatter):
        button.setEnabled(False)
        job_id = self.next_job_id
        self.next_job_id += 1
        thread = QThread(self)
        job = Job(job_id, operation)
        job.moveToThread(thread)
        job.done.connect(self.finish_job)
        thread.started.connect(job.run)
        thread.finished.connect(job.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self.jobs[job_id] = (button, output, formatter, thread)
        thread.start()

    @Slot(int, object, object)
    def finish_job(self, job_id, result, error):
        button, output, formatter, thread = self.jobs.pop(job_id)
        button.setEnabled(True)
        if error:
            output.appendPlainText(f"[{datetime.now():%H:%M:%S}] LỖI: {error}")
        else:
            output.appendPlainText(f"[{datetime.now():%H:%M:%S}] {formatter(result)}")
        thread.quit()

    def run_modbus(self):
        fn = self.functions[self.function.currentText()]
        vals_text = self.values.text().strip()
        try:
            values = [int(x.strip(), 0) for x in vals_text.split(",")] if vals_text else []
        except ValueError:
            self.mb_result.appendPlainText("Giá trị ghi không hợp lệ. Có thể nhập thập phân hoặc 0xHEX.")
            return
        if fn in (5, 6, 15, 16) and QMessageBox.question(
            self, "Xác nhận ghi I/O", f"Gửi lệnh ghi Modbus FC{fn} tới {self.port.currentText()}, Slave {self.slave.value()}, địa chỉ {self.address.value()}?") != QMessageBox.StandardButton.Yes:
            return
        port = self.port.currentText().strip()
        args = (port, self.slave.value(), fn, self.address.value(), self.quantity.value(), values)
        def fmt(pair):
            request, response = pair
            body = response[2:-2] if fn in (1, 2, 3, 4) else response[2:6]
            return (f"OK · TX {request.hex(' ').upper()} · RX {response.hex(' ').upper()}\n"
                    f"Dữ liệu phản hồi: {body.hex(' ').upper() or '(không có)'}\n"
                    "Giá trị coil/register hiển thị theo byte thô; tham khảo đúng manual thiết bị.")
        self.launch_job(self.mb_button, self.mb_result, lambda: modbus_exchange(*args), fmt)

    def run_raw(self):
        try:
            tokens = self.raw_tx.text().replace(",", " ").split()
            payload = bytes(int(token.removeprefix("0x").removeprefix("0X"), 16) for token in tokens)
            if not payload:
                raise ValueError("Chưa nhập byte cần gửi.")
        except ValueError as exc:
            self.raw_result.appendPlainText(f"HEX không hợp lệ: {exc}")
            return
        port, wait_ms = self.port.currentText().strip(), self.raw_wait.value()
        def fmt(pair):
            tx, rx = pair
            return f"TX ({len(tx)} byte): {tx.hex(' ').upper()}\nRX ({len(rx)} byte): {rx.hex(' ').upper() or '(timeout, không có dữ liệu)'}"
        self.launch_job(self.raw_button, self.raw_result, lambda: raw_exchange(port, payload, wait_ms), fmt)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
