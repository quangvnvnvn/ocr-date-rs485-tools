"""Offscreen check that the explicit Save Configuration round-trips all settings."""
import json
import os
from pathlib import Path

os.environ['QT_QPA_PLATFORM']='offscreen'
PROJECT=Path(__file__).resolve().parent
TEST_CONFIG=PROJECT/'data/debug/test_gui_persistence_config.json'
TEST_CONFIG.write_text((PROJECT/'config/config.json').read_text(encoding='utf-8'),
                       encoding='utf-8')
os.environ['OCR_DATE_CONFIG']=str(TEST_CONFIG)

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from gui import MainWindow,ROOT
from roi.fixed_roi import FixedROI


app=QApplication([])
first=MainWindow();first.show();app.processEvents()
last_image=ROOT/'data/test_images/date2.jpg'
first.load_path(last_image)
first.ab.setText('07');first.dm.setCurrentText('3')
first.threshold.setValue(.83);first.max_angle.setValue(4.2)
first.date.setDate(QDate(2027,3,14))
first.source_mode.setCurrentIndex(first.source_mode.findData('hikrobot'))
first.camera_serial.setText('MV-TEST-001');first.trigger_line.setCurrentText('Line1')
first.roi_mode.setCurrentIndex(first.roi_mode.findData('fixed'))
first.roi=FixedROI(700,900,600,240,1926,2568);first.update_roi()
first.save_configuration();app.processEvents()
saved=json.loads(TEST_CONFIG.read_text(encoding='utf-8'))
assert saved['roi_mode']=='fixed'
assert saved['inspection']=={
    'expected_ab':'07','expected_dm':3,'minimum_confidence':.83,
    'check_nsx_date':True,'expected_date':'2027-03-14',
    'maximum_angle_deviation':4.2}
assert saved['fixed_roi']['x']==700 and saved['fixed_roi']['width']==600
assert saved['hardware']['source_mode']=='hikrobot'
assert saved['hardware']['serial_number']=='MV-TEST-001'
assert saved['hardware']['trigger_source']=='Line1'
assert saved['hardware']['external_trigger_only'] is True
assert Path(saved['last_image']).resolve()==last_image.resolve()
first.close()

second=MainWindow();second.show();app.processEvents()
assert second.ab.text()=='07'
assert second.dm.currentText()=='3'
assert second.threshold.value()==.83
assert second.max_angle.value()==4.2
assert second.date.date().toString('yyyy-MM-dd')=='2027-03-14'
assert second.roi_mode.currentData()=='fixed'
assert second.source_mode.currentData()=='hikrobot'
assert second.camera_serial.text()=='MV-TEST-001'
assert second.trigger_line.currentText()=='Line1'
assert second.roi is not None and second.roi.x==700 and second.roi.width==600
assert second.path.resolve()==last_image.resolve()
second.grab().save(str(ROOT/'data/debug/gui_persistence.png'))
second.close();app.quit()
print('GUI_PERSISTENCE_PASS',flush=True)
