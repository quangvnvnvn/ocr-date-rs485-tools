from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import numpy as np
from production_log import DailyProductionLog, VIETNAM

def report(code='PASS'):
    return dict(image='frame.png',source='camera',processing_ms=42.5,
        hardware={'frame_id':7},expected={'expected_date':'2026-08-22','ab':'05','dm':1},
        alignment={'deviation':1.25},validation=dict(result=code,raw='=1+1',
        parsed={'year':2026,'month':8,'day':22,'ab':'05','dm':1,'time':'09:17'},
        overall_confidence=.95,error_reason='' if code=='PASS' else 'Sai mã AB'))

class ProductionLogTests(unittest.TestCase):
    def test_two_camera_sessions_cannot_write_same_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=DailyProductionLog(tmp)
            with self.assertRaises(RuntimeError):DailyProductionLog(tmp)
            first.close()
            reopened=DailyProductionLog(tmp);reopened.close()
    def test_daily_rollover_restart_and_typed_excel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);log=DailyProductionLog(path,interval=100)
            log.append(report(),datetime(2026,10,29,23,59,59,tzinfo=VIETNAM))
            log.append(report('FAIL_AB'),datetime(2026,10,30,0,0,1,tzinfo=VIETNAM));log.close()
            log=DailyProductionLog(path,interval=100)
            log.append(report(),datetime(2026,10,29,12,0,tzinfo=VIETNAM));log.close()
            self.assertEqual(len(list(path.glob('*.xlsx'))),2)
            with ZipFile(path/'OCR_DATE_2026-10-29.xlsx') as z:
                self.assertIsNone(z.testzip())
                root=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                self.assertEqual(len(root.findall('s:sheetData/s:row',ns)),3)
                cells={c.attrib['r']:c for c in root.findall('.//s:c',ns)}
                self.assertEqual(cells['A2'].attrib['t'],'n')
                self.assertEqual(cells['C2'].attrib['t'],'n')
                self.assertEqual(cells['E2'].find('s:is/s:t',ns).text,'05')
                self.assertEqual(cells['P2'].find('s:is/s:t',ns).text,'=1+1')
                self.assertEqual(cells['G2'].find('s:v',ns).text,'0.95')
            self.assertFalse(list(path.rglob('*.png')))

    def test_locked_excel_keeps_recoverable_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=DailyProductionLog(tmp,interval=100)
            with patch('production_log.export_day',side_effect=PermissionError('Excel open')):
                log.append(report(),datetime(2026,10,29,tzinfo=VIETNAM))
                with self.assertRaises(RuntimeError):log.close()
            recovered=DailyProductionLog(tmp,interval=100);recovered.close()
            self.assertTrue((Path(tmp)/'OCR_DATE_2026-10-29.xlsx').is_file())

    def test_production_worker_never_writes_images_or_debug_reports(self):
        from gui import CameraWorker
        from hardware.frame_source import FramePacket
        from ocr.validator import Expected
        from datetime import date
        class Runtime:
            def prepare(self,*args):pass
            def inspect(self,image,*args,**kwargs):return report(),{'original':image}
        class Source:
            delivered=0
            def open(self):pass
            def start(self):pass
            def stop(self):pass
            def close(self):pass
            def get_frame(self,timeout):
                if self.delivered>=2:
                    worker.requestInterruption();return None
                self.delivered+=1
                return FramePacket(6+self.delivered,np.zeros((10,10,3),dtype=np.uint8),metadata={'trigger_mode':'hardware' if self.delivered==1 else 'software_test'})
        with tempfile.TemporaryDirectory() as tmp,patch('gui.ROOT',Path(tmp)),patch('gui.save_image') as images,patch('gui.save_report') as debug:
            worker=CameraWorker(Runtime());worker.production=True;worker.source=Source()
            worker.expected=Expected('05',1,expected_date=date(2026,8,22),check_nsx_date=True)
            worker.start();self.assertTrue(worker.wait(10000))
            images.assert_not_called();debug.assert_not_called()
            self.assertEqual(len(list(Path(tmp).rglob('*.xlsx'))),1)
            self.assertFalse(list(Path(tmp).rglob('*.png')))
            with sqlite3.connect(Path(tmp)/'data/production/.internal/results.sqlite3') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)
            db.close()

if __name__=='__main__':unittest.main()
