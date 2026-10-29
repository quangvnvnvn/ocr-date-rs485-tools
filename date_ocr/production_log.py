"""Background daily XLSX export with a durable local recovery journal.

Only result values enter the queue, never images. SQLite and OOXML use the
standard library so the production app needs no Office or spreadsheet runtime.
"""
from datetime import datetime, date, timedelta, timezone
import json
import math
from pathlib import Path
import queue
import sqlite3
import threading
import time
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

VIETNAM = timezone(timedelta(hours=7))
HEADERS = ['Thời gian kiểm tra','Frame','Ngày NSX đọc được','DM','AB','Giờ in',
           'Độ tin cậy','Lệch góc (°)','Xử lý (ms)','Kết quả','Mã kết quả','Lý do',
           'NSX yêu cầu','AB yêu cầu','DM yêu cầu','Chuỗi OCR']

def now_local():
    return datetime.now(VIETNAM)

def result_row(report, when):
    v=report['validation'];p=v.get('parsed') or {};expected=report.get('expected') or {}
    actual=date(p['year'],p['month'],p['day']) if all(k in p for k in ('year','month','day')) else None
    return [when.replace(tzinfo=None).isoformat(sep=' ',timespec='milliseconds'),
            (report.get('hardware') or {}).get('frame_id'),actual.isoformat() if actual else None,
            p.get('dm'),p.get('ab'),p.get('time'),v.get('overall_confidence'),
            (report.get('alignment') or {}).get('deviation'),report.get('processing_ms'),
            'OK' if v['result']=='PASS' else 'NG',v['result'],v.get('error_reason',''),
            expected.get('expected_date'),expected.get('ab'),expected.get('dm'),v.get('raw','')]

def column(number):
    text=''
    while number:
        number,remainder=divmod(number-1,26);text=chr(65+remainder)+text
    return text

def cell(value, col, row, header=False, result=None):
    style=1 if header else (2 if row%2==0 else 0)
    if col==10 and not header:style=3 if result=='OK' else 4
    kind='inlineStr';content=''
    if value is not None:
        if not header and col in (1,3,13):
            value=(datetime.fromisoformat(str(value))-datetime(1899,12,30)).total_seconds()/86400
            style=5 if col==1 else 6
        if isinstance(value,(int,float)) and math.isfinite(value):
            kind='n';content=f'<v>{value}</v>'
            if not header and col==7:style=7
        else:
            text=''.join(c for c in str(value) if c in '\t\n\r' or ord(c)>=32)[:32767]
            content='<is><t xml:space="preserve">'+escape(text)+'</t></is>'
    return f'<c r="{column(col)}{row}" s="{style}" t="{kind}">{content}</c>'

STYLES='''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="3"><numFmt numFmtId="164" formatCode="dd/mm/yyyy hh:mm:ss.000"/><numFmt numFmtId="165" formatCode="dd/mm/yyyy"/><numFmt numFmtId="166" formatCode="0.0%"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="6"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF174E78"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF0F6FB"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD9F2E5"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFE2E2"/></patternFill></fill></fills>
<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="8">
<xf fontId="0" fillId="0" borderId="0"/><xf fontId="1" fillId="2" borderId="0" applyFill="1" applyFont="1"><alignment wrapText="1"/></xf>
<xf fontId="0" fillId="3" borderId="0" applyFill="1"/><xf fontId="0" fillId="4" borderId="0" applyFill="1"/><xf fontId="0" fillId="5" borderId="0" applyFill="1"/>
<xf numFmtId="164" applyNumberFormat="1"/><xf numFmtId="165" applyNumberFormat="1"/><xf numFmtId="166" applyNumberFormat="1"/>
</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''

def export_day(connection, output, day):
    target=output/f'OCR_DATE_{day}.xlsx';temporary=target.with_suffix('.tmp')
    count=connection.execute('SELECT count(*) FROM events WHERE day=?',(day,)).fetchone()[0]
    if count>1048575:raise RuntimeError('Vượt giới hạn số dòng Excel trong ngày; dữ liệu còn trong nhật ký phục hồi')
    try:
        with ZipFile(temporary,'w',ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
            z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Kết quả" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
            z.writestr('xl/styles.xml',STYLES)
            with z.open('xl/worksheets/sheet1.xml','w') as sheet:
                def write(s):sheet.write(s.encode('utf-8'))
                write('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>')
                widths=[25,12,22,8,9,12,16,16,16,12,30,50,20,14,14,45]
                for i,width in enumerate(widths,1):write(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>')
                write('</cols><sheetData><row r="1" ht="32" customHeight="1">'+''.join(cell(v,i,1,True) for i,v in enumerate(HEADERS,1))+'</row>')
                for index,(payload,) in enumerate(connection.execute('SELECT payload FROM events WHERE day=? ORDER BY id',(day,)),2):
                    values=json.loads(payload)
                    write(f'<row r="{index}">'+''.join(cell(v,i,index,result=values[9]) for i,v in enumerate(values,1))+'</row>')
                write(f'</sheetData><autoFilter ref="A1:P{count+1}"/></worksheet>')
        temporary.replace(target)
    finally:
        if temporary.exists():temporary.unlink()
    return target

class DailyProductionLog:
    def __init__(self,output,on_status=lambda text:None,interval=5):
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True)
        self.status=on_status;self.interval=interval;self.items=queue.Queue(10000)
        self.error=None;self.ready=threading.Event();self.stopping=threading.Event()
        self.thread=threading.Thread(target=self._run,name='daily-excel',daemon=True)
        self.thread.start();self.ready.wait(10)
        self.check()
        if not self.ready.is_set():raise RuntimeError('Không khởi tạo được nhật ký Excel')
    def check(self):
        if self.error:raise RuntimeError('Lỗi lưu kết quả sản xuất: '+str(self.error))
    def append(self,report,when=None):
        self.check();when=when or now_local()
        try:self.items.put_nowait((when.date().isoformat(),json.dumps(result_row(report,when),ensure_ascii=False)))
        except queue.Full:raise RuntimeError('Hàng đợi lưu Excel đầy; dừng để tránh mất kết quả')
    def close(self):
        self.stopping.set();self.thread.join(60)
        if self.thread.is_alive():raise RuntimeError('Chưa hoàn tất lưu Excel')
        self.check()
    def _run(self):
        connection=None;lock=None;locked=False
        try:
            internal=self.output/'.internal';internal.mkdir(exist_ok=True)
            import msvcrt
            lock=(internal/'production.lock').open('a+b')
            if lock.tell()==0:lock.write(b'0');lock.flush()
            lock.seek(0)
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1);locked=True
            except OSError:raise RuntimeError('Một cửa sổ khác đang ghi sổ sản xuất. Hãy ngắt camera ở cửa sổ đó trước.')
            connection=sqlite3.connect(internal/'results.sqlite3')
            connection.execute('PRAGMA journal_mode=WAL');connection.execute('PRAGMA synchronous=FULL')
            connection.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,day TEXT,payload TEXT)')
            connection.execute('CREATE INDEX IF NOT EXISTS events_day ON events(day,id)')
            connection.execute('CREATE TABLE IF NOT EXISTS pending(day TEXT PRIMARY KEY)');connection.commit()
            self.ready.set();last=0
            while not self.stopping.is_set() or not self.items.empty():
                try:
                    day,payload=self.items.get(timeout=.1)
                    with connection:
                        connection.execute('INSERT INTO events(day,payload) VALUES(?,?)',(day,payload))
                        connection.execute('INSERT OR IGNORE INTO pending(day) VALUES(?)',(day,))
                except queue.Empty:pass
                if time.monotonic()-last>=self.interval:
                    self._export(connection);last=time.monotonic()
            self._export(connection,final=True)
        except Exception as exc:self.error=exc;self.status('LỖI LƯU EXCEL: '+str(exc))
        finally:
            self.ready.set()
            if connection:connection.close()
            if lock:
                if locked:
                    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
                lock.close()
    def _export(self,connection,final=False):
        for (day,) in connection.execute('SELECT day FROM pending').fetchall():
            try:
                path=export_day(connection,self.output,day)
            except PermissionError:
                self.status('Đóng file Excel đang mở để cập nhật; kết quả vẫn đang ghi vào nhật ký phục hồi.')
                if final:raise RuntimeError('File Excel đang mở. Đóng Excel rồi kết nối lại để xuất các kết quả còn chờ.')
                continue
            with connection:connection.execute('DELETE FROM pending WHERE day=?',(day,))
            self.status('Đã cập nhật Excel: '+path.name)
