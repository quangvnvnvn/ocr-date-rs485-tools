"""Vietnamese GUI for still images and hardware-triggered HIKROBOT frames."""
import sys, json, traceback, html, time, gc, os, threading
from pathlib import Path
from datetime import datetime
import uuid
import cv2
from PySide6.QtCore import Qt, QRectF, QPointF, QThread, Signal, QDate, QTimer
from PySide6.QtGui import QPainter, QImage, QColor, QPen, QRegularExpressionValidator, QFont, QFontDatabase
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QPushButton,QLabel,QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox,QCheckBox,QDateEdit,
    QFileDialog,QMessageBox,QSplitter,QTabWidget,QTextEdit,QTableWidget,QTableWidgetItem,
    QHeaderView,QGroupBox,QSizePolicy)
from main import load_image, save_image, write_json, save_report, ROOT, EXTENSIONS
from roi.fixed_roi import FixedROI
from roi.auto_roi import AutoROIDetector
from ocr.pipeline import DEFAULT_DEADLINE_MS
from ocr.validator import Expected
from inspection_runtime import InspectionRuntime
from hardware.frame_source import FrameSourceError,missing_frame_count
from hardware.simulated_source import TriggeredImageSource
from hardware.hikrobot_mvs import HikrobotMvsSource
from hardware.rs485_ejector import EjectorSettings, ModbusOutput, TimedEjector, EjectorError
from gui_theme import apply_theme
from production_log import DailyProductionLog, now_local


RESULT_TEXT = {
    'PASS': ('Đạt', 'Khớp các điều kiện kiểm tra.'),
    'FAIL_DM': ('Sai mã DM', 'Mã DM đọc được khác mã đã cài.'),
    'FAIL_AB': ('Sai mã AB', 'Mã AB đọc được khác mã đã cài.'),
    'FAIL_DATE': ('Ngày in không hợp lệ', 'Ngày sản xuất đọc được không hợp lệ.'),
    'FAIL_TIME': ('Giờ in không hợp lệ', 'Giờ in đọc được không hợp lệ.'),
    'FAIL_DATE_MISMATCH': ('Sai ngày sản xuất', 'Ngày sản xuất đọc được khác ngày đã chọn.'),
    'FAIL_FRAME_GAP': ('Thiếu ảnh từ camera', 'Số thứ tự ảnh bị gián đoạn. Kiểm tra tín hiệu chụp và kết nối camera.'),
    'FAIL_BUSY': ('Đang xử lý ảnh trước', 'Tín hiệu chụp đến khi ảnh trước còn chờ xử lý.'),
    'FAIL_OCR_LOW_CONFIDENCE': ('Độ tin cậy thấp', 'Kết quả đọc mã chưa đạt ngưỡng tin cậy đã cài.'),
    'FAIL_DEADLINE': ('Quá thời gian', 'Thời gian xử lý vượt giới hạn {deadline} ms.'),
    'FAIL_ROI_NOT_FOUND': ('Chưa tìm thấy vùng mã', 'Không tách được đủ hai dòng mã trong vùng kiểm tra.'),
    'FAIL_FORMAT': ('Sai định dạng mã', 'Chuỗi đọc được không đúng cấu trúc ngày sản xuất, DM, AB và giờ in.'),
    'FAIL_OCR': ('Không đọc được mã', 'Xem ảnh và chi tiết OCR để kiểm tra.'),
}


def result_text(report):
    code=report['validation']['result']
    title,reason=RESULT_TEXT.get(code,('Lỗi kiểm tra','Xem chi tiết OCR để kiểm tra lỗi.'))
    if code=='FAIL_ROI_NOT_FOUND':
        if report.get('search_bbox') and not report.get('bbox'):
            return title,'Không tìm thấy cặp chữ NSX và DM trong vùng đã khoanh.'
        if (report.get('auto_roi') or {}).get('found') is False:
            return title,'Không bám được mẫu bao bì để xác định vị trí hai dòng mã.'
    return title,reason.format(deadline=report.get('deadline_ms',DEFAULT_DEADLINE_MS))


def optimize_process_priority():
    if sys.platform=='win32':
        try:
            import ctypes
            kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel32.GetCurrentProcess.restype=ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes=(ctypes.c_void_p,ctypes.c_uint32)
            kernel32.SetPriorityClass.restype=ctypes.c_int
            handle=kernel32.GetCurrentProcess()
            if not kernel32.SetPriorityClass(handle,0x00000080):  # HIGH_PRIORITY_CLASS
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            pass


class ImageView(QWidget):
    selected=Signal(object)
    def __init__(self):
        super().__init__(); self.image=None; self.roi=None; self.search_roi=None; self.selecting=False; self.start=None; self.end=None
        self.setMinimumSize(250,90)
    def set_image(self,image,roi=None,*,search_roi=None):
        self.image=image; self.roi=roi; self.search_roi=search_roi; self.update()
    def rect_image(self):
        if self.image is None:return QRectF()
        h,w=self.image.shape[:2]; scale=min(self.width()/w,self.height()/h)
        return QRectF((self.width()-w*scale)/2,(self.height()-h*scale)/2,w*scale,h*scale)
    def paintEvent(self,event):
        p=QPainter(self); p.fillRect(self.rect(),QColor('#e5ebf0'))
        if self.image is None:
            p.setPen(QColor('#596b7a')); p.drawText(self.rect(),Qt.AlignCenter,'Chưa có ảnh');return
        a=self.image
        if a.ndim==2:q=QImage(a.data,a.shape[1],a.shape[0],a.strides[0],QImage.Format_Grayscale8)
        else:q=QImage(a.data,a.shape[1],a.shape[0],a.strides[0],QImage.Format_BGR888)
        area=self.rect_image(); p.drawImage(area,q)
        sx=area.width()/a.shape[1]; sy=area.height()/a.shape[0]
        for roi,color,style in ((self.search_roi,'#1672c5',Qt.DashLine),(self.roi,'#08a65c',Qt.SolidLine)):
            if roi:
                p.setPen(QPen(QColor(color),2,style))
                x,y,w,h=roi.bounds(a.shape)
                p.drawRect(QRectF(area.x()+x*sx,area.y()+y*sy,w*sx,h*sy))
        p.setPen(QPen(QColor('#08a65c'),2))
        if self.start is not None and self.end is not None:p.drawRect(QRectF(self.start,self.end).normalized())
    def mousePressEvent(self,event):
        if self.selecting and self.image is not None and self.rect_image().contains(event.position()):
            self.start=event.position();self.end=self.start
    def mouseMoveEvent(self,event):
        if self.start is not None:
            self.end=event.position();self.update()
    def mouseReleaseEvent(self,event):
        if self.start is None:return
        area=self.rect_image(); box=QRectF(self.start,event.position()).normalized().intersected(area)
        self.start=None;self.end=None
        if box.width()>4 and box.height()>4:
            h,w=self.image.shape[:2]
            x=round((box.left()-area.left())*w/area.width()); y=round((box.top()-area.top())*h/area.height())
            right=min(w,round((box.right()-area.left())*w/area.width())); bottom=min(h,round((box.bottom()-area.top())*h/area.height()))
            self.roi=FixedROI(x,y,right-x,bottom-y,w,h);self.selected.emit(self.roi)
            self.selecting=False;self.setCursor(Qt.ArrowCursor)
        self.update()


class OCRWorker(QThread):
    result=Signal(object,object)
    status=Signal(str)
    error=Signal(str)
    def __init__(self,runtime=None):
        super().__init__();self.runtime=runtime or InspectionRuntime();self.jobs=[];self.expected=None;self.output=None
        self.deadline_ms=DEFAULT_DEADLINE_MS
    def run(self):
        records=[]
        try:
            self.runtime.prepare(any(mode=='auto' for _,_,mode in self.jobs),self.status.emit,
                                 require_guided_roi=any(mode=='guided' for _,_,mode in self.jobs))
            self.output=ROOT/'data/debug'/('gui_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
            self.output.mkdir(parents=True)
            for i,(path,roi,roi_mode) in enumerate(self.jobs):
                if self.isInterruptionRequested():break
                self.status.emit(f'Đang kiểm tra {i+1}/{len(self.jobs)}: {path.name}')
                folder=self.output/f'{i+1:03d}_{path.stem}';folder.mkdir()
                try:
                    image=load_image(path)
                    report,artifacts=self.runtime.inspect(image,roi,roi_mode,self.expected,
                        image_name=path.name,source=str(path),deadline_ms=self.deadline_ms,
                        check_alignment=False)
                    for name,array in artifacts.items():save_image(folder/f'{name}.png',array)
                except Exception as exc:
                    report=dict(image=path.name,source=str(path),artifacts=[],processing_ms=0,
                        deadline_ms=self.deadline_ms,deadline_met=False,
                        validation=dict(result='FAIL_OCR',raw='',parsed=None,confidence={},overall_confidence=None,error_reason=str(exc)))
                write_json(folder/'result.json',report);records.append(report)
                self.result.emit(report,folder)
            save_report(self.output,records,include_alignment=False)
            self.status.emit(f'Đã kiểm tra {len(records)} ảnh. Kết quả đã lưu.')
        except Exception:
            self.error.emit(traceback.format_exc())


class CameraWorker(QThread):
    """Arm one frame source and inspect every externally-triggered frame."""
    result=Signal(object,object,object)
    status=Signal(str)
    error=Signal(str)
    armed=Signal(object)
    eject_event=Signal(object)
    def __init__(self,runtime=None):
        super().__init__();self.runtime=runtime or InspectionRuntime();self.source=None
        self.expected=None;self.roi=None;self.roi_mode='auto';self.timeout_ms=100;self.output=None
        self.deadline_ms=DEFAULT_DEADLINE_MS
        self.eject_settings=None;self.ejector=None
        self.production=False;self.manual_trigger=False;self.sensor_test=False
    def run(self):
        records=[];daily_log=None
        try:
            capture_once=self.manual_trigger or self.sensor_test
            if self.roi_mode=='guided':
                self.runtime.prepare(False,self.status.emit,require_guided_roi=True)
            else:
                self.runtime.prepare(self.roi_mode=='auto' or capture_once,self.status.emit)
            if self.eject_settings is not None and self.eject_settings.enabled:
                if not isinstance(self.source,HikrobotMvsSource):
                    raise EjectorError('Chỉ cho phép đẩy thật với camera Hikrobot trigger ngoài')
                self.ejector=TimedEjector(ModbusOutput(self.eject_settings),
                    lambda phase,frame_id,detail,when:self.eject_event.emit(dict(
                        phase=phase,frame_id=frame_id,detail=detail,monotonic=when)))
                self.ejector.start()
            self.status.emit('Đang mở camera để chụp thử…' if capture_once
                             else 'Đang mở camera và bật trigger cứng…')
            self.source.open();self.source.start()
            self.output=(ROOT/'data/production' if self.production else ROOT/'data/debug'/('camera_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]))
            self.output.mkdir(parents=True,exist_ok=True)
            if self.production:daily_log=DailyProductionLog(self.output,self.status.emit)
            if self.manual_trigger:
                self.source.software_trigger_once()
                trigger_deadline=time.monotonic()+2
                self.status.emit('Đã gửi trigger tay • Đang chờ ảnh từ camera…')
            else:
                if self.sensor_test:
                    trigger_deadline=time.monotonic()+15
                self.armed.emit({'ready':True,'source':type(self.source).__name__})
                self.status.emit('Đang chờ một xung cảm biến • Tối đa 15 giây' if self.sensor_test
                                 else 'CAMERA ĐÃ ARM • Chờ tín hiệu trigger ngoài')
            sequence=0;last_frame_id=None
            while not self.isInterruptionRequested():
                if daily_log:daily_log.check()
                packet=self.source.get_frame(self.timeout_ms)
                if packet is None:
                    if capture_once and time.monotonic()>=trigger_deadline:
                        if self.sensor_test:
                            raise FrameSourceError('Không nhận được ảnh từ cảm biến trong 15 giây.')
                        raise FrameSourceError('Không nhận được ảnh trong 2 giây sau khi gửi trigger tay.')
                    continue
                captured_at=now_local()
                sequence+=1
                name=f'frame_{packet.frame_id or sequence:08d}_{datetime.now().strftime("%H%M%S_%f")[:-3]}.png'
                folder=self.output if self.production else self.output/f'{sequence:06d}_{Path(name).stem}'
                if not self.production:folder.mkdir()
                missing=missing_frame_count(last_frame_id,packet.frame_id)
                if packet.frame_id>0:last_frame_id=packet.frame_id
                metadata={**packet.metadata,'frame_id':packet.frame_id,
                    'device_timestamp':packet.device_timestamp,
                    'missing_frames_before':missing,
                    'received_monotonic':packet.received_monotonic,
                    'external_trigger_only':packet.metadata.get('trigger_mode')!='software_test'}
                try:
                    report,artifacts=self.runtime.inspect(packet.image,self.roi,self.roi_mode,
                        self.expected,image_name=name,source=packet.source,
                        received_monotonic=packet.received_monotonic,metadata=metadata,
                        deadline_ms=self.deadline_ms,check_alignment=False,
                        reacquire_camera_roi=capture_once)
                except Exception as exc:
                    report=dict(image=name,source=packet.source,artifacts=['original'],processing_ms=0,
                        deadline_ms=self.deadline_ms,deadline_met=False,hardware=metadata,
                        validation=dict(result='FAIL_OCR',raw='',parsed=None,confidence={},
                            overall_confidence=None,error_reason=str(exc)))
                    artifacts={'original':packet.image}
                if missing:
                    previous=report['validation'].get('error_reason','')
                    report['validation'].update(result='FAIL_FRAME_GAP',
                        error_reason=(f'Mất {missing} frame trigger trước frame #{packet.frame_id}. '
                                      f'{previous}').strip())
                decision_monotonic=time.perf_counter()
                schedule_error=None
                # Diagnostic software triggers never actuate the pneumatic valve.
                if self.ejector is not None and metadata.get('trigger_mode')=='hardware' and report['validation']['result']!='PASS':
                    try:
                        due=self.ejector.schedule(packet.frame_id,decision_monotonic,
                                                  report['validation']['result'])
                        report['ejector']=dict(status='SCHEDULED',frame_id=packet.frame_id,
                            decision_monotonic=decision_monotonic,due_monotonic=due,
                            decision_delay_ms=self.eject_settings.decision_delay_ms)
                    except Exception as exc:
                        schedule_error=exc
                        report['validation']['error_reason']+='; Lỗi lập lịch xi lanh: '+str(exc)
                report['production']=self.production
                if daily_log and metadata.get('trigger_mode')=='hardware':
                    report.setdefault('expected',dict(expected_date=self.expected.expected_date.isoformat() if self.expected.expected_date else None,ab=self.expected.ab,dm=self.expected.dm))
                    daily_log.append(report,captured_at)
                elif not self.production:records.append(report)
                # The operator sees the decision before diagnostic files are written.
                self.result.emit(report,folder,artifacts)
                if schedule_error:raise EjectorError(str(schedule_error))
                if not self.production:
                    for artifact_name,array in artifacts.items():save_image(folder/f'{artifact_name}.png',array)
                    write_json(folder/'result.json',report)
                if capture_once:break
            if not self.production:save_report(self.output,records,include_alignment=False)
        except Exception:
            self.error.emit(traceback.format_exc())
        finally:
            if self.ejector is not None:
                try:self.ejector.stop()
                except Exception as exc:self.error.emit(f'Không tắt được DO RS485: {exc}')
                self.ejector=None
            if daily_log:
                try:daily_log.close()
                except Exception as exc:self.error.emit(str(exc))
            if self.source is not None:
                try:self.source.stop()
                except Exception:pass
                try:self.source.close()
                except Exception:pass
            self.armed.emit({'ready':False})


class EjectTestWorker(QThread):
    """One explicit operator test pulse, using the same Modbus scheduler as production."""
    event=Signal(object)
    error=Signal(str)
    def __init__(self):
        super().__init__();self.settings=None
    def run(self):
        completed=threading.Event()
        def callback(phase,frame_id,detail,when):
            self.event.emit(dict(phase=phase,frame_id=frame_id,detail=detail,
                                 monotonic=when,test=True))
            if phase in ('OFF','ERROR'):completed.set()
        ejector=None
        try:
            ejector=TimedEjector(ModbusOutput(self.settings),callback)
            ejector.start()
            decision=time.perf_counter()
            due=ejector.schedule(-1,decision,'FAIL_TEST')
            self.event.emit(dict(phase='SCHEDULED',frame_id=-1,detail='NG giả lập',
                                 monotonic=decision,due_monotonic=due,test=True))
            while not completed.wait(.05):
                if self.isInterruptionRequested():break
        except Exception as exc:self.error.emit(str(exc))
        finally:
            if ejector is not None:
                try:ejector.stop()
                except Exception as exc:self.error.emit('Không tắt được DO sau test: '+str(exc))


class LiveViewWorker(QThread):
    """Free-run the camera for lens setup without invoking OCR."""
    frame=Signal(object,object)
    status=Signal(str)
    error=Signal(str)
    armed=Signal(object)
    def __init__(self):
        super().__init__();self.source=None;self.timeout_ms=200;self.maximum_fps=12.0
    def run(self):
        cleanup_errors=[]
        try:
            self.status.emit('Đang mở CAMERA LIVE để chỉnh lens…')
            self.source.open();self.source.enable_live_view();self.source.start()
            self.armed.emit({'ready':True,'source':type(self.source).__name__})
            self.status.emit('CAMERA LIVE • Xoay lens đến khi hình rõ nhất')
            last_emit=0.0
            while not self.isInterruptionRequested():
                packet=self.source.get_frame(self.timeout_ms)
                if packet is None:continue
                now=time.perf_counter()
                if now-last_emit < 1.0/self.maximum_fps:continue
                last_emit=now
                self.frame.emit(packet.image,{**packet.metadata,'frame_id':packet.frame_id})
        except Exception:
            self.error.emit(traceback.format_exc())
        finally:
            if self.source is not None:
                try:self.source.stop()
                except Exception as exc:cleanup_errors.append(str(exc))
                try:self.source.disable_live_view()
                except Exception as exc:cleanup_errors.append(str(exc))
                try:self.source.close()
                except Exception as exc:cleanup_errors.append(str(exc))
            if cleanup_errors:self.error.emit('Lỗi khi khôi phục trigger cứng: '+'; '.join(cleanup_errors))
            self.armed.emit({'ready':False})


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        optimize_process_priority()
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        QApplication.instance().setFont(QFont('Segoe UI',10))
        apply_theme(QApplication.instance())
        self.setWindowTitle('OCR DATE — Kiểm tra ngày sản xuất');self.resize(1250,860)
        self.path=None;self.image=None;self.roi=None;self.rows=[];self.closing=False;self.latest_report=None
        self.camera_source=None;self.camera_frames=0;self.live_source=None;self.live_frames=0
        self.config_path=Path(os.environ.get('OCR_DATE_CONFIG',
                                             str(ROOT/'config/config.json')))
        try:self.config=json.loads(self.config_path.read_text(encoding='utf-8'))
        except Exception:self.config={'roi_mode':'fixed','fixed_roi':None}
        self.manifest=json.loads((ROOT/'config/sample_rois.json').read_text(encoding='utf-8'))
        self.runtime=InspectionRuntime()
        self.worker=OCRWorker(self.runtime);self.worker.result.connect(self.got_result);self.worker.status.connect(self.show_status)
        self.worker.error.connect(self.failed);self.worker.finished.connect(self.finished)
        self.camera_worker=CameraWorker(self.runtime)
        self.camera_worker.result.connect(self.got_camera_result)
        self.camera_worker.status.connect(self.show_status)
        self.camera_worker.error.connect(self.camera_failed)
        self.camera_worker.armed.connect(self.camera_armed)
        self.camera_worker.eject_event.connect(self.on_eject_event)
        self.eject_test_worker=EjectTestWorker()
        self.eject_test_worker.event.connect(self.on_eject_event)
        self.eject_test_worker.error.connect(self.eject_test_failed)
        self.eject_test_worker.finished.connect(self.eject_test_finished)
        self.camera_worker.finished.connect(self.camera_finished)
        self.live_worker=LiveViewWorker()
        self.live_worker.frame.connect(self.got_live_frame)
        self.live_worker.status.connect(self.show_status)
        self.live_worker.error.connect(self.live_failed)
        self.live_worker.armed.connect(self.live_armed)
        self.live_worker.finished.connect(self.live_finished)
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central)
        layout.setContentsMargins(18,12,18,12);layout.setSpacing(8)
        self.controls=QWidget();self.controls.setObjectName('controls');top=QVBoxLayout(self.controls)
        top.setContentsMargins(12,10,12,10);top.setSpacing(8);layout.addWidget(self.controls)
        self.mode_tabs=QTabWidget();self.mode_tabs.setObjectName('operatorModes');self.mode_tabs.tabBar().setObjectName('modeBar');top.addWidget(self.mode_tabs)
        pages=[]
        for name,identity in zip(('Chạy thật','Chạy thử','Test chức năng','Cài đặt','Camera Live'),('productionPage','trialPage','diagnosticsPage','settingsPage','livePage')):
            page=QWidget();page.setObjectName(identity);page_layout=QVBoxLayout(page);page_layout.setSpacing(8)
            self.mode_tabs.addTab(page,name);pages.append(page_layout)
        def function_block(parent,identity):
            block=QWidget();block.setObjectName(identity)
            content=QVBoxLayout(block);content.setContentsMargins(8,6,8,6);content.setSpacing(6)
            parent.addWidget(block);return content
        production,trial,diagnostics,configuration,live=pages
        self.live_page=self.mode_tabs.widget(self.mode_tabs.count()-1)
        production.addWidget(QLabel('Sản xuất · Camera Hikrobot nhận trigger ngoài, xử lý và đẩy NG theo cấu hình.'))
        trial.addWidget(QLabel('Chụp thử từ camera hoặc kiểm tra ảnh có sẵn · Không kích xi lanh.'))
        diagnostics.addWidget(QLabel('Kiểm tra camera, RS485 và xi lanh · TEST NG phát một xung DO thật khi bấm.'))
        camera_bar=QHBoxLayout();function_block(configuration,'cameraBlock').addLayout(camera_bar)
        camera_bar.addWidget(QLabel('Nguồn ảnh:'))
        self.source_mode=QComboBox();self.source_mode.addItem('Ảnh tĩnh / thư mục','static')
        self.source_mode.addItem('Camera Hikrobot · trigger ngoài','hikrobot')
        self.source_mode.addItem('Mô phỏng trigger','simulation');camera_bar.addWidget(self.source_mode)
        camera_bar.addWidget(QLabel('Serial:'))
        self.camera_serial=QLineEdit();self.camera_serial.setPlaceholderText('Để trống = camera đầu tiên')
        self.camera_serial.setMaximumWidth(190);camera_bar.addWidget(self.camera_serial)
        camera_bar.addWidget(QLabel('Ngõ vào:'))
        self.trigger_line=QComboBox();self.trigger_line.addItems(['Line0','Line1']);camera_bar.addWidget(self.trigger_line)
        camera_bar.addWidget(QLabel('Phơi sáng:'))
        self.exposure_us=QSpinBox();self.exposure_us.setRange(0,1000000);self.exposure_us.setSingleStep(100)
        self.exposure_us.setSuffix(' µs');self.exposure_us.setSpecialValueText('Theo camera')
        self.exposure_us.setToolTip('Thời gian phơi sáng tính bằng micro giây. Giá trị khác 0 dùng phơi sáng tay khi mở camera.')
        camera_bar.addWidget(self.exposure_us)
        self.connect_camera_button=QPushButton('KẾT NỐI + ARM');self.connect_camera_button.clicked.connect(lambda: self.connect_camera())
        self.connect_camera_button.setObjectName('primary');camera_bar.addWidget(self.connect_camera_button)
        self.disconnect_camera_button=QPushButton('NGẮT CAMERA');self.disconnect_camera_button.clicked.connect(self.disconnect_camera)
        camera_bar.addWidget(self.disconnect_camera_button)
        self.camera_state=QLabel('ẢNH TĨNH');self.camera_state.setObjectName('cameraState');camera_bar.addWidget(self.camera_state)
        camera_bar.addStretch()
        camera_test_bar=QHBoxLayout();function_block(diagnostics,'cameraBlock').addLayout(camera_test_bar)
        self.camera_test_label=QLabel('Chỉnh / kiểm tra camera:');camera_test_bar.addWidget(self.camera_test_label)
        live_bar=QHBoxLayout();function_block(live,'cameraBlock').addLayout(live_bar)
        self.live_button=QPushButton('BẬT LIVE');self.live_button.clicked.connect(self.toggle_live_view)
        self.live_button.setObjectName('primary')
        self.live_button.setToolTip('Xem hình liên tục để chỉnh lens. Dừng LIVE sẽ tự khôi phục trigger ngoài.')
        live_bar.addWidget(self.live_button)
        self.live_info=QLabel('Bấm BẬT LIVE để xem hình và chỉnh camera.');self.live_info.setWordWrap(True)
        live_bar.addWidget(self.live_info,1)
        self.test_trigger_button=QPushButton('CHỤP THỬ 1 ẢNH (TRIGGER TAY)');self.test_trigger_button.clicked.connect(self.test_camera_trigger)
        self.test_trigger_button.setObjectName('primary')
        self.test_trigger_button.setToolTip('Chụp một ảnh, đối chiếu NSX, AB và DM trong tab Chạy thử rồi ngắt camera. Không kích xi lanh.')
        camera_test_bar.addWidget(self.test_trigger_button)
        self.camera_test_hint=QLabel('Chụp thử không cần tín hiệu ngoài · Không kích xi lanh')
        self.camera_test_hint.setObjectName('subtitle');camera_test_bar.addWidget(self.camera_test_hint);camera_test_bar.addStretch()
        self.source_mode.currentIndexChanged.connect(self.source_changed)
        eject_bar=QHBoxLayout();function_block(configuration,'rs485Block').addLayout(eject_bar)
        self.eject_enabled=QCheckBox('Bật đẩy sản phẩm NG qua RS485');eject_bar.addWidget(self.eject_enabled)
        eject_bar.addWidget(QLabel('COM:'));self.eject_port=QComboBox();self.eject_port.setEditable(True)
        self.eject_port.setMinimumWidth(90);eject_bar.addWidget(self.eject_port)
        self.refresh_ports=QPushButton('Quét COM');self.refresh_ports.clicked.connect(self.refresh_eject_ports);eject_bar.addWidget(self.refresh_ports)
        eject_bar.addWidget(QLabel('Slave:'));self.eject_slave=QComboBox();self.eject_slave.setEditable(True);self.eject_slave.setCurrentText('1');self.eject_slave.setMaximumWidth(55);eject_bar.addWidget(self.eject_slave)
        eject_bar.addWidget(QLabel('DO:'));self.eject_channel=QComboBox();self.eject_channel.addItems([str(n) for n in range(1,9)]);eject_bar.addWidget(self.eject_channel)
        eject_bar.addWidget(QLabel('Delay sau NG:'));self.eject_delay=QDoubleSpinBox();self.eject_delay.setRange(0,60000);self.eject_delay.setDecimals(0);self.eject_delay.setSuffix(' ms');self.eject_delay.setToolTip('Tính từ lúc có kết quả NG đến lúc gửi lệnh bật DO. Cần đo và hiệu chuẩn trên dây chuyền.');eject_bar.addWidget(self.eject_delay)
        eject_bar.addWidget(QLabel('Xung:'));self.eject_pulse=QDoubleSpinBox();self.eject_pulse.setRange(20,2000);self.eject_pulse.setDecimals(0);self.eject_pulse.setValue(100);self.eject_pulse.setSuffix(' ms');eject_bar.addWidget(self.eject_pulse)
        eject_actions=QHBoxLayout();function_block(diagnostics,'rs485Block').addLayout(eject_actions)
        self.test_eject_link=QPushButton('Kiểm tra kết nối RS485');self.test_eject_link.clicked.connect(self.check_eject_link);eject_actions.addWidget(self.test_eject_link)
        self.test_eject_pulse=QPushButton('TEST NG → XI LANH');self.test_eject_pulse.clicked.connect(self.test_ng_eject)
        self.test_eject_pulse.setObjectName('testHardware')
        self.test_eject_pulse.setToolTip('Bật DO thật một lần sau delay đã cài. Chỉ chạy khi bấm nút này; không cần camera.')
        eject_actions.addWidget(self.test_eject_pulse)
        self.eject_state=QLabel('RS485 TẮT');eject_actions.addWidget(self.eject_state);eject_actions.addStretch()
        bar=QHBoxLayout();function_block(trial,'cameraBlock').addLayout(bar)
        def button(text,fn,parent=bar):
            b=QPushButton(text);b.clicked.connect(fn);parent.addWidget(b);return b
        self.open_button=button('1. Chọn ảnh',self.open_image)
        button('Chọn thư mục',self.open_folder)
        self.sample_button=button('Thử 5 ảnh mẫu',self.samples)
        bar.addStretch()
        self.save_config_button=button('LƯU CẤU HÌNH',self.save_configuration)
        self.save_config_button.setObjectName('saveSettings')
        bar.removeWidget(self.save_config_button);configuration.addWidget(self.save_config_button)
        self.save_config_button.setShortcut('Ctrl+S')
        self.save_config_button.setToolTip('Lưu toàn bộ thiết lập hiện tại để khôi phục khi mở lại (Ctrl+S).')
        self.run_button=button('KIỂM TRA OCR',self.run_image);self.run_button.setObjectName('primary')
        trial_settings=QHBoxLayout();function_block(trial,'inspectionBlock').addLayout(trial_settings)
        trial_settings.addWidget(QLabel('Ngày NSX cần đối chiếu khi chạy thử:'))
        self.trial_date=QDateEdit();self.trial_date.setCalendarPopup(True);self.trial_date.setDisplayFormat('dd/MM/yyyy')
        trial_settings.addWidget(self.trial_date)
        self.trial_ab=QLineEdit('12');self.trial_ab.setMaximumWidth(65);self.trial_ab.setMaxLength(2)
        self.trial_ab.setValidator(QRegularExpressionValidator(QRegularExpression('[0-9]{2}')))
        trial_settings.addWidget(QLabel('Mã AB:'));trial_settings.addWidget(self.trial_ab)
        self.trial_dm=QComboBox();self.trial_dm.addItems(['1','2','3'])
        trial_settings.addWidget(QLabel('DM:'));trial_settings.addWidget(self.trial_dm)
        trial_save=button('Lưu thông số chạy thử',lambda:self.save_configuration(trial=True),trial_settings)
        trial_save.setObjectName('saveSettings');trial_settings.addStretch()
        settings=QHBoxLayout();function_block(configuration,'ocrBlock').addLayout(settings)
        settings.addWidget(QLabel('Mã AB cần kiểm tra:'));self.ab=QLineEdit('12');self.ab.setMaximumWidth(65);self.ab.setMaxLength(2)
        self.ab.setValidator(QRegularExpressionValidator(QRegularExpression('[0-9]{2}')));settings.addWidget(self.ab)
        settings.addWidget(QLabel('DM:'));self.dm=QComboBox();self.dm.addItems(['1','2','3']);settings.addWidget(self.dm)
        settings.addWidget(QLabel('Ngưỡng tin cậy:'));self.threshold=QDoubleSpinBox();self.threshold.setRange(.01,1);self.threshold.setSingleStep(.01);self.threshold.setValue(.8);settings.addWidget(self.threshold)
        self.threshold.setToolTip('Mỗi trường phải đạt ngưỡng này. Không hạ ngưỡng để ép kết quả ĐẠT.')
        settings.addWidget(QLabel('Chế độ ROI:'));self.roi_mode=QComboBox()
        self.roi_mode.addItem('Tự động theo mẫu bao bì','auto')
        self.roi_mode.addItem('Cố định / khoanh tay','fixed')
        self.roi_mode.addItem('Tự tìm trong vùng khoanh','guided')
        settings.addWidget(self.roi_mode)
        self.check_date=QCheckBox();self.check_date.setChecked(True);self.check_date.hide()
        self.nsx_label=QLabel('Ngày NSX cần kiểm tra:');settings.addWidget(self.nsx_label)
        self.date=QDateEdit(QDate.currentDate());self.date.setCalendarPopup(True);self.date.setDisplayFormat('dd/MM/yyyy');settings.addWidget(self.date);settings.addStretch()
        self.date.setEnabled(True);self.date.setToolTip('Chọn ngày NSX cần có trên sản phẩm. Ngày đã chọn sẽ được ghi nhớ.')
        self.deadline_ms=QSpinBox();self.deadline_ms.setRange(1,5000);self.deadline_ms.setSuffix(' ms')
        self.deadline_ms.setToolTip('Tính từ lúc nhận ảnh đến lúc có kết quả. Kết quả đến trễ không được tính ĐẠT.')
        settings.insertWidget(settings.count()-1,QLabel('Giới hạn xử lý:'))
        settings.insertWidget(settings.count()-1,self.deadline_ms)
        production_settings=QHBoxLayout();function_block(production,'inspectionBlock').addLayout(production_settings)
        for i in range(settings.count()-1,-1,-1):
            widget=settings.itemAt(i).widget()
            if widget in (self.ab,self.dm,self.date,self.nsx_label) or (isinstance(widget,QLabel) and widget.text() in ('Mã AB cần kiểm tra:','DM:')):
                settings.removeWidget(widget)
                if isinstance(widget,QLabel):widget.deleteLater()
        for label,widget in (('Ngày NSX đang kiểm tra:',self.date),('Mã AB:',self.ab),('DM:',self.dm)):
            production_settings.addWidget(QLabel(label));production_settings.addWidget(widget)
        production_settings.addStretch()
        production_save=QPushButton('Lưu thông số kiểm tra');production_save.clicked.connect(self.save_configuration);production_settings.addWidget(production_save)
        production_save.setObjectName('saveSettings')
        self.production_state=QLabel('Camera chưa ARM · Kết nối tại tab Cài đặt');self.production_state.setObjectName('productionState');production.addWidget(self.production_state)
        excel_bar=QHBoxLayout();production.addLayout(excel_bar)
        excel_bar.addWidget(QLabel('Tự lưu Excel theo ngày · Không lưu ảnh'));excel_bar.addStretch()
        excel_button=QPushButton('Mở Excel hôm nay');excel_button.clicked.connect(self.open_daily_excel);excel_bar.addWidget(excel_button)
        self.roi_mode.currentIndexChanged.connect(self.roi_mode_changed)
        roi_bar=QHBoxLayout();function_block(configuration,'roiBlock').addLayout(roi_bar)
        self.select_roi_button=button('2. Khoanh vùng tìm mã',self.select_roi,roi_bar)
        self.save_roi_button=button('Lưu vùng',self.save_roi,roi_bar)
        self.reset_roi_button=button('Xóa vùng',self.reset_roi,roi_bar)
        self.coords=QLabel('');roi_bar.addWidget(self.coords);roi_bar.addStretch()
        self.trial_trigger_button=QPushButton('PHÁT TRIGGER THỬ · CHỤP 1 ẢNH')
        self.trial_trigger_button.setObjectName('primary')
        self.trial_trigger_button.setToolTip(self.test_trigger_button.toolTip())
        trigger_bar=QHBoxLayout();trial.addLayout(trigger_bar)
        self.trial_trigger_button.clicked.connect(self.test_camera_trigger);trigger_bar.addWidget(self.trial_trigger_button)
        self.sensor_test_button=QPushButton('CHỜ CẢM BIẾN · CHỤP 1 ẢNH')
        self.sensor_test_button.setToolTip('Chờ tối đa 15 giây ở ngõ Line0/Line1 đã cài. Chụp thử một ảnh và không kích xi lanh.')
        self.sensor_test_button.clicked.connect(lambda:self.connect_camera(sensor_test=True))
        trigger_bar.addWidget(self.sensor_test_button)
        for page_layout in (trial,diagnostics):
            disconnect=QPushButton('Ngắt camera');disconnect.clicked.connect(self.disconnect_camera);page_layout.addWidget(disconnect)
        camera_bar.itemAt(0).widget().hide();self.source_mode.hide()
        self.path_label=QLabel('Chọn ảnh để bắt đầu');self.path_label.setObjectName('fileLabel');layout.addWidget(self.path_label)
        split=QSplitter(Qt.Horizontal);self.image_split=split;layout.addWidget(split,1)
        left=QWidget();left.setObjectName('imagePanel');ll=QVBoxLayout(left)
        image_label=QLabel('ẢNH SẢN PHẨM  ·  Nét đứt: vùng tìm  ·  Nét liền: vùng đọc');image_label.setObjectName('sectionLabel');ll.addWidget(image_label)
        self.original=ImageView();self.original.selected.connect(self.roi_selected);ll.addWidget(self.original,1);split.addWidget(left)
        right=QWidget();right.setObjectName('resultPanel');rl=QVBoxLayout(right)
        self.banner=QLabel('SẴN SÀNG');self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:#dae9f0');rl.addWidget(self.banner)
        self.tabs=QTabWidget();self.views={}
        for key,label in [('roi','ROI DATE'),('final','Ảnh OCR'),('gray','Xám'),('otsu','Otsu'),('clahe','CLAHE'),('adaptive','Adaptive'),('morphology','Morphology')]:
            view=ImageView();self.views[key]=view;self.tabs.addTab(view,label)
        self.debug_text=QTextEdit();self.debug_text.setReadOnly(True);self.tabs.addTab(self.debug_text,'Chi tiết OCR')
        self.debug_toggle=QCheckBox('Hiện ảnh xử lý và chi tiết OCR');self.debug_toggle.toggled.connect(self.toggle_debug);rl.addWidget(self.debug_toggle)
        self.toggle_debug(False)
        rl.addWidget(self.tabs,1)
        self.details=QTextEdit();self.details.setReadOnly(True);self.details.setFixedHeight(145)
        self.details.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);rl.addWidget(self.details)
        split.addWidget(right);split.setSizes([580,620])
        self.live_view=ImageView();layout.addWidget(self.live_view,1)
        self.history=QTableWidget(0,8);self.history.setHorizontalHeaderLabels(['Ảnh','Ngày sản xuất','DM','AB','Giờ in','Độ tin cậy','Xử lý','Kết quả'])
        self.history.setAlternatingRowColors(True);self.history.verticalHeader().hide();self.history.verticalHeader().setDefaultSectionSize(30)
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.history.setFixedHeight(190)
        self.history.setSelectionBehavior(QTableWidget.SelectRows);self.history.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history.cellClicked.connect(self.history_selected);layout.addWidget(self.history)
        footer=QHBoxLayout();layout.addLayout(footer)
        self.status=QLabel('Sẵn sàng kiểm tra');footer.addWidget(self.status,1)
        self.stop=QPushButton('Dừng sau ảnh hiện tại');self.stop.clicked.connect(self.worker.requestInterruption);self.stop.setEnabled(False);footer.addWidget(self.stop)
        out=QPushButton('Mở thư mục kết quả');out.clicked.connect(self.open_output);footer.addWidget(out)
        self.load_operator_settings()
        startup=Path(self.config.get('last_image','')) if self.config.get('last_image') else ROOT/'data/test_images/date1.jpg'
        if not startup.is_absolute():startup=ROOT/startup
        if not startup.is_file() or startup.suffix.lower() not in EXTENSIONS:
            startup=ROOT/'data/test_images/date1.jpg'
        self.load_path(startup)
        self.roi_mode_changed()
        self.source_changed()
        self.mode_tabs.setCurrentIndex(int(self.config.get('operator_tab',0 if self.source_mode.currentData()=='hikrobot' else 1)) % self.mode_tabs.count())
        self.mode_tabs.currentChanged.connect(self.operator_tab_changed)
        self.operator_tab_changed(self.mode_tabs.currentIndex())
    def update_operator_layout(self,index):
        for i in range(self.mode_tabs.count()):
            self.mode_tabs.widget(i).setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred if i==index else QSizePolicy.Ignored)
        self.mode_tabs.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Maximum)
        self.mode_tabs.setFixedHeight(self.mode_tabs.widget(index).layout().sizeHint().height()+self.mode_tabs.tabBar().sizeHint().height()+12)
        live_selected=self.mode_tabs.widget(index)==self.live_page
        self.live_view.setVisible(live_selected)
        self.path_label.setVisible(not live_selected)
        self.image_split.setVisible(not live_selected)
        self.history.setVisible(index!=0 and not live_selected)
        self.image_split.setSizes([850,350] if index==0 else [580,620])
        self.mode_tabs.updateGeometry()
    def lock_inspection_settings(self,locked):
        for widget in (self.date,self.trial_date,self.ab,self.trial_ab,self.dm,self.trial_dm,
                       self.threshold,self.roi_mode,self.deadline_ms):
            widget.setEnabled(not locked)
    def operator_tab_changed(self,index):
        self.update_operator_layout(index)
        if self.live_worker.isRunning() and self.mode_tabs.widget(index)!=self.live_page:
            self.stop_live_view()
        if self.worker.isRunning() or self.camera_worker.isRunning() or self.live_worker.isRunning() or self.eject_test_worker.isRunning():return
        if index in (0,1):
            self.source_mode.setCurrentIndex(self.source_mode.findData('hikrobot' if index==0 else 'static'))
    def load_operator_settings(self):
        settings=self.config.get('inspection',{})
        ab=str(settings.get('expected_ab','12'))
        if len(ab)==2 and ab.isascii() and ab.isdigit():self.ab.setText(ab)
        dm=self.dm.findText(str(settings.get('expected_dm',1)))
        if dm>=0:self.dm.setCurrentIndex(dm)
        threshold=settings.get('minimum_confidence',.80)
        if isinstance(threshold,(int,float)) and .01<=threshold<=1:self.threshold.setValue(threshold)
        deadline=settings.get('deadline_ms',DEFAULT_DEADLINE_MS)
        if type(deadline) is not int or not self.deadline_ms.minimum()<=deadline<=self.deadline_ms.maximum():
            deadline=DEFAULT_DEADLINE_MS
        self.deadline_ms.setValue(deadline)
        self.check_date.setChecked(True)
        expected_date=settings.get('expected_date')
        if expected_date:
            parsed=QDate.fromString(expected_date,'yyyy-MM-dd')
            if parsed.isValid():self.date.setDate(parsed)
        else:
            self.date.setDate(QDate(2026,8,22))
        trial_date=QDate.fromString(self.config.get('trial_expected_date') or '', 'yyyy-MM-dd')
        self.trial_date.setDate(trial_date if trial_date.isValid() else self.date.date())
        trial_ab=str(self.config.get('trial_expected_ab',self.ab.text()))
        self.trial_ab.setText(trial_ab if len(trial_ab)==2 and trial_ab.isascii() and trial_ab.isdigit() else self.ab.text())
        trial_dm=self.trial_dm.findText(str(self.config.get('trial_expected_dm',self.dm.currentText())))
        self.trial_dm.setCurrentIndex(trial_dm if trial_dm>=0 else self.dm.currentIndex())
        mode=self.roi_mode.findData(self.config.get('roi_mode','auto'))
        self.roi_mode.setCurrentIndex(mode if mode>=0 else 0)
        hardware=self.config.get('hardware',{})
        source=self.source_mode.findData(hardware.get('source_mode','static'))
        self.source_mode.setCurrentIndex(source if source>=0 else 0)
        self.camera_serial.setText(str(hardware.get('serial_number','')))
        line=self.trigger_line.findText(str(hardware.get('trigger_source','Line0')))
        self.trigger_line.setCurrentIndex(line if line>=0 else 0)
        exposure=hardware.get('exposure_us',0)
        if type(exposure) is int and 0<=exposure<=self.exposure_us.maximum():
            self.exposure_us.setValue(exposure)
        eject=self.config.get('ejector',{})
        self.eject_enabled.setChecked(bool(eject.get('enabled',False)))
        self.eject_port.setCurrentText(str(eject.get('port','')))
        self.eject_slave.setCurrentText(str(eject.get('slave',1)))
        self.eject_channel.setCurrentText(str(eject.get('channel',1)))
        self.eject_delay.setValue(float(eject.get('decision_delay_ms',0)))
        self.eject_pulse.setValue(float(eject.get('pulse_ms',100)))
    def save_operator_settings(self):
        self.config['operator_tab']=self.mode_tabs.currentIndex()
        self.config['trial_expected_date']=self.trial_date.date().toString('yyyy-MM-dd')
        self.config['trial_expected_ab']=self.trial_ab.text()
        self.config['trial_expected_dm']=int(self.trial_dm.currentText())
        self.config['roi_mode']=self.roi_mode.currentData()
        self.config['inspection']={'expected_ab':self.ab.text(),'expected_dm':int(self.dm.currentText()),
            'minimum_confidence':self.threshold.value(),'check_nsx_date':True,
            'expected_date':self.date.date().toString('yyyy-MM-dd'),'deadline_ms':self.deadline_ms.value()}
        self.config['hardware']={'source_mode':self.source_mode.currentData(),
            'provider':'hikrobot_mvs','serial_number':self.camera_serial.text().strip(),
            'trigger_mode':'hardware','trigger_source':self.trigger_line.currentText(),
            'exposure_us':self.exposure_us.value(),
            'frame_timeout_ms':100,'external_trigger_only':True}
        self.config['ejector']={'enabled':self.eject_enabled.isChecked(),
            'port':self.eject_port.currentText().strip(),'baudrate':9600,
            'slave':int(self.eject_slave.currentText()) if self.eject_slave.currentText().isdigit() else 1,
            'channel':int(self.eject_channel.currentText()),
            'decision_delay_ms':int(self.eject_delay.value()),'pulse_ms':int(self.eject_pulse.value())}
        if self.path and self.path.is_file():self.config['last_image']=str(self.path.resolve())
        write_json(self.config_path,self.config)
    def save_configuration(self,*,trial=False):
        try:
            self.current_expected(trial=trial)
        except ValueError as exc:
            self.show_status('Không thể lưu: '+str(exc));return
        if self.eject_enabled.isChecked():
            try:self.current_eject_settings().validate()
            except (ValueError,EjectorError) as exc:
                self.show_status('Không thể lưu bộ đẩy: '+str(exc));return
        if self.roi_mode.currentData() in ('fixed','guided'):
            self.config['fixed_roi']=self.roi.to_dict() if self.roi else None
        self.save_operator_settings()
        self.save_config_button.setText('ĐÃ LƯU ✓')
        self.show_status('Đã lưu cấu hình • Lần mở sau sẽ khôi phục đúng thiết lập này')
        QTimer.singleShot(1800,lambda:self.save_config_button.setText('LƯU CẤU HÌNH'))
    def source_changed(self):
        mode=self.source_mode.currentData();camera_running=self.camera_worker.isRunning();live_running=self.live_worker.isRunning()
        test_running=self.eject_test_worker.isRunning()
        running=camera_running or live_running or test_running
        hardware=mode in ('hikrobot','simulation')
        self.camera_serial.setEnabled(mode=='hikrobot' and not running)
        self.trigger_line.setEnabled(mode=='hikrobot' and not running)
        self.exposure_us.setEnabled(not running)
        self.connect_camera_button.setEnabled(hardware and not running)
        self.disconnect_camera_button.setEnabled(camera_running)
        if not live_running:
            self.live_button.setText('BẬT LIVE')
            self.live_button.setEnabled(not camera_running and not test_running)
            self.live_button.setToolTip('Ngắt phiên camera hoặc dừng test xi lanh trước khi bật Live.'
                                        if camera_running or test_running else 'Xem hình trực tiếp để chỉnh nét và ánh sáng.')
        self.test_trigger_button.setEnabled(not running)
        self.trial_trigger_button.setEnabled(not running)
        self.sensor_test_button.setEnabled(not running)
        self.open_button.setEnabled(not running)
        self.sample_button.setEnabled(not running)
        self.run_button.setEnabled(mode=='static' and not running)
        if not running:
            self.camera_state.setText('ẢNH TĨNH' if mode=='static' else 'CHƯA ARM')
            self.camera_state.setStyleSheet('font-weight:700;color:#596b7a')
    def roi_mode_changed(self):
        manual=self.roi_mode.currentData() in ('fixed','guided')
        self.select_roi_button.setEnabled(manual);self.save_roi_button.setEnabled(manual);self.reset_roi_button.setEnabled(manual)
        if manual:self.update_roi()
        else:
            self.coords.setText('AUTO ROI sẽ tự bám theo mẫu bao bì khi kiểm tra')
            self.original.roi=None;self.original.search_roi=None;self.original.update()
            self.show_status('AUTO ROI đang bật • Không cần khoanh vùng bằng tay')
    def toggle_debug(self,enabled):
        for index in range(2,self.tabs.count()):self.tabs.setTabVisible(index,enabled)
    def show_status(self,text):self.status.setText(text)
    def clear_result(self):
        self.banner.setText('SẴN SÀNG KIỂM TRA');self.banner.setStyleSheet('font-size:22px;font-weight:bold;padding:10px;border-radius:6px;background:#e5f0f7;color:#315c76')
        self.details.setHtml('<p style="color:#536d80">Chọn ngày NSX, mã AB và DM rồi bấm <b>KIỂM TRA OCR</b>.</p><p>Kết quả và thời gian xử lý sẽ hiển thị tại đây.</p>')
        self.debug_text.clear()
        for view in self.views.values():view.set_image(None)
    def load_path(self,path,use_sample=False):
        image=load_image(path);self.path=Path(path);self.image=image
        value=self.manifest.get(path.name) if use_sample else self.config.get('fixed_roi')
        self.roi=FixedROI.from_dict(value) if value else None
        self.original.set_image(image,self.roi);self.path_label.setText('Ảnh đang mở: '+path.name);self.path_label.setToolTip(str(path));self.clear_result();self.update_roi()
    def update_roi(self):
        mode=self.roi_mode.currentData()
        if self.roi:
            x,y,w,h=self.roi.bounds(self.image.shape)
            label='Vùng tìm mã' if mode=='guided' else 'Vùng DATE'
            self.coords.setText(f'{label} · {w} × {h} px  ·  X {x} / Y {y}')
        else:self.coords.setText('Chưa khoanh vùng tìm mã')
        self.original.roi=self.roi if mode=='fixed' else None
        self.original.search_roi=self.roi if mode=='guided' else None
        self.original.update()
        if mode=='guided':self.views['roi'].set_image(None)
        elif self.original.roi and self.image is not None:self.views['roi'].set_image(self.roi.crop(self.image))
    def roi_selected(self,roi):self.roi=roi;self.clear_result();self.update_roi();self.show_status('Đã chọn vùng. Bấm LƯU VÙNG để dùng lần sau.')
    def select_roi(self):
        if self.image is not None:
            self.original.selecting=True;self.original.setCursor(Qt.CrossCursor)
            self.show_status('Khoanh đủ vị trí hai dòng mã có thể xuất hiện khi vỉ dịch.'
                             if self.roi_mode.currentData()=='guided' else
                             'Kéo chuột bao quanh hai dòng DATE trên ảnh gốc.')
    def save_roi(self):
        if not self.roi:return self.show_status('Chưa có ROI để lưu.')
        self.config['fixed_roi']=self.roi.to_dict();self.save_operator_settings();self.show_status('Đã lưu ROI và cấu hình hiện tại.')
    def reset_roi(self):
        self.roi=None;self.config['fixed_roi']=None;write_json(self.config_path,self.config);self.clear_result();self.update_roi()
    def open_image(self):
        name,_=QFileDialog.getOpenFileName(self,'Chọn ảnh sản phẩm',str(ROOT/'data/test_images'),'Ảnh (*.jpg *.jpeg *.png *.bmp)')
        if name:
            try:self.load_path(Path(name))
            except Exception as exc:QMessageBox.warning(self,'Không mở được ảnh',str(exc))
    def open_folder(self):
        name=QFileDialog.getExistingDirectory(self,'Chọn thư mục ảnh')
        if name:self.begin([(p,self.roi,self.roi_mode.currentData()) for p in sorted(Path(name).iterdir()) if p.suffix.lower() in EXTENSIONS])
    def samples(self):
        mode=self.roi_mode.currentData()
        self.begin([(ROOT/'data/test_images'/name,FixedROI.from_dict(value) if mode=='fixed' else self.roi,mode) for name,value in self.manifest.items()])
    def run_image(self):
        if self.path:self.begin([(self.path,self.roi,self.roi_mode.currentData())])
    def current_expected(self,*,trial=False):
        date,ab,dm=(self.trial_date,self.trial_ab,self.trial_dm) if trial else (self.date,self.ab,self.dm)
        return Expected(ab.text(),int(dm.currentText()),self.threshold.value(),
            check_nsx_date=True,expected_date=date.date().toPython())
    def begin(self,jobs):
        if self.worker.isRunning() or self.camera_worker.isRunning() or self.live_worker.isRunning() or not jobs:return
        try:expected=self.current_expected(trial=True)
        except ValueError as exc:return QMessageBox.warning(self,'Kiểm tra thiết lập',str(exc))
        if any(mode in ('fixed','guided') and roi is None for _,roi,mode in jobs):
            return QMessageBox.warning(self,'Thiếu ROI','Hãy khoanh vùng tìm mã trước khi kiểm tra.')
        self.save_operator_settings()
        self.clear_result();self.banner.setText('ĐANG ĐỌC…');self.controls.setEnabled(False);self.original.selecting=False
        self.history.setEnabled(False);self.stop.setEnabled(True);self.rows=[];self.history.setRowCount(0)
        self.worker.jobs=jobs;self.worker.expected=expected;self.worker.deadline_ms=self.deadline_ms.value()
        self.worker.start(QThread.Priority.HighPriority)
    def connect_camera(self,*,manual_trigger=False,sensor_test=False):
        if self.worker.isRunning() or self.camera_worker.isRunning() or self.live_worker.isRunning() or self.eject_test_worker.isRunning():return
        capture_once=manual_trigger or sensor_test
        mode='hikrobot' if capture_once else self.source_mode.currentData()
        if mode not in ('hikrobot','simulation'):
            return QMessageBox.information(self,'Nguồn ảnh','Hãy chọn camera Hikrobot hoặc mô phỏng trigger.')
        try:expected=self.current_expected(trial=capture_once or mode=='simulation')
        except ValueError as exc:return QMessageBox.warning(self,'Kiểm tra thiết lập',str(exc))
        try:
            eject=self.current_eject_settings()
            if capture_once:
                from dataclasses import replace
                eject=replace(eject,enabled=False)
            if eject.enabled:
                if mode!='hikrobot':raise EjectorError('Chỉ bật đẩy với camera Hikrobot trigger ngoài')
                eject.validate()
        except (ValueError,EjectorError) as exc:
            return QMessageBox.warning(self,'Thiết lập RS485',str(exc))
        roi_mode=self.roi_mode.currentData()
        if roi_mode in ('fixed','guided') and self.roi is None:
            if roi_mode=='guided' or not capture_once:
                return QMessageBox.warning(self,'Thiếu ROI','Hãy mở một ảnh chuẩn và khoanh vùng tìm mã trước khi kết nối camera.')
            roi_mode='auto'
        if mode=='hikrobot':
            source=HikrobotMvsSource(self.camera_serial.text(),self.trigger_line.currentText(),
                                     software_test=manual_trigger,exposure_us=self.exposure_us.value() or None)
        else:
            source=TriggeredImageSource(capacity=1)
        self.camera_source=source;self.camera_frames=0;self.camera_passes=0;self.rows=[];self.history.setRowCount(0)
        self.save_operator_settings();self.clear_result()
        self.banner.setText('ĐANG NẠP OCR & ARM CAMERA…')
        self.camera_worker.source=source;self.camera_worker.expected=expected
        self.camera_worker.roi=self.roi;self.camera_worker.roi_mode=roi_mode
        self.camera_worker.eject_settings=eject
        self.camera_worker.production=(mode=='hikrobot' and not capture_once)
        self.camera_worker.manual_trigger=manual_trigger
        self.camera_worker.sensor_test=sensor_test
        self.camera_worker.deadline_ms=self.deadline_ms.value()
        self.camera_worker.timeout_ms=int(self.config.get('hardware',{}).get('frame_timeout_ms',100))
        self.source_mode.setEnabled(False);self.connect_camera_button.setEnabled(False)
        self.camera_serial.setEnabled(False);self.trigger_line.setEnabled(False)
        for control in (self.eject_enabled,self.eject_port,self.refresh_ports,self.eject_slave,
                        self.eject_channel,self.eject_delay,self.eject_pulse,self.test_eject_link,
                        self.test_eject_pulse):
            control.setEnabled(False)
        self.camera_worker.start(QThread.Priority.TimeCriticalPriority)
        self.source_changed()
        self.lock_inspection_settings(True)
    def toggle_live_view(self):
        if self.eject_test_worker.isRunning():return
        if self.live_worker.isRunning():
            self.stop_live_view()
            return
        if self.worker.isRunning() or self.camera_worker.isRunning():return
        source=HikrobotMvsSource(self.camera_serial.text(),self.trigger_line.currentText(),
                                 exposure_us=self.exposure_us.value() or None)
        self.live_source=source;self.live_frames=0;self.live_error=False;self.save_operator_settings()
        self.live_worker.source=source
        self.source_mode.setEnabled(False);self.camera_serial.setEnabled(False);self.trigger_line.setEnabled(False)
        self.connect_camera_button.setEnabled(False);self.disconnect_camera_button.setEnabled(False)
        self.test_trigger_button.setEnabled(False);self.trial_trigger_button.setEnabled(False)
        self.sensor_test_button.setEnabled(False);self.exposure_us.setEnabled(False)
        self.live_button.setText('ĐANG MỞ LIVE…');self.live_button.setEnabled(False)
        self.live_info.setText('Đang mở camera…');self.live_view.set_image(None)
        self.live_worker.start(QThread.Priority.HighPriority)
    def stop_live_view(self):
        self.live_worker.requestInterruption();self.live_button.setEnabled(False)
        self.live_button.setText('ĐANG DỪNG…')
        self.show_status('Đang dừng CAMERA LIVE và khôi phục trigger cứng…')
    def live_armed(self,info):
        if info.get('ready') and not self.live_worker.isInterruptionRequested():
            self.camera_state.setText('CAMERA LIVE')
            self.camera_state.setStyleSheet('font-weight:800;color:#0874a8')
            self.live_button.setText('DỪNG LIVE');self.live_button.setEnabled(True)
    def got_live_frame(self,image,metadata):
        if self.live_worker.isInterruptionRequested() or self.live_error:return
        self.live_frames+=1;self.live_view.set_image(image)
        h,w=image.shape[:2];sample=image[::4,::4]
        gray=cv2.cvtColor(sample,cv2.COLOR_BGR2GRAY) if sample.ndim==3 else sample
        brightness=float(gray.mean());sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
        exposure=metadata.get('exposure_us')
        exposure_text=f'  ·  Phơi sáng {exposure:g} µs' if exposure is not None else ''
        self.live_info.setText(f'{w} × {h}  ·  Độ nét {sharpness:.1f}  ·  Độ sáng {brightness:.1f}/255{exposure_text}  ·  Frame {self.live_frames}')
    def live_failed(self,text):
        last=text.strip().splitlines()[-1] if text.strip() else 'Lỗi CAMERA LIVE không xác định'
        self.live_error=True
        self.live_info.setText('Lỗi Camera Live · '+last);self.show_status(last)
    def live_finished(self):
        self.source_mode.setEnabled(True);self.live_source=None
        self.operator_tab_changed(self.mode_tabs.currentIndex())
        self.source_changed()
        if not self.live_error:
            self.live_info.setText(f'Đã dừng Live · {self.live_frames} khung hình đã xem.')
            self.show_status(f'Đã dừng Camera Live · Đã khôi phục {self.trigger_line.currentText()}')
        if self.closing:self.close()
    def disconnect_camera(self):
        if self.camera_worker.isRunning():
            self.camera_worker.requestInterruption();self.disconnect_camera_button.setEnabled(False)
            self.show_status('Đang ngắt camera sau chu kỳ chờ frame hiện tại…')
    def test_camera_trigger(self):
        self.connect_camera(manual_trigger=True)
    def simulate_trigger(self):
        if not self.camera_worker.isRunning():return
        try:
            if isinstance(self.camera_source,TriggeredImageSource):
                if self.image is None:return self.show_status('Chưa có ảnh mẫu để mô phỏng trigger.')
                packet=self.camera_source.trigger(self.image,self.path.name if self.path else 'current')
                self.show_status(f'Đã phát trigger thử #{packet.frame_id} • đang xử lý')
        except FrameSourceError as exc:
            self.banner.setText('KHÔNG ĐẠT  ·  ĐANG XỬ LÝ ẢNH TRƯỚC')
            self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:#ffe0df;color:#a72525')
            self.show_status(str(exc))
    def camera_armed(self,info):
        ready=bool(info.get('ready'))
        if ready:
            if self.camera_worker.production:
                self.production_state.setText(f'ĐÃ ARM · NSX {self.date.date().toString("dd/MM/yyyy")} · AB {self.ab.text()} · DM {self.dm.currentText()}')
            self.camera_state.setText('ĐÃ ARM • CHỜ TRIGGER')
            self.camera_state.setStyleSheet('font-weight:800;color:#087448')
            self.disconnect_camera_button.setEnabled(True)
            ready_text='CHỜ ẢNH MÔ PHỎNG'
            if self.camera_worker.production:ready_text='CHỜ TRIGGER NGOÀI'
            if self.camera_worker.sensor_test:ready_text='CHỜ 1 XUNG CẢM BIẾN'
            self.banner.setText('SẴN SÀNG  ·  '+ready_text)
            self.banner.setStyleSheet('font-size:22px;font-weight:bold;padding:10px;background:#d7f3e4;color:#11663f')
        else:
            self.production_state.setText('Camera đã ngắt · Kết nối tại tab Cài đặt')
            self.camera_state.setText('ĐÃ NGẮT')
            self.camera_state.setStyleSheet('font-weight:700;color:#596b7a')
    def camera_failed(self,text):
        last=text.strip().splitlines()[-1] if text.strip() else 'Lỗi camera không xác định'
        self.banner.setText('LỖI CAMERA / SDK')
        self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:#ffe0df;color:#a72525')
        self.details.setPlainText(text);self.show_status(last)
    def camera_finished(self):
        self.lock_inspection_settings(False)
        self.source_mode.setEnabled(True);self.camera_source=None
        for control in (self.eject_enabled,self.eject_port,self.refresh_ports,self.eject_slave,
                        self.eject_channel,self.eject_delay,self.eject_pulse,self.test_eject_link,
                        self.test_eject_pulse):
            control.setEnabled(True)
        self.disconnect_camera_button.setEnabled(False);self.source_changed()
        if self.camera_frames:
            passes=self.camera_passes
            label='Đã chụp thử và ngắt camera' if self.camera_worker.manual_trigger or self.camera_worker.sensor_test else 'Đã ngắt camera'
            self.show_status(f'{label} · {self.camera_frames} frame · {passes} đạt · {self.camera_frames-passes} lỗi')
        elif not self.details.toPlainText():self.show_status('Camera đã ngắt.')
        if self.closing:self.close()
    def current_eject_settings(self):
        return EjectorSettings(port=self.eject_port.currentText().strip(),baudrate=9600,
            slave=int(self.eject_slave.currentText() or '0'),
            channel=int(self.eject_channel.currentText()),
            decision_delay_ms=int(self.eject_delay.value()),pulse_ms=int(self.eject_pulse.value()),
            enabled=self.eject_enabled.isChecked())
    def refresh_eject_ports(self):
        try:
            from serial.tools import list_ports
            current=self.eject_port.currentText()
            self.eject_port.clear()
            self.eject_port.addItems([item.device for item in list_ports.comports()])
            self.eject_port.setCurrentText(current)
        except Exception as exc:self.show_status('Không quét được COM: '+str(exc))
    def check_eject_link(self):
        if self.camera_worker.isRunning() or self.live_worker.isRunning() or self.eject_test_worker.isRunning():return
        try:
            settings=self.current_eject_settings()
            # Link test only confirms communication and OFF readback. The
            # A calibrated decision delay is required later, before arming ejection.
            from dataclasses import replace
            settings=replace(settings,decision_delay_ms=max(1,settings.decision_delay_ms))
            settings.validate()
            output=ModbusOutput(settings)
            output.open()  # OFF write + readback, never ON
            output.close()
            self.eject_state.setText(f'RS485 OK · DO{settings.channel} OFF')
            self.show_status('Đã xác nhận module RS485 trả lời và DO đang OFF')
        except Exception as exc:
            self.eject_state.setText('RS485 LỖI')
            self.show_status('RS485: '+str(exc))
    def test_ng_eject(self):
        if (self.worker.isRunning() or self.camera_worker.isRunning() or
                self.live_worker.isRunning() or self.eject_test_worker.isRunning()):return
        try:
            settings=self.current_eject_settings()
            settings.validate()
        except (ValueError,EjectorError) as exc:
            return QMessageBox.warning(self,'Test xi lanh',str(exc))
        self.save_operator_settings()
        self.eject_test_worker.settings=settings
        self.test_eject_pulse.setEnabled(False)
        self.connect_camera_button.setEnabled(False)
        self.test_eject_link.setEnabled(False)
        for control in (self.eject_port,self.eject_slave,self.eject_channel,self.eject_delay,self.eject_pulse):
            control.setEnabled(False)
        self.eject_state.setText('TEST NG · ĐANG CHỜ DELAY')
        self.eject_test_worker.start()
        self.source_changed()
    def eject_test_failed(self,detail):
        self.eject_state.setText('TEST NG · LỖI')
        self.show_status('Test xi lanh lỗi: '+detail)
    def eject_test_finished(self):
        self.test_eject_pulse.setEnabled(True)
        self.test_eject_link.setEnabled(True)
        for control in (self.eject_port,self.eject_slave,self.eject_channel,self.eject_delay,self.eject_pulse):
            control.setEnabled(True)
        self.source_changed()
        if self.closing:self.close()
    def on_eject_event(self,event):
        phase=event['phase'];frame=event['frame_id']
        test=bool(event.get('test'))
        if not test and self.camera_worker.output and not self.camera_worker.production:
            path=self.camera_worker.output/'eject_events.jsonl'
            with path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        self.eject_state.setText(f'TEST NG · {phase}' if test else f'RS485 {phase} · frame {frame}')
        if phase=='ERROR':
            if not test:self.camera_worker.requestInterruption()
            self.banner.setText('LỖI RS485 · DỪNG CAMERA' if not test else 'TEST XI LANH LỖI')
            self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:#ffe0df;color:#a72525')
            self.show_status(str(event['detail']))
        elif phase=='SCHEDULED':
            self.show_status(f'NG giả lập: chờ {self.eject_delay.value():.0f} ms rồi bật DO{self.eject_channel.currentText()}')
        else:self.show_status(f'Xi lanh {phase} · '+('NG giả lập' if test else f'frame {frame}'))
    def got_result(self,report,folder):
        self.append_result(report,folder);self.show_record(report,folder);self.latest_report=report
    def got_camera_result(self,report,folder,artifacts):
        self.camera_frames+=1;self.append_result(report,folder)
        self.camera_passes+=int(report['validation']['result']=='PASS')
        self.show_record(report,folder,artifacts);self.latest_report=report
        mode='TRIGGER TAY' if self.camera_worker.manual_trigger else 'CAMERA ĐÃ ARM'
        title,_=result_text(report)
        self.show_status(f'{mode} • Ảnh {self.camera_frames} • {title} • {report.get("processing_ms",0):.1f} ms')
    def append_result(self,report,folder):
        if report.get('production') and len(self.rows)>=500:
            self.rows.pop(0);self.history.removeRow(0)
        self.rows.append((report,folder));row=self.history.rowCount();self.history.insertRow(row)
        v=report['validation'];p=v.get('parsed') or {};conf=v.get('overall_confidence')
        date_text=f"{p['day']:02d}/{p['month']:02d}/{p['year']}" if p else '—'
        title,reason=result_text(report)
        values=[report['image'],date_text,p.get('dm','—'),p.get('ab','—'),p.get('time','—'),f'{conf:.2%}' if conf is not None else '—',f'{report.get("processing_ms",0):.1f} ms',title]
        for col,value in enumerate(values):
            item=QTableWidgetItem(str(value));item.setBackground(QColor('#edf8f1' if v['result']=='PASS' else '#fff0ed'));self.history.setItem(row,col,item)
        self.history.item(row,self.history.columnCount()-1).setForeground(QColor('#117449' if v['result']=='PASS' else '#b32b30'))
        self.history.item(row,self.history.columnCount()-1).setToolTip(reason)
    def show_record(self,report,folder,artifacts=None):
        v=report['validation'];ok=v['result']=='PASS';title,reason=result_text(report)
        self.banner.setText('ĐẠT' if ok else 'KHÔNG ĐẠT  ·  '+title.upper())
        self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:'+('#d7f3e4;color:#11663f' if ok else '#ffe0df;color:#a72525'))
        camera=bool(report.get('hardware'))
        if camera:
            self.path_label.setText(f'Frame camera: {report["image"]}');self.path_label.setToolTip(report['source'])
        else:
            self.path=Path(report['source']);self.path_label.setText('Ảnh đang xem: '+self.path.name);self.path_label.setToolTip(str(self.path))
        record_image=artifacts.get('original') if artifacts else None
        if record_image is None and (folder/'original.png').exists():
            record_image=load_image(folder/'original.png')
        if record_image is not None:
            self.image=record_image
            bbox=report.get('bbox')
            display_roi=FixedROI(*bbox,self.image.shape[1],self.image.shape[0]) if bbox else None
            search_bbox=report.get('search_bbox')
            search_roi=FixedROI(*search_bbox,self.image.shape[1],self.image.shape[0]) if search_bbox else None
            self.original.set_image(self.image,display_roi,search_roi=search_roi)
        elif report.get('production'):
            self.original.set_image(None)
        for name,view in self.views.items():
            if artifacts is not None:view.set_image(artifacts.get(name))
            else:view.set_image(load_image(folder/(name+'.png')) if (folder/(name+'.png')).exists() else None)
        lines=[v.get('raw',''),'',f"Kết quả: {title} | {report['processing_ms']} ms",f"Lý do: {reason}",f"Độ tin cậy: {v.get('confidence',{})}",f"Yêu cầu: {report.get('expected',{})}",f"Chẩn đoán kỹ thuật: {v['result']} · {v['error_reason']}"]
        if report.get('auto_roi'):lines.append('AUTO ROI: '+str(report['auto_roi']))
        for c in report.get('candidates',[]):lines.extend(['',c['name']+': '+c['raw'].replace('\n',' / ')])
        self.debug_text.setPlainText('\n'.join(lines))
        p=v.get('parsed') or {};conf=v.get('overall_confidence');expected=report.get('expected',{})
        deadline=report.get('deadline_ms',DEFAULT_DEADLINE_MS)
        expected_date=QDate.fromString(expected.get('expected_date') or '', 'yyyy-MM-dd')
        expected_day=expected_date.toString('dd/MM/yyyy') if expected_date.isValid() else '—'
        day=f"{p['day']:02d}/{p['month']:02d}/{p['year']}" if p else '—'
        fields=[('NGÀY SẢN XUẤT',day),('DM',p.get('dm','—')),('AB',p.get('ab','—')),('GIỜ IN',p.get('time','—'))]
        cells=''.join(f'<td><span style="color:#60798b;font-size:10px">{key}</span><br><span style="font-size:19px;font-weight:600">{html.escape(str(value))}</span></td>' for key,value in fields)
        confidence_text=f'{conf:.2%}' if conf is not None else '—'
        elapsed=report.get('processing_ms',0);timing_color='#117449' if report.get('deadline_met') else '#b32b30'
        late_text=' · QUÁ GIỚI HẠN' if elapsed>deadline else ''
        device_text='GPU CUDA' if str(report.get('ocr_device','')).startswith('cuda') else 'CPU'
        if report.get('ocr_mode')=='PP_OCR_LINES':device_text='PP-OCR · CPU'
        auto=report.get('auto_roi');auto_text=(f' &nbsp; | &nbsp; AUTO ROI: <b>{auto["confidence"]:.1%}</b>' if auto and auto.get('found') else '')
        self.details.setHtml(f'<table width="100%" cellpadding="3"><tr>{cells}</tr></table>'
            f'<p style="color:{"#117449" if ok else "#b32b30"}">{reason}</p>'
            f'<p style="color:#496778">Yêu cầu: NSX <b>{expected_day}</b> · AB <b>{html.escape(str(expected.get("ab","—")))}</b> · DM <b>{expected.get("dm","—")}</b>'
            f' &nbsp; | &nbsp; Độ tin cậy: <b>{confidence_text}</b>{auto_text}</p>'
            f'<p style="color:{timing_color}">Thời gian quyết định: <b>{elapsed:.1f} ms</b> / giới hạn {deadline} ms{late_text} · OCR: <b>{device_text}</b></p>')
    def history_selected(self,row,col):
        if row<len(self.rows):self.show_record(*self.rows[row])
    def failed(self,text):self.banner.setText('LỖI OCR');self.details.setPlainText(text);self.show_status('Có lỗi — xem chi tiết bên phải.')
    def finished(self):
        self.controls.setEnabled(True);self.history.setEnabled(True);self.stop.setEnabled(False)
        if self.rows:
            passes=sum(r['validation']['result']=='PASS' for r,f in self.rows)
            self.show_status(f'Hoàn tất  ·  {len(self.rows)} ảnh  ·  {passes} đạt  ·  {len(self.rows)-passes} lỗi  ·  Đã lưu kết quả')
        if self.closing:self.close()
    def open_output(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        output=self.camera_worker.output if self.camera_frames else self.worker.output
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(output or ROOT/'data/debug')))
    def open_daily_excel(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        path=ROOT/'data/production'/f'OCR_DATE_{now_local().date().isoformat()}.xlsx'
        if not path.exists():return self.show_status('Chưa có Excel hôm nay. Kết quả được xuất nền mỗi 5 giây và khi ngắt camera.')
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def closeEvent(self,event):
        self.save_operator_settings()
        if (self.worker.isRunning() or self.camera_worker.isRunning() or
                self.live_worker.isRunning() or self.eject_test_worker.isRunning()):
            self.closing=True
            if self.worker.isRunning():self.worker.requestInterruption()
            if self.camera_worker.isRunning():self.camera_worker.requestInterruption()
            if self.live_worker.isRunning():self.live_worker.requestInterruption()
            if self.eject_test_worker.isRunning():self.eject_test_worker.requestInterruption()
            self.show_status('Đang hoàn tất ảnh hiện tại và ngắt camera trước khi đóng…');event.ignore()
        else:event.accept()


def main():
    logs=ROOT/'data/logs';logs.mkdir(parents=True,exist_ok=True)
    optimize_process_priority()
    # pythonw has no console streams; preserve diagnostics in a bounded per-session log.
    log=(logs/'gui.log').open('w',encoding='utf-8',buffering=1)
    sys.stdout=log;sys.stderr=log
    app=QApplication(sys.argv);app.setStyle('Fusion');window=MainWindow();window.show()
    marker=os.environ.get('OCR_DATE_PORTABLE_SMOKE_MARKER')
    if marker:
        state={'report':None,'finished':False}
        window.load_path(ROOT/'data/test_images/date1.jpg',use_sample=True)
        window.source_mode.setCurrentIndex(window.source_mode.findData('simulation'))
        window.roi_mode.setCurrentIndex(window.roi_mode.findData('auto'))
        window.trial_ab.setText('12');window.trial_dm.setCurrentText('1');window.threshold.setValue(.70)
        window.trial_date.setDate(QDate(2026,8,22))
        def smoke_armed(info):
            if info.get('ready') and state['report'] is None:QTimer.singleShot(0,window.simulate_trigger)
        def smoke_result(report,folder,artifacts):
            state['report']=report;QTimer.singleShot(0,window.disconnect_camera)
        def smoke_finished():
            if state['finished']:return
            state['finished']=True;report=state['report'] or {}
            payload={'result':(report.get('validation') or {}).get('result','NO_RESULT'),
                'processing_ms':report.get('processing_ms'),'deadline_met':report.get('deadline_met',False),
                'ocr_device':report.get('ocr_device','unknown'),
                'hardware':report.get('hardware',{})}
            Path(marker).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            app.exit(0 if payload['result']=='PASS' and payload['deadline_met'] else 1)
        def smoke_timeout():
            if state['finished']:return
            Path(marker).write_text(json.dumps({'result':'TIMEOUT'}),encoding='utf-8');app.exit(2)
        window.camera_worker.armed.connect(smoke_armed)
        window.camera_worker.result.connect(smoke_result)
        window.camera_worker.finished.connect(smoke_finished)
        QTimer.singleShot(0,window.connect_camera);QTimer.singleShot(120000,smoke_timeout)
    sys.exit(app.exec())

if __name__=='__main__':main()
