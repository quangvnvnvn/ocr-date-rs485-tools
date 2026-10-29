"""Timed reject output for Waveshare Modbus RTU IO 8CH (DO1..DO8).

The output is active only while a hardware-triggered camera is armed. An
acknowledged write and readback are required; a failed transaction stops the
camera path. Timing is relative to the inspection decision and must be calibrated on the
actual conveyor before enabling physical output.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import threading
import time


class EjectorError(RuntimeError):
    pass


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc.to_bytes(2, "little")


@dataclass(frozen=True)
class EjectorSettings:
    port: str = ""
    baudrate: int = 9600
    slave: int = 1
    channel: int = 1  # physical DO1; Modbus coil 0x0000
    decision_delay_ms: int = 0
    pulse_ms: int = 100
    enabled: bool = False

    def validate(self) -> None:
        if not self.port.strip():
            raise EjectorError("Chưa chọn cổng COM cho RS485")
        if self.baudrate not in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
            raise EjectorError("Baud rate RS485 không hợp lệ")
        if not 1 <= self.slave <= 255 or not 1 <= self.channel <= 8:
            raise EjectorError("Địa chỉ slave phải 1–255; kênh DO phải 1–8")
        if not 1 <= self.decision_delay_ms <= 60000:
            raise EjectorError("Phải cài delay từ quyết định NG đến xi lanh (1–60000 ms)")
        if not 20 <= self.pulse_ms <= 2000:
            raise EjectorError("Xung xi lanh phải nằm trong 20–2000 ms")


class ModbusOutput:
    def __init__(self, settings: EjectorSettings, serial_factory=None):
        self.settings = settings
        self.serial_factory = serial_factory
        self.serial = None

    def open(self) -> None:
        self.settings.validate()
        if self.serial_factory is None:
            import serial
            self.serial_factory = serial.Serial
        self.serial = self.serial_factory(
            self.settings.port, self.settings.baudrate, bytesize=8,
            parity="N", stopbits=1, timeout=0.12, write_timeout=0.12,
        )
        try:
            mode_request = bytes((self.settings.slave, 3, 0x10,
                                  self.settings.channel - 1, 0, 1))
            mode_response = self._exchange(mode_request, 7)
            if mode_response[1:3] != b"\x03\x02" or mode_response[3:5] != b"\x00\x00":
                raise EjectorError("DO chưa ở Normal mode; hãy đặt mode 0 bằng phần mềm hãng")
            self.set_output(False)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.serial is not None:
            try:
                self.set_output(False)
            finally:
                self.serial.close()
                self.serial = None

    def _exchange(self, payload: bytes, response_len: int) -> bytes:
        if self.serial is None:
            raise EjectorError("Cổng RS485 chưa mở")
        request = payload + crc16(payload)
        self.serial.reset_input_buffer()
        count = self.serial.write(request)
        if count != len(request):
            raise EjectorError("Gửi lệnh RS485 không đủ byte")
        response = self.serial.read(response_len)
        if len(response) != response_len or crc16(response[:-2]) != response[-2:]:
            raise EjectorError("RS485 không trả lời hoặc CRC sai")
        if response[0] != self.settings.slave:
            raise EjectorError("Sai địa chỉ thiết bị RS485")
        if response[1] & 0x80:
            raise EjectorError(f"Module báo lỗi Modbus {response[2]}")
        return response

    def set_output(self, enabled: bool) -> None:
        address = self.settings.channel - 1
        payload = bytes((self.settings.slave, 5, 0, address, 0xFF if enabled else 0, 0))
        if self._exchange(payload, 8) != payload + crc16(payload):
            raise EjectorError("Module không xác nhận lệnh DO")
        # Function 01 reads one coil. An echo alone cannot prove output state.
        read = bytes((self.settings.slave, 1, 0, address, 0, 1))
        response = self._exchange(read, 6)
        if response[1:3] != b"\x01\x01" or bool(response[3] & 1) != enabled:
            raise EjectorError("Trạng thái DO đọc lại không khớp lệnh")


class TimedEjector:
    def __init__(self, output: ModbusOutput, on_event):
        self.output = output
        self.on_event = on_event
        self.condition = threading.Condition()
        self.pending = []
        self.thread = None
        self.stopping = False
        self.failed = False
        self.sequence = 0
        self.active_until = 0.0

    def start(self) -> None:
        self.output.open()  # verifies DO off before camera is armed
        self.thread = threading.Thread(target=self._run, name="rs485-ejector", daemon=True)
        self.thread.start()

    def schedule(self, frame_id: int, decision_monotonic: float, result: str) -> float:
        with self.condition:
            if self.stopping or self.failed:
                raise EjectorError("Bộ đẩy RS485 đã dừng do lỗi")
            if result == "PASS":
                raise EjectorError("Không được lập lịch đẩy sản phẩm đạt")
            self.sequence += 1
            due = decision_monotonic + self.output.settings.decision_delay_ms / 1000
            if due <= time.perf_counter():
                raise EjectorError("Không kịp lập lịch xung xi lanh sau quyết định NG")
            if due < self.active_until:
                raise EjectorError("Sản phẩm NG tới khi xung xi lanh trước còn hoạt động")
            if self.pending and due < max(item[0] for item in self.pending) + self.output.settings.pulse_ms / 1000:
                raise EjectorError("Hai sản phẩm NG quá gần nhau cho một xung xi lanh")
            heapq.heappush(self.pending, (due, self.sequence, frame_id, result))
            self.condition.notify()
            return due

    def _run(self) -> None:
        while True:
            with self.condition:
                while not self.pending and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                due, sequence, frame_id, result = self.pending[0]
                remaining = due - time.perf_counter()
                if remaining > 0:
                    self.condition.wait(remaining)
                    continue
                heapq.heappop(self.pending)
            try:
                if time.perf_counter() - due > .05:
                    raise EjectorError("Lệnh đẩy trễ hơn 50 ms; không đẩy nhầm sản phẩm")
                with self.condition:
                    self.active_until = time.perf_counter() + self.output.settings.pulse_ms / 1000
                self.output.set_output(True)
                with self.condition:
                    self.active_until = time.perf_counter() + self.output.settings.pulse_ms / 1000
                self.on_event("ON", frame_id, result, time.perf_counter())
                time.sleep(self.output.settings.pulse_ms / 1000)
                self.output.set_output(False)
                self.on_event("OFF", frame_id, result, time.perf_counter())
                with self.condition:
                    self.active_until = 0.0
            except Exception as exc:
                # A failed ON acknowledgement may still have switched the valve.
                # Send OFF immediately instead of waiting for the UI to stop ARM.
                try:
                    self.output.set_output(False)
                except Exception as off_exc:
                    exc = EjectorError(f"{exc}; không xác nhận được DO OFF: {off_exc}")
                with self.condition:
                    self.failed = True
                    self.pending.clear()
                    self.stopping = True
                    self.condition.notify_all()
                self.on_event("ERROR", frame_id, str(exc), time.perf_counter())
                return

    def stop(self) -> None:
        with self.condition:
            self.stopping = True
            self.pending.clear()
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=3)
        self.output.close()
