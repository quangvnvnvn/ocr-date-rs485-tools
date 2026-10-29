"""Verify that the operator's reject timing survives an application restart."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from gui import EjectTestWorker, MainWindow
from hardware.rs485_ejector import EjectorSettings, ModbusOutput
from test_rs485_ejector import FakeSerial


class EjectorSettingsGUITests(unittest.TestCase):
    def test_explicit_fake_ng_runs_one_delayed_pulse(self):
        app = QApplication.instance() or QApplication([])
        fake = FakeSerial()
        events = []
        worker = EjectTestWorker()
        worker.settings = EjectorSettings(port='COM9', decision_delay_ms=60,
                                          pulse_ms=30)
        worker.event.connect(events.append)
        with patch('gui.ModbusOutput', side_effect=lambda settings:
                   ModbusOutput(settings, lambda *args, **kwargs: fake)):
            worker.start()
            deadline = time.perf_counter() + 2
            while worker.isRunning() and time.perf_counter() < deadline:
                app.processEvents()
                time.sleep(.005)
            worker.wait(1000)
            app.processEvents()
        self.assertEqual([event['phase'] for event in events],
                         ['SCHEDULED', 'ON', 'OFF'])
        self.assertGreaterEqual(events[1]['monotonic'] - events[0]['monotonic'], .055)
        self.assertFalse(fake.coils[0])
        del app

    def test_decision_delay_is_saved_and_restored(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            config_path.write_text(json.dumps({'roi_mode': 'auto'}), encoding='utf-8')
            previous = os.environ.get('OCR_DATE_CONFIG')
            os.environ['OCR_DATE_CONFIG'] = str(config_path)
            try:
                first = MainWindow()
                self.assertEqual([first.mode_tabs.tabText(i) for i in range(4)],
                                 ['Chạy thật','Chạy thử','Test chức năng','Cài đặt'])
                first.mode_tabs.setCurrentIndex(0)
                self.assertEqual(first.source_mode.currentData(),'hikrobot')
                self.assertFalse(first.camera_worker.isRunning())
                first.mode_tabs.setCurrentIndex(1)
                self.assertEqual(first.source_mode.currentData(),'static')
                first.mode_tabs.setCurrentIndex(3)
                first.eject_port.setCurrentText('COM9')
                first.eject_channel.setCurrentText('2')
                first.eject_delay.setValue(525)
                first.eject_pulse.setValue(110)
                first.save_operator_settings()
                first.close()

                second = MainWindow()
                self.assertEqual(second.mode_tabs.currentIndex(),3)
                self.assertEqual(second.eject_port.currentText(), 'COM9')
                self.assertEqual(second.eject_channel.currentText(), '2')
                self.assertEqual(second.eject_delay.value(), 525)
                self.assertEqual(second.eject_pulse.value(), 110)
                self.assertFalse(second.eject_enabled.isChecked())
                second.close()
                saved = json.loads(config_path.read_text(encoding='utf-8'))
                self.assertEqual(saved['ejector']['decision_delay_ms'], 525)
                self.assertNotIn('travel_ms', saved['ejector'])
            finally:
                if previous is None:
                    os.environ.pop('OCR_DATE_CONFIG', None)
                else:
                    os.environ['OCR_DATE_CONFIG'] = previous
        del app


if __name__ == '__main__':
    unittest.main()
