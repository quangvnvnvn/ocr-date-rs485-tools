import ctypes
import os
import unittest
from unittest.mock import patch

import numpy as np

from hardware.frame_source import FrameSourceError, missing_frame_count
from hardware.hikrobot_mvs import HikrobotMvsSource, discover_mvimport
from hardware.simulated_source import TriggeredImageSource


class TriggeredImageSourceTests(unittest.TestCase):
    def test_frame_number_gap_is_detected(self):
        self.assertEqual(missing_frame_count(None, 10), 0)
        self.assertEqual(missing_frame_count(10, 11), 0)
        self.assertEqual(missing_frame_count(10, 14), 3)
        self.assertEqual(missing_frame_count(0, 14), 0)

    def test_trigger_requires_arm_and_copies_image(self):
        source = TriggeredImageSource()
        image = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        with self.assertRaises(FrameSourceError):
            source.trigger(image)
        source.open(); source.start()
        packet = source.trigger(image, "sample")
        image[:] = 0
        received = source.get_frame(5)
        self.assertEqual(received.frame_id, packet.frame_id)
        self.assertTrue(np.any(received.image))
        self.assertEqual(received.metadata["trigger"], "manual")
        self.assertIsNone(source.get_frame(1))
        source.stop(); source.close()

    def test_full_queue_is_explicit_fail_busy(self):
        source = TriggeredImageSource(); source.open(); source.start()
        source.trigger(np.zeros((2, 2, 3), np.uint8))
        with self.assertRaisesRegex(FrameSourceError, "FAIL_BUSY"):
            source.trigger(np.zeros((2, 2, 3), np.uint8))
        self.assertEqual(source.rejected_busy, 1)

    def test_missing_sdk_has_actionable_error(self):
        clean_env = {key: value for key, value in os.environ.items()
                     if key not in {"HIKROBOT_MVIMPORT", "MVCAM_COMMON_RUNENV"}}
        with patch.dict(os.environ, clean_env, clear=True), \
             patch("hardware.hikrobot_mvs.DEFAULT_MVIMPORT_DIRS", ()):
            with self.assertRaisesRegex(FrameSourceError, "Hikrobot MVS Python SDK"):
                discover_mvimport()


class _GigEInfo(ctypes.Structure):
    _fields_ = [("chSerialNumber", ctypes.c_ubyte * 64)]


class _UsbInfo(ctypes.Structure):
    _fields_ = [("chSerialNumber", ctypes.c_ubyte * 64)]


class _SpecialInfo(ctypes.Union):
    _fields_ = [("stGigEInfo", _GigEInfo), ("stUsb3VInfo", _UsbInfo)]


class _DeviceInfo(ctypes.Structure):
    _fields_ = [("nTLayerType", ctypes.c_uint), ("SpecialInfo", _SpecialInfo)]


class _DeviceList(ctypes.Structure):
    _fields_ = [("nDeviceNum", ctypes.c_uint),
                ("pDeviceInfo", ctypes.POINTER(ctypes.POINTER(_DeviceInfo)))]


class _FrameInfo(ctypes.Structure):
    _fields_ = [("nWidth", ctypes.c_uint), ("nHeight", ctypes.c_uint),
                ("nFrameLen", ctypes.c_uint), ("enPixelType", ctypes.c_uint),
                ("nFrameNum", ctypes.c_uint), ("nDevTimeStampHigh", ctypes.c_uint),
                ("nDevTimeStampLow", ctypes.c_uint)]


class _FrameOut(ctypes.Structure):
    _fields_ = [("pBufAddr", ctypes.POINTER(ctypes.c_ubyte)),
                ("stFrameInfo", _FrameInfo)]


class FakeMvsSdk:
    MV_OK = 0
    MV_GIGE_DEVICE = 1
    MV_USB_DEVICE = 2
    MV_ACCESS_Exclusive = 1
    MV_TRIGGER_MODE_OFF = 0
    MV_TRIGGER_MODE_ON = 1
    MV_TRIGGER_SOURCE_LINE0 = 0
    MV_TRIGGER_SOURCE_SOFTWARE = 7
    MV_E_NODATA = -1
    PixelType_Gvsp_Mono8 = 0x01080001
    MV_CC_DEVICE_INFO = _DeviceInfo
    MV_CC_DEVICE_INFO_LIST = _DeviceList
    MV_FRAME_OUT = _FrameOut
    _info = _DeviceInfo()
    _info.nTLayerType = MV_GIGE_DEVICE
    _serial = b"HIK123\0"
    for _index, _byte in enumerate(_serial):
        _info.SpecialInfo.stGigEInfo.chSerialNumber[_index] = _byte
    _info_pointer = ctypes.pointer(_info)
    _device_array = (ctypes.POINTER(_DeviceInfo) * 1)(_info_pointer)
    instances = []

    class MvCamera:
        @staticmethod
        def MV_CC_EnumDevices(mask, device_list):
            device_list.nDeviceNum = 1
            device_list.pDeviceInfo = FakeMvsSdk._device_array
            return 0

        def __init__(self):
            self.calls = []
            self._buffer = (ctypes.c_ubyte * 6)(0, 10, 20, 30, 40, 50)
            FakeMvsSdk.instances.append(self)

        def MV_CC_CreateHandle(self, info): self.calls.append(("create",)); return 0
        def MV_CC_OpenDevice(self, access, index): self.calls.append(("open", access)); return 0
        def MV_CC_GetOptimalPacketSize(self): return 1500
        def MV_CC_SetIntValue(self, key, value): self.calls.append((key, value)); return 0
        def MV_CC_SetEnumValue(self, key, value): self.calls.append((key, value)); return 0
        def MV_CC_SetCommandValue(self, key): self.calls.append((key,)); return 0
        def MV_CC_StartGrabbing(self): self.calls.append(("start",)); return 0
        def MV_CC_StopGrabbing(self): self.calls.append(("stop",)); return 0
        def MV_CC_CloseDevice(self): self.calls.append(("close",)); return 0
        def MV_CC_DestroyHandle(self): self.calls.append(("destroy",)); return 0
        def MV_CC_FreeImageBuffer(self, frame): self.calls.append(("free",)); return 0
        def MV_CC_GetImageBuffer(self, frame, timeout):
            frame.pBufAddr = ctypes.cast(self._buffer, ctypes.POINTER(ctypes.c_ubyte))
            frame.stFrameInfo.nWidth = 3
            frame.stFrameInfo.nHeight = 2
            frame.stFrameInfo.nFrameLen = 6
            frame.stFrameInfo.enPixelType = FakeMvsSdk.PixelType_Gvsp_Mono8
            frame.stFrameInfo.nFrameNum = 42
            frame.stFrameInfo.nDevTimeStampLow = 123
            return 0


class HikrobotSourceContractTests(unittest.TestCase):
    def setUp(self):
        FakeMvsSdk.instances.clear()

    def test_external_trigger_configuration_and_mono_frame(self):
        source = HikrobotMvsSource("HIK123", "Line0", sdk=FakeMvsSdk)
        source.open(); source.start()
        packet = source.get_frame(20)
        camera = FakeMvsSdk.instances[-1]
        self.assertIn(("TriggerMode", 1), camera.calls)
        self.assertIn(("TriggerSource", 0), camera.calls)
        self.assertEqual(packet.frame_id, 42)
        self.assertEqual(packet.image.shape, (2, 3, 3))
        self.assertEqual(packet.metadata["trigger_mode"], "hardware")
        self.assertEqual(packet.metadata["serial_number"], "HIK123")
        self.assertIn(("free",), camera.calls)
        source.stop(); source.close()

    def test_wrong_serial_fails_closed(self):
        source = HikrobotMvsSource("OTHER", sdk=FakeMvsSdk)
        with self.assertRaisesRegex(FrameSourceError, "HIK123"):
            source.open()

    def test_software_test_trigger_restores_line0_and_marks_one_frame(self):
        source = HikrobotMvsSource("HIK123", "Line0", sdk=FakeMvsSdk)
        source.open(); source.start()
        source.software_trigger_once()
        packet = source.get_frame(20)
        camera = FakeMvsSdk.instances[-1]
        software_index = camera.calls.index(("TriggerSource", 7))
        command_index = camera.calls.index(("TriggerSoftware",))
        restore_index = len(camera.calls) - 1 - camera.calls[::-1].index(("TriggerSource", 0))
        self.assertLess(software_index, command_index)
        self.assertLess(command_index, restore_index)
        self.assertEqual(packet.metadata["trigger_mode"], "software_test")
        self.assertEqual(packet.metadata["trigger_source"], "Software")
        self.assertTrue(packet.metadata["software_test"])
        next_packet = source.get_frame(20)
        self.assertEqual(next_packet.metadata["trigger_mode"], "hardware")
        self.assertEqual(next_packet.metadata["trigger_source"], "Line0")
        self.assertFalse(next_packet.metadata["software_test"])
        source.stop(); source.close()

    def test_live_view_is_continuous_and_restores_external_trigger(self):
        source = HikrobotMvsSource("HIK123", "Line0", sdk=FakeMvsSdk)
        source.open()
        source.enable_live_view()
        source.start()
        packet = source.get_frame(20)
        self.assertEqual(packet.metadata["trigger_mode"], "live_preview")
        self.assertEqual(packet.metadata["trigger_source"], "Continuous")
        with self.assertRaisesRegex(FrameSourceError, "dừng CAMERA LIVE"):
            source.software_trigger_once()
        source.stop()
        source.disable_live_view()
        camera = FakeMvsSdk.instances[-1]
        self.assertIn(("TriggerMode", 0), camera.calls)
        restore_mode_index = len(camera.calls) - 1 - camera.calls[::-1].index(("TriggerMode", 1))
        restore_source_index = len(camera.calls) - 1 - camera.calls[::-1].index(("TriggerSource", 0))
        self.assertLess(restore_mode_index, restore_source_index)
        source.close()


if __name__ == "__main__":
    unittest.main()
