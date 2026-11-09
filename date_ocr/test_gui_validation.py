"""Offscreen GUI integration check for the 13 handheld images."""
import json
import os
import sys
import atexit
from pathlib import Path

os.environ['QT_QPA_PLATFORM']='offscreen'
PROJECT=Path(__file__).resolve().parent
TEST_CONFIG=PROJECT/'data/debug/test_gui_validation_config.json'
TEST_CONFIG.write_text((PROJECT/'config/config.json').read_text(encoding='utf-8'),encoding='utf-8')
os.environ['OCR_DATE_CONFIG']=str(TEST_CONFIG)

from PySide6.QtCore import QTimer,QDate
from PySide6.QtWidgets import QApplication

from gui import MainWindow,ROOT


original_config=TEST_CONFIG.read_text(encoding='utf-8')
atexit.register(lambda:TEST_CONFIG.write_text(original_config,encoding='utf-8'))
app=QApplication([])
window=MainWindow();window.show();app.processEvents()
window.roi_mode.setCurrentIndex(window.roi_mode.findData('auto'))
window.trial_ab.setText('13');window.trial_dm.setCurrentText('1');window.threshold.setValue(.70)
window.trial_date.setDate(QDate(2026,8,22))
data=ROOT/'data/validation/testdate_20260906'
truth=json.loads((data/'manifest.json').read_text(encoding='utf-8'))
jobs=[(data/name,None,'auto') for name in truth]
ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(25)
phase={'value':'normal'}


def done():
    try:
        if phase['value']=='normal':
            assert len(window.rows)==13,len(window.rows)
            reports={report['image']:report for report,_ in window.rows}
            assert sum(report['validation']['result']=='PASS' for report in reports.values())==12
            assert reports[next(name for name in truth if name.startswith('1788666718782'))]['validation']['result']=='FAIL_ROI_NOT_FOUND'
            assert all(report['deadline_met'] for report in reports.values())
            for name,expected in truth.items():
                if expected['expected_result']=='PASS':
                    assert reports[name]['validation']['raw']==expected['raw'],name
                    assert 'alignment' not in reports[name],name
            assert len(ticks)>5,len(ticks)
            window.grab().save(str(ROOT/'data/debug/gui_validation.png'))
            phase['value']='deadline_fail';window.deadline_ms.setValue(1)
            failing_name=next(name for name in truth if name.startswith('1788666718633'))
            QTimer.singleShot(0,lambda:window.begin([(data/failing_name,None,'auto')]))
            return
        assert len(window.rows)==1
        report=window.rows[0][0]
        assert report['validation']['result']=='FAIL_DEADLINE',report['validation']
        assert report['deadline_ms']==1 and not report['deadline_met']
        assert 'alignment' not in report
        (ROOT/'data/debug/gui_validation.json').write_text(json.dumps({
            'images':13,'pass':12,'expected_reject':1,'deadline_met':13,
            'deadline_gate':'passed','forced_limit_ms':1,
            'ui_heartbeat_ticks':len(ticks)},indent=2),encoding='utf-8')
        print('GUI_VALIDATION_PASS',len(ticks),'DEADLINE_FAIL_PASS',flush=True)
    except Exception:
        import traceback
        traceback.print_exc();app.exit(1);return
    window.close();TEST_CONFIG.write_text(original_config,encoding='utf-8');app.quit()


window.worker.finished.connect(done)
QTimer.singleShot(0,lambda:window.begin(jobs))
QTimer.singleShot(90000,lambda:app.exit(2))
sys.exit(app.exec())
