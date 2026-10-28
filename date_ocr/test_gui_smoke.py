import os,sys,json,atexit
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
PROJECT=Path(__file__).resolve().parent
TEST_CONFIG=PROJECT/'data/debug/test_gui_smoke_config.json'
TEST_CONFIG.write_text((PROJECT/'config/config.json').read_text(encoding='utf-8'),encoding='utf-8')
os.environ['OCR_DATE_CONFIG']=str(TEST_CONFIG)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer,QPoint,Qt,QDate
from PySide6.QtTest import QTest
from gui import MainWindow,ROOT
original_config=TEST_CONFIG.read_text(encoding='utf-8')
atexit.register(lambda:TEST_CONFIG.write_text(original_config,encoding='utf-8'))
app=QApplication([]);w=MainWindow();w.show();app.processEvents()
w.roi_mode.setCurrentIndex(w.roi_mode.findData('auto'))
w.date.setDate(QDate(2026,8,22))
assert w.roi_mode.currentData()=='auto'
assert w.date.isEnabled()
assert w.check_date.isChecked()
assert w.date.date().toString('yyyy-MM-dd')=='2026-08-22'
a=w.original.rect_image();w.select_roi()
QTest.mousePress(w.original,Qt.LeftButton,pos=QPoint(int(a.x()+a.width()*.4),int(a.y()+a.height()*.4)))
QTest.mouseRelease(w.original,Qt.LeftButton,pos=QPoint(int(a.x()+a.width()*.7),int(a.y()+a.height()*.6)))
assert w.roi is not None and .28<w.roi.width/w.roi.source_width<.32
w.load_path(ROOT/'data/test_images/date1.jpg',use_sample=True)
ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(50)
def done():
 try:
  assert len(w.rows)==5,len(w.rows)
  assert all(r['validation']['result']=='PASS' for r,f in w.rows)
  assert len(ticks)>5
  w.grab().save(str(ROOT/'data/debug/gui_smoke.png'))
  (ROOT/'data/debug/gui_smoke.json').write_text(json.dumps({'images':5,'pass':5,'ui_heartbeat_ticks':len(ticks),'roi_mouse_mapping':'passed'}))
  print('GUI_SMOKE_PASS',len(ticks),flush=True)
 except Exception:
  import traceback;traceback.print_exc();app.exit(1);return
 w.close();TEST_CONFIG.write_text(original_config,encoding='utf-8');app.quit()
w.worker.finished.connect(done)
w.ab.setText('12')
w.threshold.setValue(.70);w.max_angle.setValue(3.0)
QTimer.singleShot(0,w.samples);QTimer.singleShot(90000,lambda:app.exit(2))
sys.exit(app.exec())
