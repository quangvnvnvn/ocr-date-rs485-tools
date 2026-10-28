"""Offscreen end-to-end test of the hardware-trigger UI without a camera."""
import atexit
import json
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
PROJECT = Path(__file__).resolve().parent
TEST_CONFIG = PROJECT / "data/debug/test_gui_camera_config.json"
TEST_CONFIG.write_text((PROJECT / "config/config.json").read_text(encoding="utf-8"), encoding="utf-8")
os.environ["OCR_DATE_CONFIG"] = str(TEST_CONFIG)

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QApplication

from gui import MainWindow, ROOT


original_config = TEST_CONFIG.read_text(encoding="utf-8")
atexit.register(lambda: TEST_CONFIG.write_text(original_config, encoding="utf-8"))
app = QApplication([])
window = MainWindow(); window.show(); app.processEvents()
window.load_path(ROOT / "data/test_images/date1.jpg", use_sample=True)
window.source_mode.setCurrentIndex(window.source_mode.findData("simulation"))
window.roi_mode.setCurrentIndex(window.roi_mode.findData("auto"))
window.ab.setText("12"); window.dm.setCurrentText("1")
window.threshold.setValue(.70); window.max_angle.setValue(3.0)
window.date.setDate(QDate(2026, 8, 22))
state = {"armed": False, "result": None}


def armed(info):
    if info.get("ready") and not state["armed"]:
        state["armed"] = True
        QTimer.singleShot(0, window.simulate_trigger)


def result(report, folder, artifacts):
    try:
        state["result"] = report
        assert report["validation"]["result"] == "PASS", report["validation"]
        assert report["deadline_met"], report["processing_ms"]
        assert report["hardware"]["external_trigger_only"] is True
        assert report["hardware"]["provider"] == "simulation"
        assert artifacts["original"].shape[:2] == window.image.shape[:2]
        window.grab().save(str(ROOT / "data/debug/gui_camera_simulation.png"))
        QTimer.singleShot(0, window.disconnect_camera)
    except Exception:
        import traceback
        traceback.print_exc(); app.exit(1)


def finished():
    if state["result"] is None:
        print("No simulated camera result", file=sys.stderr); app.exit(1); return
    report = state["result"]
    (ROOT / "data/debug/gui_camera_simulation.json").write_text(json.dumps({
        "source": "simulation", "external_trigger_only": True,
        "result": report["validation"]["result"],
        "processing_ms": report["processing_ms"],
        "deadline_met": report["deadline_met"]
    }, indent=2), encoding="utf-8")
    print("GUI_CAMERA_SIMULATION_PASS", report["processing_ms"], "ms", flush=True)
    window.close(); TEST_CONFIG.write_text(original_config, encoding="utf-8"); app.quit()


window.camera_worker.armed.connect(armed)
window.camera_worker.result.connect(result)
window.camera_worker.finished.connect(finished)
QTimer.singleShot(0, window.connect_camera)
QTimer.singleShot(90000, lambda: app.exit(2))
sys.exit(app.exec())

