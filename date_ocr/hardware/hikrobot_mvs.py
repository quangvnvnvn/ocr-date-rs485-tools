"""Optional HIKROBOT MVS adapter for externally hardware-triggered cameras.

MVS is loaded lazily from the vendor installation.  It is intentionally not a
Python package dependency and is not redistributed with this application.
"""
from __future__ import annotations

from ctypes import POINTER, byref, c_ubyte, cast, memset, sizeof
import importlib
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

import cv2
import numpy as np

from .frame_source import FramePacket, FrameSource, FrameSourceError


DEFAULT_MVIMPORT_DIRS = (
    Path(r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport"),
    Path(r"C:\Program Files\MVS\Development\Samples\Python\MvImport"),
)

DEFAULT_MVS_RUNTIME_DIRS = (
    Path(r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64"),
    Path(r"C:\Program Files\Common Files\MVS\Runtime\Win64_x64"),
)

# Keep handles alive for the lifetime of the process.  Python 3.8+ removes a
# directory from the Windows DLL search path when the returned handle closes.
_DLL_DIRECTORY_HANDLES: list[Any] = []


def discover_mvimport() -> Path:
    candidates = []
    for variable in ("HIKROBOT_MVIMPORT", "MVCAM_COMMON_RUNENV"):
        value = os.environ.get(variable)
        if value:
            root = Path(value)
            candidates.extend((root, root / "Samples/Python/MvImport", root / "MvImport"))
    candidates.extend(DEFAULT_MVIMPORT_DIRS)
    for candidate in candidates:
        if (candidate / "MvCameraControl_class.py").is_file():
            return candidate
    raise FrameSourceError(
        "Chưa tìm thấy Hikrobot MVS Python SDK. Hãy cài MVS 64-bit rồi đặt "
        "HIKROBOT_MVIMPORT tới thư mục chứa MvCameraControl_class.py."
    )


def _prepare_windows_dll_search() -> None:
    if os.name != "nt":
        return
    candidates: list[Path] = []
    runtime_override = os.environ.get("HIKROBOT_MVS_RUNTIME")
    if runtime_override:
        candidates.append(Path(runtime_override))
    candidates.extend(DEFAULT_MVS_RUNTIME_DIRS)

    existing_path = os.environ.get("PATH", "")
    path_parts = [part for part in existing_path.split(os.pathsep) if part]
    normalized = {os.path.normcase(os.path.abspath(part)) for part in path_parts}
    for directory in candidates:
        if not (directory / "MvCameraControl.dll").is_file():
            continue
        value = str(directory)
        key = os.path.normcase(os.path.abspath(value))
        if key not in normalized:
            path_parts.insert(0, value)
            normalized.add(key)
        add_directory = getattr(os, "add_dll_directory", None)
        if add_directory is not None:
            try:
                _DLL_DIRECTORY_HANDLES.append(add_directory(value))
            except OSError:
                pass
    os.environ["PATH"] = os.pathsep.join(path_parts)


def load_mvs_module():
    directory = discover_mvimport()
    _prepare_windows_dll_search()
    value = str(directory)
    if value not in sys.path:
        sys.path.insert(0, value)
    try:
        return importlib.import_module("MvCameraControl_class")
    except Exception as exc:
        raise FrameSourceError(f"Không nạp được Hikrobot MVS SDK: {exc}") from exc


def _decode_c_string(value) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("utf-8", errors="replace")


class HikrobotMvsSource(FrameSource):
    """Read frames caused only by the camera's external Line0/Line1 input."""

    def __init__(
        self,
        serial_number: str = "",
        trigger_source: str = "Line0",
        *,
        sdk: Any | None = None,
    ) -> None:
        if trigger_source not in {"Line0", "Line1"}:
            raise ValueError("Ngõ trigger chỉ hỗ trợ Line0 hoặc Line1")
        self.serial_number = serial_number.strip()
        self.trigger_source = trigger_source
        self.sdk = sdk
        self.camera = None
        self.device = None
        self.device_serial = ""
        self._running = False
        self._sdk_lock = threading.RLock()
        self._pending_software_tests = 0
        self._live_mode = False

    def _ok(self, result: int, action: str) -> None:
        ok = int(getattr(self.sdk, "MV_OK", 0))
        if int(result) != ok:
            raise FrameSourceError(f"Hikrobot MVS lỗi khi {action}: 0x{int(result) & 0xffffffff:08X}")

    def _device_serial(self, info) -> str:
        layer = int(info.nTLayerType)
        if layer == int(getattr(self.sdk, "MV_GIGE_DEVICE", -1)):
            return _decode_c_string(info.SpecialInfo.stGigEInfo.chSerialNumber)
        if layer == int(getattr(self.sdk, "MV_USB_DEVICE", -2)):
            return _decode_c_string(info.SpecialInfo.stUsb3VInfo.chSerialNumber)
        return ""

    def _enumerate(self):
        device_list = self.sdk.MV_CC_DEVICE_INFO_LIST()
        mask = int(getattr(self.sdk, "MV_GIGE_DEVICE", 0)) | int(
            getattr(self.sdk, "MV_USB_DEVICE", 0)
        )
        self._ok(self.sdk.MvCamera.MV_CC_EnumDevices(mask, device_list), "tìm camera")
        found = []
        for index in range(int(device_list.nDeviceNum)):
            info = cast(
                device_list.pDeviceInfo[index], POINTER(self.sdk.MV_CC_DEVICE_INFO)
            ).contents
            found.append((info, self._device_serial(info)))
        return found

    def open(self) -> None:
        if self.sdk is None:
            self.sdk = load_mvs_module()
        devices = self._enumerate()
        if not devices:
            raise FrameSourceError("MVS không tìm thấy camera Hikrobot GigE/USB")
        matches = [item for item in devices if not self.serial_number or item[1] == self.serial_number]
        if not matches:
            available = ", ".join(serial or "(không đọc được serial)" for _, serial in devices)
            raise FrameSourceError(
                f'Không tìm thấy camera serial "{self.serial_number}". Đang thấy: {available}'
            )
        self.device, self.device_serial = matches[0]
        self.camera = self.sdk.MvCamera()
        self._ok(self.camera.MV_CC_CreateHandle(self.device), "tạo handle")
        try:
            self._ok(
                self.camera.MV_CC_OpenDevice(getattr(self.sdk, "MV_ACCESS_Exclusive", 1), 0),
                "mở camera",
            )
            if int(self.device.nTLayerType) == int(getattr(self.sdk, "MV_GIGE_DEVICE", -1)):
                packet_size = int(self.camera.MV_CC_GetOptimalPacketSize())
                if packet_size > 0:
                    self._ok(
                        self.camera.MV_CC_SetIntValue("GevSCPSPacketSize", packet_size),
                        "đặt GigE packet size",
                    )
            self._ok(
                self.camera.MV_CC_SetEnumValue(
                    "TriggerMode", int(getattr(self.sdk, "MV_TRIGGER_MODE_ON", 1))
                ),
                "bật trigger cứng",
            )
            source_name = "MV_TRIGGER_SOURCE_" + self.trigger_source.upper()
            source_value = getattr(self.sdk, source_name, 0 if self.trigger_source == "Line0" else 1)
            self._ok(
                self.camera.MV_CC_SetEnumValue("TriggerSource", int(source_value)),
                f"chọn {self.trigger_source}",
            )
            self._live_mode = False
            self._pending_software_tests = 0
        except Exception:
            try:
                self.camera.MV_CC_CloseDevice()
                self.camera.MV_CC_DestroyHandle()
            finally:
                self.camera = None
            raise

    def start(self) -> None:
        if self.camera is None:
            raise FrameSourceError("Camera chưa được mở")
        self._ok(self.camera.MV_CC_StartGrabbing(), "ARM camera")
        self._running = True

    def _hardware_trigger_value(self) -> int:
        source_name = "MV_TRIGGER_SOURCE_" + self.trigger_source.upper()
        return int(
            getattr(self.sdk, source_name, 0 if self.trigger_source == "Line0" else 1)
        )

    def enable_live_view(self) -> None:
        """Temporarily free-run the camera for lens and lighting adjustment."""
        if self.camera is None:
            raise FrameSourceError("Camera chưa được mở")
        if self._running:
            raise FrameSourceError("Phải bật CAMERA LIVE trước khi bắt đầu nhận ảnh")
        with self._sdk_lock:
            self._ok(
                self.camera.MV_CC_SetEnumValue(
                    "TriggerMode", int(getattr(self.sdk, "MV_TRIGGER_MODE_OFF", 0))
                ),
                "bật CAMERA LIVE",
            )
            self._live_mode = True

    def disable_live_view(self) -> None:
        """Restore fail-closed external trigger settings after live preview."""
        if self.camera is None:
            self._live_mode = False
            return
        if self._running:
            raise FrameSourceError("Phải dừng nhận ảnh trước khi khôi phục trigger cứng")
        with self._sdk_lock:
            self._ok(
                self.camera.MV_CC_SetEnumValue(
                    "TriggerMode", int(getattr(self.sdk, "MV_TRIGGER_MODE_ON", 1))
                ),
                "khôi phục trigger cứng",
            )
            self._ok(
                self.camera.MV_CC_SetEnumValue(
                    "TriggerSource", self._hardware_trigger_value()
                ),
                f"khôi phục {self.trigger_source}",
            )
            self._live_mode = False

    def software_trigger_once(self) -> None:
        """Generate one diagnostic frame, then restore the external trigger input.

        This command is intended only for an operator connection test.  Normal
        production acquisition remains armed on Line0/Line1 and still requires
        the physical hardware trigger.
        """
        if not self._running or self.camera is None:
            raise FrameSourceError("Camera chưa ARM")
        if self._live_mode:
            raise FrameSourceError("Hãy dừng CAMERA LIVE trước khi phát trigger test")
        software_value = int(getattr(self.sdk, "MV_TRIGGER_SOURCE_SOFTWARE", 7))
        hardware_value = self._hardware_trigger_value()
        with self._sdk_lock:
            self._ok(
                self.camera.MV_CC_SetEnumValue("TriggerSource", software_value),
                "chuyển tạm sang software trigger",
            )
            command_succeeded = False
            try:
                self._ok(
                    self.camera.MV_CC_SetCommandValue("TriggerSoftware"),
                    "phát software trigger thử",
                )
                command_succeeded = True
                self._pending_software_tests += 1
            finally:
                restore_result = self.camera.MV_CC_SetEnumValue(
                    "TriggerSource", hardware_value
                )
                if int(restore_result) != int(getattr(self.sdk, "MV_OK", 0)):
                    if command_succeeded:
                        self._pending_software_tests = max(
                            0, self._pending_software_tests - 1
                        )
                    self._ok(restore_result, f"khôi phục {self.trigger_source}")

    def _pixel_constant(self, name: str) -> int | None:
        value = getattr(self.sdk, name, None)
        return None if value is None else int(value)

    def _decode_frame(self, frame) -> np.ndarray:
        info = frame.stFrameInfo
        width, height = int(info.nWidth), int(info.nHeight)
        length, pixel_type = int(info.nFrameLen), int(info.enPixelType)
        raw = np.ctypeslib.as_array(
            cast(frame.pBufAddr, POINTER(c_ubyte)), shape=(length,)
        ).copy()
        mono8 = self._pixel_constant("PixelType_Gvsp_Mono8")
        if pixel_type == mono8 and length >= width * height:
            gray = raw[: width * height].reshape(height, width)
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        color_formats = {
            self._pixel_constant("PixelType_Gvsp_BayerRG8"): cv2.COLOR_BAYER_RG2BGR,
            self._pixel_constant("PixelType_Gvsp_BayerGB8"): cv2.COLOR_BAYER_GB2BGR,
            self._pixel_constant("PixelType_Gvsp_BayerGR8"): cv2.COLOR_BAYER_GR2BGR,
            self._pixel_constant("PixelType_Gvsp_BayerBG8"): cv2.COLOR_BAYER_BG2BGR,
        }
        if pixel_type in color_formats and length >= width * height:
            return cv2.cvtColor(raw[: width * height].reshape(height, width), color_formats[pixel_type])
        if pixel_type == self._pixel_constant("PixelType_Gvsp_BGR8_Packed") and length >= width * height * 3:
            return raw[: width * height * 3].reshape(height, width, 3)
        if pixel_type == self._pixel_constant("PixelType_Gvsp_RGB8_Packed") and length >= width * height * 3:
            return cv2.cvtColor(
                raw[: width * height * 3].reshape(height, width, 3), cv2.COLOR_RGB2BGR
            )
        raise FrameSourceError(
            f"Pixel format 0x{pixel_type:08X} chưa hỗ trợ; đặt camera Mono8/Bayer8/RGB8 trong MVS"
        )

    def get_frame(self, timeout_ms: int) -> FramePacket | None:
        if not self._running or self.camera is None:
            raise FrameSourceError("Camera chưa ARM")
        with self._sdk_lock:
            frame = self.sdk.MV_FRAME_OUT()
            memset(byref(frame), 0, sizeof(frame))
            result = int(self.camera.MV_CC_GetImageBuffer(frame, int(timeout_ms)))
            timeout_codes = {
                int(value)
                for name in ("MV_E_NODATA", "MV_E_TIMEOUT")
                if (value := getattr(self.sdk, name, None)) is not None
            }
            if result in timeout_codes:
                return None
            self._ok(result, "nhận frame trigger")
            received = time.perf_counter()
            try:
                image = self._decode_frame(frame)
                info = frame.stFrameInfo
                frame_id = int(getattr(info, "nFrameNum", 0))
                high = int(getattr(info, "nDevTimeStampHigh", 0))
                low = int(getattr(info, "nDevTimeStampLow", 0))
                device_timestamp = (high << 32) | low if high or low else None
                software_test = not self._live_mode and self._pending_software_tests > 0
                if software_test:
                    self._pending_software_tests -= 1
                trigger_mode = (
                    "live_preview" if self._live_mode
                    else "software_test" if software_test
                    else "hardware"
                )
                trigger_source = (
                    "Continuous" if self._live_mode
                    else "Software" if software_test
                    else self.trigger_source
                )
                return FramePacket(
                    frame_id=frame_id,
                    image=np.ascontiguousarray(image),
                    received_monotonic=received,
                    source=f"hikrobot://{self.device_serial or 'camera'}",
                    device_timestamp=device_timestamp,
                    metadata={
                        "provider": "hikrobot_mvs",
                        "serial_number": self.device_serial,
                        "trigger_mode": trigger_mode,
                        "trigger_source": trigger_source,
                        "software_test": software_test,
                        "pixel_type": int(info.enPixelType),
                        "width": int(info.nWidth),
                        "height": int(info.nHeight),
                    },
                )
            finally:
                self._ok(self.camera.MV_CC_FreeImageBuffer(frame), "trả buffer frame")

    def stop(self) -> None:
        if self.camera is not None and self._running:
            self._ok(self.camera.MV_CC_StopGrabbing(), "dừng nhận frame")
        self._running = False

    def close(self) -> None:
        if self.camera is not None:
            try:
                if self._running:
                    self.stop()
                if self._live_mode:
                    self.disable_live_view()
            finally:
                try:
                    self.camera.MV_CC_CloseDevice()
                finally:
                    self.camera.MV_CC_DestroyHandle()
                    self.camera = None
                    self._live_mode = False
                    self._pending_software_tests = 0

