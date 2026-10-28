"""Vietnamese GUI for still images and hardware-triggered HIKROBOT frames."""
import sys, json, traceback, html, time, gc, os
from pathlib import Path
from datetime import datetime
import uuid
import cv2
from PySide6.QtCore import Qt, QRectF, QPointF, QThread, Signal, QDate, QTimer
from PySide6.QtGui import QPainter, QImage, QColor, QPen, QRegularExpressionValidator, QFont, QFontDatabase
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QPushButton,QLabel,QLineEdit,QComboBox,QDoubleSpinBox,QCheckBox,QDateEdit,
    QFileDialog,QMessageBox,QSplitter,QTabWidget,QTextEdit,QTableWidget,QTableWidgetItem,
    QHeaderView,QGroupBox)
from main import load_image, save_image, write_json, save_report, ROOT, EXTENSIONS
from roi.fixed_roi import FixedROI
from roi.auto_roi import AutoROIDetector
from ocr.validator import Expected
from inspection_runtime import InspectionRuntime
from hardware.frame_source import FrameSourceError,missing_frame_count
from hardware.simulated_source import TriggeredImageSource
from hardware.hikrobot_mvs import HikrobotMvsSource
from gui_theme import apply_theme


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
        super().__init__(); self.image=None; self.roi=None; self.selecting=False; self.start=None; self.end=None
        self.setMinimumSize(250,90)
    def set_image(self,image,roi=None):
        self.image=image; self.roi=roi; self.update()
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
        p.setPen(QPen(QColor('#08a65c'),2))
        if self.roi:
            x,y,w,h=self.roi.bounds(a.shape); sx=area.width()/a.shape[1]; sy=area.height()/a.shape[0]
            p.drawRect(QRectF(area.x()+x*sx,area.y()+y*sy,w*sx,h*sy))
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
    def run(self):
        records=[]
        try:
            self.runtime.prepare(any(mode=='auto' for _,_,mode in self.jobs),self.status.emit)
            self.output=ROOT/'data/debug'/('gui_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
            self.output.mkdir(parents=True)
            for i,(path,roi,roi_mode) in enumerate(self.jobs):
                if self.isInterruptionRequested():break
                self.status.emit(f'Đang kiểm tra {i+1}/{len(self.jobs)}: {path.name}')
                folder=self.output/f'{i+1:03d}_{path.stem}';folder.mkdir()
                try:
                    image=load_image(path)
                    report,artifacts=self.runtime.inspect(image,roi,roi_mode,self.expected,
                        image_name=path.name,source=str(path))
                    for name,array in artifacts.items():save_image(folder/f'{name}.png',array)
                except Exception as exc:
                    report=dict(image=path.name,source=str(path),artifacts=[],processing_ms=0,
                        validation=dict(result='FAIL_OCR',raw='',parsed=None,confidence={},overall_confidence=None,error_reason=str(exc)))
                write_json(folder/'result.json',report);records.append(report)
                self.result.emit(report,folder)
            save_report(self.output,records)
            self.status.emit(f'Đã kiểm tra {len(records)} ảnh. Kết quả đã lưu.')
        except Exception:
            self.error.emit(traceback.format_exc())


class CameraWorker(QThread):
    """Arm one frame source and inspect every externally-triggered frame."""
    result=Signal(object,object,object)
    status=Signal(str)
    error=Signal(str)
    armed=Signal(object)
    def __init__(self,runtime=None):
        super().__init__();self.runtime=runtime or InspectionRuntime();self.source=None
        self.expected=None;self.roi=None;self.roi_mode='auto';self.timeout_ms=100;self.output=None
    def run(self):
        records=[]
        try:
            self.runtime.prepare(self.roi_mode=='auto',self.status.emit)
            self.status.emit('Đang mở camera và bật trigger cứng…')
            self.source.open();self.source.start()
            self.output=ROOT/'data/debug'/('camera_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
            self.output.mkdir(parents=True)
            self.armed.emit({'ready':True,'source':type(self.source).__name__})
            self.status.emit('CAMERA ĐÃ ARM • Chờ tín hiệu trigger ngoài')
            sequence=0;last_frame_id=None
            while not self.isInterruptionRequested():
                packet=self.source.get_frame(self.timeout_ms)
                if packet is None:continue
                sequence+=1
                name=f'frame_{packet.frame_id or sequence:08d}_{datetime.now().strftime("%H%M%S_%f")[:-3]}.png'
                folder=self.output/f'{sequence:06d}_{Path(name).stem}';folder.mkdir()
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
                        received_monotonic=packet.received_monotonic,metadata=metadata)
                except Exception as exc:
                    report=dict(image=name,source=packet.source,artifacts=['original'],processing_ms=0,
                        deadline_ms=100,deadline_met=False,hardware=metadata,
                        validation=dict(result='FAIL_OCR',raw='',parsed=None,confidence={},
                            overall_confidence=None,error_reason=str(exc)))
                    artifacts={'original':packet.image}
                if missing:
                    previous=report['validation'].get('error_reason','')
                    report['validation'].update(result='FAIL_FRAME_GAP',
                        error_reason=(f'Mất {missing} frame trigger trước frame #{packet.frame_id}. '
                                      f'{previous}').strip())
                records.append(report)
                # The operator sees the decision before diagnostic files are written.
                self.result.emit(report,folder,artifacts)
                for artifact_name,array in artifacts.items():
                    save_image(folder/f'{artifact_name}.png',array)
                write_json(folder/'result.json',report)
            save_report(self.output,records)
        except Exception:
            self.error.emit(traceback.format_exc())
        finally:
            if self.source is not None:
                try:self.source.stop()
                except Exception:pass
                try:self.source.close()
                except Exception:pass
            self.armed.emit({'ready':False})


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
        self.camera_worker.finished.connect(self.camera_finished)
        self.live_worker=LiveViewWorker()
        self.live_worker.frame.connect(self.got_live_frame)
        self.live_worker.status.connect(self.show_status)
        self.live_worker.error.connect(self.live_failed)
        self.live_worker.armed.connect(self.live_armed)
        self.live_worker.finished.connect(self.live_finished)
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central)
        layout.setContentsMargins(18,12,18,12);layout.setSpacing(8)
        title=QLabel('OCR DATE  ·  Kiểm tra ngày sản xuất');title.setObjectName('title');layout.addWidget(title)
        subtitle=QLabel('Ảnh tĩnh hoặc Hikrobot trigger cứng  →  ROI  →  DATE + góc dải tối  →  PASS/FAIL ≤ 100 ms')
        subtitle.setObjectName('subtitle');layout.addWidget(subtitle)
        self.controls=QWidget();self.controls.setObjectName('controls');top=QVBoxLayout(self.controls)
        top.setContentsMargins(12,10,12,10);top.setSpacing(8);layout.addWidget(self.controls)
        camera_bar=QHBoxLayout();top.addLayout(camera_bar)
        camera_bar.addWidget(QLabel('Nguồn ảnh:'))
        self.source_mode=QComboBox();self.source_mode.addItem('Ảnh tĩnh / thư mục','static')
        self.source_mode.addItem('Camera Hikrobot · trigger ngoài','hikrobot')
        self.source_mode.addItem('Mô phỏng trigger','simulation');camera_bar.addWidget(self.source_mode)
        camera_bar.addWidget(QLabel('Serial:'))
        self.camera_serial=QLineEdit();self.camera_serial.setPlaceholderText('Để trống = camera đầu tiên')
        self.camera_serial.setMaximumWidth(190);camera_bar.addWidget(self.camera_serial)
        camera_bar.addWidget(QLabel('Ngõ vào:'))
        self.trigger_line=QComboBox();self.trigger_line.addItems(['Line0','Line1']);camera_bar.addWidget(self.trigger_line)
        self.connect_camera_button=QPushButton('KẾT NỐI + ARM');self.connect_camera_button.clicked.connect(self.connect_camera)
        self.connect_camera_button.setObjectName('primary');camera_bar.addWidget(self.connect_camera_button)
        self.disconnect_camera_button=QPushButton('NGẮT CAMERA');self.disconnect_camera_button.clicked.connect(self.disconnect_camera)
        camera_bar.addWidget(self.disconnect_camera_button)
        self.camera_state=QLabel('ẢNH TĨNH');self.camera_state.setObjectName('cameraState');camera_bar.addWidget(self.camera_state)
        camera_bar.addStretch()
        camera_test_bar=QHBoxLayout();top.addLayout(camera_test_bar)
        self.camera_test_label=QLabel('Chỉnh / kiểm tra camera:');camera_test_bar.addWidget(self.camera_test_label)
        self.live_button=QPushButton('CAMERA LIVE');self.live_button.clicked.connect(self.toggle_live_view)
        self.live_button.setToolTip('Xem hình liên tục để chỉnh lens. Dừng LIVE sẽ tự khôi phục trigger ngoài.')
        camera_test_bar.addWidget(self.live_button)
        self.test_trigger_button=QPushButton('TRIGGER TEST (SOFTWARE)');self.test_trigger_button.clicked.connect(self.test_camera_trigger)
        self.test_trigger_button.setToolTip('Phát đúng 1 software trigger để kiểm tra kết nối; camera tự trở về ngõ trigger ngoài sau đó.')
        camera_test_bar.addWidget(self.test_trigger_button)
        self.camera_test_hint=QLabel('LIVE chỉ dùng chỉnh lens · Sản xuất vẫn dùng Line0')
        self.camera_test_hint.setObjectName('subtitle');camera_test_bar.addWidget(self.camera_test_hint);camera_test_bar.addStretch()
        self.source_mode.currentIndexChanged.connect(self.source_changed)
        bar=QHBoxLayout();top.addLayout(bar)
        def button(text,fn,parent=bar):
            b=QPushButton(text);b.clicked.connect(fn);parent.addWidget(b);return b
        self.open_button=button('1. Chọn ảnh',self.open_image)
        button('Chọn thư mục',self.open_folder)
        self.sample_button=button('Thử 5 ảnh mẫu',self.samples)
        bar.addStretch()
        self.save_config_button=button('LƯU CẤU HÌNH',self.save_configuration)
        self.save_config_button.setShortcut('Ctrl+S')
        self.save_config_button.setToolTip('Lưu toàn bộ thiết lập hiện tại để khôi phục khi mở lại (Ctrl+S).')
        self.run_button=button('KIỂM TRA OCR',self.run_image);self.run_button.setObjectName('primary')
        settings=QHBoxLayout();top.addLayout(settings)
        settings.addWidget(QLabel('Mã AB cần kiểm tra:'));self.ab=QLineEdit('12');self.ab.setMaximumWidth(65);self.ab.setMaxLength(2)
        self.ab.setValidator(QRegularExpressionValidator(QRegularExpression('[0-9]{2}')));settings.addWidget(self.ab)
        settings.addWidget(QLabel('DM:'));self.dm=QComboBox();self.dm.addItems(['1','2','3']);settings.addWidget(self.dm)
        settings.addWidget(QLabel('Ngưỡng tin cậy:'));self.threshold=QDoubleSpinBox();self.threshold.setRange(.01,1);self.threshold.setSingleStep(.01);self.threshold.setValue(.8);settings.addWidget(self.threshold)
        self.threshold.setToolTip('Mỗi trường phải đạt ngưỡng này. Không hạ ngưỡng để ép kết quả ĐẠT.')
        settings.addWidget(QLabel('Chế độ ROI:'));self.roi_mode=QComboBox();self.roi_mode.addItem('Tự động (khuyên dùng)','auto');self.roi_mode.addItem('Cố định / khoanh tay','fixed');settings.addWidget(self.roi_mode)
        self.check_date=QCheckBox();self.check_date.setChecked(True);self.check_date.hide()
        self.nsx_label=QLabel('Ngày NSX cần kiểm tra:');settings.addWidget(self.nsx_label)
        self.date=QDateEdit(QDate.currentDate());self.date.setCalendarPopup(True);self.date.setDisplayFormat('dd/MM/yyyy');settings.addWidget(self.date);settings.addStretch()
        self.date.setEnabled(True);self.date.setToolTip('Chọn ngày NSX cần có trên sản phẩm. Ngày đã chọn sẽ được ghi nhớ.')
        settings.insertWidget(settings.count()-1,QLabel('Lệch góc tối đa:'))
        self.max_angle=QDoubleSpinBox();self.max_angle.setRange(.1,15);self.max_angle.setDecimals(1);self.max_angle.setSingleStep(.1);self.max_angle.setValue(3.0);self.max_angle.setSuffix('°')
        self.max_angle.setToolTip('Độ lệch cho phép giữa hướng chữ DATE và mép dải ngang tối phía dưới.')
        settings.insertWidget(settings.count()-1,self.max_angle)
        self.roi_mode.currentIndexChanged.connect(self.roi_mode_changed)
        roi_bar=QHBoxLayout();top.addLayout(roi_bar)
        self.select_roi_button=button('2. Khoanh vùng DATE',self.select_roi,roi_bar)
        self.save_roi_button=button('Lưu vùng',self.save_roi,roi_bar)
        self.reset_roi_button=button('Xóa vùng',self.reset_roi,roi_bar)
        self.coords=QLabel('');roi_bar.addWidget(self.coords);roi_bar.addStretch()
        self.path_label=QLabel('Chọn ảnh để bắt đầu');self.path_label.setObjectName('fileLabel');layout.addWidget(self.path_label)
        split=QSplitter(Qt.Horizontal);layout.addWidget(split,1)
        left=QWidget();left.setObjectName('imagePanel');ll=QVBoxLayout(left)
        image_label=QLabel('ẢNH SẢN PHẨM  ·  Khung xanh là vùng kiểm tra');image_label.setObjectName('sectionLabel');ll.addWidget(image_label)
        self.original=ImageView();self.original.selected.connect(self.roi_selected);ll.addWidget(self.original,1);split.addWidget(left)
        right=QWidget();right.setObjectName('resultPanel');rl=QVBoxLayout(right)
        self.banner=QLabel('SẴN SÀNG');self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:#dae9f0');rl.addWidget(self.banner)
        self.tabs=QTabWidget();self.views={}
        for key,label in [('roi','ROI DATE'),('final','Ảnh OCR'),('alignment','DATE ↔ dải tối'),('gray','Xám'),('otsu','Otsu'),('clahe','CLAHE'),('adaptive','Adaptive'),('morphology','Morphology')]:
            view=ImageView();self.views[key]=view;self.tabs.addTab(view,label)
        self.debug_text=QTextEdit();self.debug_text.setReadOnly(True);self.tabs.addTab(self.debug_text,'Chi tiết OCR')
        self.debug_toggle=QCheckBox('Hiện ảnh xử lý và chi tiết OCR');self.debug_toggle.toggled.connect(self.toggle_debug);rl.addWidget(self.debug_toggle)
        self.toggle_debug(False)
        rl.addWidget(self.tabs,1)
        self.details=QTextEdit();self.details.setReadOnly(True);self.details.setFixedHeight(145)
        self.details.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);rl.addWidget(self.details)
        split.addWidget(right);split.setSizes([580,620])
        self.history=QTableWidget(0,9);self.history.setHorizontalHeaderLabels(['Ảnh','Ngày sản xuất','DM','AB','Giờ in','Độ tin cậy','Lệch góc','Xử lý','Kết quả'])
        self.history.setAlternatingRowColors(True);self.history.verticalHeader().hide();self.history.verticalHeader().setDefaultSectionSize(30)
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.history.setFixedHeight(190)
        self.history.setSelectionBehavior(QTableWidget.SelectRows);self.history.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history.cellClicked.connect(self.history_selected);layout.addWidget(self.history)
        footer=QHBoxLayout();layout.addLayout(footer)
        self.status=QLabel('Chạy local • mục tiêu quyết định ≤ 100 ms');footer.addWidget(self.status,1)
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
    def load_operator_settings(self):
        settings=self.config.get('inspection',{})
        ab=str(settings.get('expected_ab','12'))
        if len(ab)==2 and ab.isascii() and ab.isdigit():self.ab.setText(ab)
        dm=self.dm.findText(str(settings.get('expected_dm',1)))
        if dm>=0:self.dm.setCurrentIndex(dm)
        threshold=settings.get('minimum_confidence',.80)
        if isinstance(threshold,(int,float)) and .01<=threshold<=1:self.threshold.setValue(threshold)
        maximum_angle=settings.get('maximum_angle_deviation',3.0)
        if isinstance(maximum_angle,(int,float)) and .1<=maximum_angle<=15:self.max_angle.setValue(maximum_angle)
        self.check_date.setChecked(True)
        expected_date=settings.get('expected_date')
        if expected_date:
            parsed=QDate.fromString(expected_date,'yyyy-MM-dd')
            if parsed.isValid():self.date.setDate(parsed)
        else:
            self.date.setDate(QDate(2026,8,22))
        mode=self.roi_mode.findData(self.config.get('roi_mode','auto'))
        self.roi_mode.setCurrentIndex(mode if mode>=0 else 0)
        hardware=self.config.get('hardware',{})
        source=self.source_mode.findData(hardware.get('source_mode','static'))
        self.source_mode.setCurrentIndex(source if source>=0 else 0)
        self.camera_serial.setText(str(hardware.get('serial_number','')))
        line=self.trigger_line.findText(str(hardware.get('trigger_source','Line0')))
        self.trigger_line.setCurrentIndex(line if line>=0 else 0)
    def save_operator_settings(self):
        self.config['roi_mode']=self.roi_mode.currentData()
        self.config['inspection']={'expected_ab':self.ab.text(),'expected_dm':int(self.dm.currentText()),
            'minimum_confidence':self.threshold.value(),'check_nsx_date':True,
            'expected_date':self.date.date().toString('yyyy-MM-dd'),
            'maximum_angle_deviation':self.max_angle.value()}
        self.config['hardware']={'source_mode':self.source_mode.currentData(),
            'provider':'hikrobot_mvs','serial_number':self.camera_serial.text().strip(),
            'trigger_mode':'hardware','trigger_source':self.trigger_line.currentText(),
            'frame_timeout_ms':100,'external_trigger_only':True}
        if self.path and self.path.is_file():self.config['last_image']=str(self.path.resolve())
        write_json(self.config_path,self.config)
    def save_configuration(self):
        try:
            Expected(self.ab.text(),int(self.dm.currentText()),self.threshold.value(),
                check_nsx_date=True,expected_date=self.date.date().toPython(),
                maximum_angle_deviation=self.max_angle.value())
        except ValueError as exc:
            self.show_status('Không thể lưu: '+str(exc));return
        if self.roi_mode.currentData()=='fixed':
            self.config['fixed_roi']=self.roi.to_dict() if self.roi else None
        self.save_operator_settings()
        self.save_config_button.setText('ĐÃ LƯU ✓')
        self.show_status('Đã lưu cấu hình • Lần mở sau sẽ khôi phục đúng thiết lập này')
        QTimer.singleShot(1800,lambda:self.save_config_button.setText('LƯU CẤU HÌNH'))
    def source_changed(self):
        mode=self.source_mode.currentData();camera_running=self.camera_worker.isRunning();live_running=self.live_worker.isRunning()
        running=camera_running or live_running
        hardware=mode in ('hikrobot','simulation')
        self.camera_serial.setEnabled(mode=='hikrobot' and not running)
        self.trigger_line.setEnabled(mode=='hikrobot' and not running)
        self.connect_camera_button.setEnabled(hardware and not running)
        self.disconnect_camera_button.setEnabled(camera_running)
        self.live_button.setVisible(mode=='hikrobot')
        self.live_button.setText('DỪNG LIVE' if live_running else 'CAMERA LIVE')
        self.live_button.setEnabled(mode=='hikrobot' and not camera_running)
        self.test_trigger_button.setVisible(hardware)
        self.test_trigger_button.setText('TRIGGER THỬ' if mode=='simulation' else 'TRIGGER TEST (SOFTWARE)')
        self.test_trigger_button.setEnabled(hardware and camera_running)
        self.camera_test_label.setVisible(hardware);self.camera_test_hint.setVisible(hardware)
        self.open_button.setEnabled(not running)
        self.sample_button.setEnabled(not running)
        self.run_button.setEnabled(mode=='static' and not running)
        if not running:
            self.camera_state.setText('ẢNH TĨNH' if mode=='static' else 'CHƯA ARM')
            self.camera_state.setStyleSheet('font-weight:700;color:#596b7a')
    def roi_mode_changed(self):
        fixed=self.roi_mode.currentData()=='fixed'
        self.select_roi_button.setEnabled(fixed);self.save_roi_button.setEnabled(fixed);self.reset_roi_button.setEnabled(fixed)
        if fixed:self.update_roi()
        else:
            self.coords.setText('AUTO ROI sẽ tự bám theo mẫu bao bì khi kiểm tra')
            self.show_status('AUTO ROI đang bật • Không cần khoanh vùng bằng tay')
    def toggle_debug(self,enabled):
        for index in range(3,self.tabs.count()):self.tabs.setTabVisible(index,enabled)
    def show_status(self,text):self.status.setText(text)
    def clear_result(self):
        self.banner.setText('SẴN SÀNG KIỂM TRA');self.banner.setStyleSheet('font-size:22px;font-weight:bold;padding:10px;border-radius:6px;background:#e5f0f7;color:#315c76')
        self.details.setHtml('<p style="color:#536d80">Kiểm tra mã AB / DM rồi bấm <b>KIỂM TRA OCR</b>. AUTO ROI sẽ tự tìm hai dòng DATE.</p><p>Kết quả và thời gian xử lý sẽ hiển thị tại đây.</p>')
        self.debug_text.clear()
        for view in self.views.values():view.set_image(None)
    def load_path(self,path,use_sample=False):
        image=load_image(path);self.path=Path(path);self.image=image
        value=self.manifest.get(path.name) if use_sample else self.config.get('fixed_roi')
        self.roi=FixedROI.from_dict(value) if value else None
        self.original.set_image(image,self.roi);self.path_label.setText('Ảnh đang mở: '+path.name);self.path_label.setToolTip(str(path));self.clear_result();self.update_roi()
    def update_roi(self):
        if self.roi:
            x,y,w,h=self.roi.bounds(self.image.shape);self.coords.setText(f'Vùng DATE: {w} × {h} px  ·  X {x} / Y {y}')
        else:self.coords.setText('Chưa chọn vùng DATE')
        self.original.roi=self.roi;self.original.update()
        if self.roi and self.image is not None:self.views['roi'].set_image(self.roi.crop(self.image))
    def roi_selected(self,roi):self.roi=roi;self.clear_result();self.update_roi();self.show_status('Đã chọn ROI. Bấm LƯU CẤU HÌNH để dùng lần sau.')
    def select_roi(self):
        if self.image is not None:self.original.selecting=True;self.original.setCursor(Qt.CrossCursor);self.show_status('Kéo chuột bao quanh đúng hai dòng DATE trên ảnh gốc.')
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
        self.begin([(ROOT/'data/test_images'/name,FixedROI.from_dict(value) if mode=='fixed' else None,mode) for name,value in self.manifest.items()])
    def run_image(self):
        if self.path:self.begin([(self.path,self.roi,self.roi_mode.currentData())])
    def current_expected(self):
        return Expected(self.ab.text(),int(self.dm.currentText()),self.threshold.value(),
            check_nsx_date=True,expected_date=self.date.date().toPython(),
            maximum_angle_deviation=self.max_angle.value())
    def begin(self,jobs):
        if self.worker.isRunning() or self.camera_worker.isRunning() or self.live_worker.isRunning() or not jobs:return
        try:expected=self.current_expected()
        except ValueError as exc:return QMessageBox.warning(self,'Kiểm tra thiết lập',str(exc))
        if any(mode=='fixed' and roi is None for _,roi,mode in jobs):return QMessageBox.warning(self,'Thiếu ROI','Hãy khoanh vùng DATE trước khi kiểm tra.')
        self.save_operator_settings()
        self.clear_result();self.banner.setText('ĐANG ĐỌC…');self.controls.setEnabled(False);self.original.selecting=False
        self.history.setEnabled(False);self.stop.setEnabled(True);self.rows=[];self.history.setRowCount(0)
        self.worker.jobs=jobs;self.worker.expected=expected
        self.worker.start(QThread.Priority.HighPriority)
    def connect_camera(self):
        if self.worker.isRunning() or self.camera_worker.isRunning() or self.live_worker.isRunning():return
        mode=self.source_mode.currentData()
        if mode not in ('hikrobot','simulation'):
            return QMessageBox.information(self,'Nguồn ảnh','Hãy chọn camera Hikrobot hoặc mô phỏng trigger.')
        try:expected=self.current_expected()
        except ValueError as exc:return QMessageBox.warning(self,'Kiểm tra thiết lập',str(exc))
        roi_mode=self.roi_mode.currentData()
        if roi_mode=='fixed' and self.roi is None:
            return QMessageBox.warning(self,'Thiếu ROI','Hãy mở một ảnh chuẩn và khoanh vùng DATE trước khi ARM camera.')
        if mode=='hikrobot':
            source=HikrobotMvsSource(self.camera_serial.text(),self.trigger_line.currentText())
        else:
            source=TriggeredImageSource(capacity=1)
        self.camera_source=source;self.camera_frames=0;self.rows=[];self.history.setRowCount(0)
        self.save_operator_settings();self.clear_result()
        self.banner.setText('ĐANG NẠP OCR & ARM CAMERA…')
        self.camera_worker.source=source;self.camera_worker.expected=expected
        self.camera_worker.roi=self.roi;self.camera_worker.roi_mode=roi_mode
        self.camera_worker.timeout_ms=int(self.config.get('hardware',{}).get('frame_timeout_ms',100))
        self.source_mode.setEnabled(False);self.connect_camera_button.setEnabled(False)
        self.camera_serial.setEnabled(False);self.trigger_line.setEnabled(False)
        self.camera_worker.start(QThread.Priority.TimeCriticalPriority)
    def toggle_live_view(self):
        if self.live_worker.isRunning():
            self.live_worker.requestInterruption();self.live_button.setEnabled(False)
            self.show_status('Đang dừng CAMERA LIVE và khôi phục trigger cứng…')
            return
        if self.worker.isRunning() or self.camera_worker.isRunning():return
        if self.source_mode.currentData()!='hikrobot':
            return QMessageBox.information(self,'CAMERA LIVE','Hãy chọn Camera Hikrobot · trigger ngoài.')
        source=HikrobotMvsSource(self.camera_serial.text(),self.trigger_line.currentText())
        self.live_source=source;self.live_frames=0;self.save_operator_settings()
        self.live_worker.source=source
        self.source_mode.setEnabled(False);self.camera_serial.setEnabled(False);self.trigger_line.setEnabled(False)
        self.connect_camera_button.setEnabled(False);self.disconnect_camera_button.setEnabled(False)
        self.test_trigger_button.setEnabled(False);self.live_button.setText('ĐANG MỞ LIVE…');self.live_button.setEnabled(False)
        self.banner.setText('CAMERA LIVE  ·  CHỈNH LENS')
        self.banner.setStyleSheet('font-size:22px;font-weight:bold;padding:10px;background:#dceeff;color:#075d91')
        self.debug_text.clear()
        for view in self.views.values():view.set_image(None)
        self.details.setHtml('<p><b>CAMERA LIVE không chạy OCR.</b></p><p>Xoay vòng lấy nét đến khi chỉ số <b>Độ nét tương đối</b> đạt giá trị lớn nhất. Chỉnh ánh sáng/khẩu độ để độ sáng không quá tối hoặc cháy trắng.</p>')
        self.live_worker.start(QThread.Priority.HighPriority)
    def live_armed(self,info):
        if info.get('ready'):
            self.camera_state.setText('CAMERA LIVE')
            self.camera_state.setStyleSheet('font-weight:800;color:#0874a8')
            self.live_button.setText('DỪNG LIVE');self.live_button.setEnabled(True)
    def got_live_frame(self,image,metadata):
        self.live_frames+=1;self.original.set_image(image,None)
        h,w=image.shape[:2];sample=image[::4,::4]
        gray=cv2.cvtColor(sample,cv2.COLOR_BGR2GRAY) if sample.ndim==3 else sample
        brightness=float(gray.mean());sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
        self.path_label.setText(f'CAMERA LIVE • {w} × {h} • Frame {self.live_frames}')
        self.details.setHtml(f'<p><b>Độ nét tương đối: {sharpness:.1f}</b> &nbsp; | &nbsp; Độ sáng: {brightness:.1f}/255</p><p>Xoay lens để độ nét tăng cao nhất. Bấm <b>DỪNG LIVE</b> trước khi chạy trigger sản xuất.</p>')
        self.show_status(f'CAMERA LIVE • {w}×{h} • Độ nét {sharpness:.1f} • Độ sáng {brightness:.1f}/255')
    def live_failed(self,text):
        last=text.strip().splitlines()[-1] if text.strip() else 'Lỗi CAMERA LIVE không xác định'
        self.banner.setText('LỖI CAMERA LIVE / SDK')
        self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:#ffe0df;color:#a72525')
        self.details.setPlainText(text);self.show_status(last)
    def live_finished(self):
        self.source_mode.setEnabled(True);self.live_source=None
        self.original.set_image(self.image,self.roi)
        self.path_label.setText('Ảnh đang mở: '+self.path.name if self.path else 'Chọn ảnh để bắt đầu')
        self.source_changed()
        if self.live_frames:self.show_status(f'Đã dừng CAMERA LIVE • {self.live_frames} frame xem trước • Đã khôi phục {self.trigger_line.currentText()}')
        if self.closing:self.close()
    def disconnect_camera(self):
        if self.camera_worker.isRunning():
            self.camera_worker.requestInterruption();self.disconnect_camera_button.setEnabled(False)
            self.show_status('Đang ngắt camera sau chu kỳ chờ frame hiện tại…')
    def test_camera_trigger(self):
        if not self.camera_worker.isRunning():return
        try:
            if isinstance(self.camera_source,TriggeredImageSource):
                if self.image is None:return self.show_status('Chưa có ảnh mẫu để mô phỏng trigger.')
                packet=self.camera_source.trigger(self.image,self.path.name if self.path else 'current')
                self.show_status(f'Đã phát trigger thử #{packet.frame_id} • đang xử lý')
            elif isinstance(self.camera_source,HikrobotMvsSource):
                self.test_trigger_button.setEnabled(False)
                self.camera_source.software_trigger_once()
                self.show_status(f'Đã phát 1 software trigger test • camera đã trở về {self.camera_source.trigger_source}')
        except FrameSourceError as exc:
            self.banner.setText('KHÔNG ĐẠT  ·  FAIL_BUSY')
            self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:#ffe0df;color:#a72525')
            self.show_status(str(exc))
            self.test_trigger_button.setEnabled(True)
    def simulate_trigger(self):
        self.test_camera_trigger()
    def camera_armed(self,info):
        ready=bool(info.get('ready'))
        if ready:
            self.camera_state.setText('ĐÃ ARM • CHỜ TRIGGER')
            self.camera_state.setStyleSheet('font-weight:800;color:#087448')
            self.disconnect_camera_button.setEnabled(True)
            self.test_trigger_button.setEnabled(isinstance(self.camera_source,(TriggeredImageSource,HikrobotMvsSource)))
            self.banner.setText('SẴN SÀNG  ·  CHỜ TRIGGER NGOÀI')
            self.banner.setStyleSheet('font-size:22px;font-weight:bold;padding:10px;background:#d7f3e4;color:#11663f')
        else:
            self.camera_state.setText('ĐÃ NGẮT')
            self.camera_state.setStyleSheet('font-weight:700;color:#596b7a')
    def camera_failed(self,text):
        last=text.strip().splitlines()[-1] if text.strip() else 'Lỗi camera không xác định'
        self.banner.setText('LỖI CAMERA / SDK')
        self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:#ffe0df;color:#a72525')
        self.details.setPlainText(text);self.show_status(last)
    def camera_finished(self):
        self.source_mode.setEnabled(True);self.camera_source=None
        self.disconnect_camera_button.setEnabled(False);self.source_changed()
        if self.camera_frames:
            passes=sum(r['validation']['result']=='PASS' for r,f in self.rows)
            self.show_status(f'Đã ngắt camera · {self.camera_frames} frame · {passes} đạt · {self.camera_frames-passes} lỗi')
        elif not self.details.toPlainText():self.show_status('Camera đã ngắt.')
        if self.closing:self.close()
    def got_result(self,report,folder):
        self.append_result(report,folder);self.show_record(report,folder);self.latest_report=report
    def got_camera_result(self,report,folder,artifacts):
        self.camera_frames+=1;self.append_result(report,folder)
        self.show_record(report,folder,artifacts);self.latest_report=report
        self.test_trigger_button.setEnabled(True)
        self.show_status(f'CAMERA ĐÃ ARM • Frame {self.camera_frames} • {report["validation"]["result"]} • {report.get("processing_ms",0):.1f} ms')
    def append_result(self,report,folder):
        self.rows.append((report,folder));row=self.history.rowCount();self.history.insertRow(row)
        v=report['validation'];p=v.get('parsed') or {};conf=v.get('overall_confidence')
        alignment=report.get('alignment') or {};deviation=alignment.get('deviation')
        date_text=f"{p['day']:02d}/{p['month']:02d}/{p['year']}" if p else '—'
        values=[report['image'],date_text,p.get('dm','—'),p.get('ab','—'),p.get('time','—'),f'{conf:.2%}' if conf is not None else '—',f'{deviation:.2f}°' if deviation is not None else '—',f'{report.get("processing_ms",0):.1f} ms','ĐẠT' if v['result']=='PASS' else 'LỖI']
        for col,value in enumerate(values):self.history.setItem(row,col,QTableWidgetItem(str(value)))
        self.history.item(row,8).setForeground(QColor('#117449' if v['result']=='PASS' else '#b32b30'))
    def show_record(self,report,folder,artifacts=None):
        v=report['validation'];ok=v['result']=='PASS';self.banner.setText('ĐẠT  ·  PASS' if ok else 'KHÔNG ĐẠT  ·  '+v['result'])
        self.banner.setStyleSheet('font-size:24px;font-weight:bold;padding:10px;background:'+('#d7f3e4;color:#11663f' if ok else '#ffe0df;color:#a72525'))
        camera=bool(report.get('hardware'))
        if camera:
            self.path_label.setText(f'Frame camera: {report["image"]}');self.path_label.setToolTip(report['source'])
        else:
            self.path=Path(report['source']);self.path_label.setText('Ảnh đang xem: '+self.path.name);self.path_label.setToolTip(str(self.path))
        if artifacts and artifacts.get('original') is not None:
            self.image=artifacts['original']
            bbox=report.get('bbox');display_roi=FixedROI(*bbox,self.image.shape[1],self.image.shape[0]) if bbox else self.roi
            self.original.set_image(self.image,display_roi)
        elif (folder/'original.png').exists():
            self.image=load_image(folder/'original.png')
            bbox=report.get('bbox');self.roi=FixedROI(*bbox,self.image.shape[1],self.image.shape[0]) if bbox else None
            self.original.set_image(self.image,self.roi);self.update_roi()
        for name,view in self.views.items():
            if artifacts is not None:view.set_image(artifacts.get(name))
            else:view.set_image(load_image(folder/(name+'.png')) if (folder/(name+'.png')).exists() else None)
        lines=[v.get('raw',''),'',f"Kết quả: {v['result']} | {report['processing_ms']} ms",f"Lý do: {v['error_reason']}",f"Confidence: {v.get('confidence',{})}",f"Expected: {report.get('expected',{})}"]
        if report.get('auto_roi'):lines.append('AUTO ROI: '+str(report['auto_roi']))
        if report.get('alignment'):lines.append('ANGLE: '+str(report['alignment']))
        for c in report.get('candidates',[]):lines.extend(['',c['name']+': '+c['raw'].replace('\n',' / ')])
        self.debug_text.setPlainText('\n'.join(lines))
        p=v.get('parsed') or {};conf=v.get('overall_confidence');expected=report.get('expected',{})
        labels={'FAIL_DM':'Mã DM khác với thiết lập.','FAIL_AB':'Mã AB khác với thiết lập.',
                'FAIL_DATE':'Ngày in không hợp lệ.','FAIL_TIME':'Giờ in không hợp lệ.',
                'FAIL_DATE_MISMATCH':'Ngày sản xuất khác ngày đã chọn.',
                'FAIL_DATE_ALIGNMENT':'DATE bị lệch quá giới hạn so với dải tối phía dưới.',
                'FAIL_REFERENCE_LINE_NOT_FOUND':'Không tìm thấy mép dải tối phía dưới để so sánh góc.',
                'FAIL_FRAME_GAP':'Thiếu frame trigger theo số thứ tự camera. Dừng máy và kiểm tra tốc độ/cáp/buffer.',
                'FAIL_BUSY':'Trigger đến khi frame trước còn chờ xử lý.',
                'FAIL_OCR_LOW_CONFIDENCE':'Chữ chưa đủ rõ để xác nhận. Hãy kiểm tra ảnh và vùng DATE.',
                'FAIL_DEADLINE':'Kết quả đến trễ hơn giới hạn 100 ms.',
                'FAIL_ROI_NOT_FOUND':'Chưa xác định được đủ hai dòng DATE. Hãy khoanh lại vùng.',
                'FAIL_FORMAT':'Chữ đọc được không đúng cấu trúc yêu cầu.',
                'FAIL_OCR':'Không đọc chắc chắn. Xem ảnh hoặc chi tiết OCR.'}
        day=f"{p['day']:02d}/{p['month']:02d}/{p['year']}" if p else '—'
        alignment=report.get('alignment') or {};deviation=alignment.get('deviation')
        angle_text=(f'{deviation:.2f}° / {alignment.get("maximum_deviation",self.max_angle.value()):.2f}°'
                    if deviation is not None else '—')
        fields=[('NGÀY SẢN XUẤT',day),('DM',p.get('dm','—')),('AB',p.get('ab','—')),('GIỜ IN',p.get('time','—')),('ĐỘ LỆCH',angle_text)]
        cells=''.join(f'<td><span style="color:#60798b;font-size:10px">{key}</span><br><span style="font-size:19px;font-weight:600">{html.escape(str(value))}</span></td>' for key,value in fields)
        message='Khớp các điều kiện kiểm tra.' if ok else labels.get(v['result'],'Xem chi tiết OCR để kiểm tra lỗi.')
        confidence_text=f'{conf:.2%}' if conf is not None else '—'
        elapsed=report.get('processing_ms',0);timing_color='#117449' if report.get('deadline_met') else '#b32b30'
        device_text='GPU CUDA' if str(report.get('ocr_device','')).startswith('cuda') else 'CPU'
        auto=report.get('auto_roi');auto_text=(f' &nbsp; | &nbsp; AUTO ROI: <b>{auto["confidence"]:.1%}</b>' if auto and auto.get('found') else '')
        self.details.setHtml(f'<table width="100%" cellpadding="3"><tr>{cells}</tr></table>'
            f'<p style="color:#496778">Yêu cầu: NSX <b>{self.date.date().toString("dd/MM/yyyy")}</b> · AB <b>{html.escape(str(expected.get("ab","—")))}</b> · DM <b>{expected.get("dm","—")}</b>'
            f' &nbsp; | &nbsp; Độ tin cậy: <b>{confidence_text}</b>{auto_text}</p>'
            f'<p style="color:{timing_color}">Thời gian quyết định: <b>{elapsed:.1f} ms</b> / mục tiêu ≤ 100 ms · OCR: <b>{device_text}</b></p>'
            f'<p style="color:{"#117449" if ok else "#b32b30"}">{message}</p>')
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
    def closeEvent(self,event):
        self.save_operator_settings()
        if self.worker.isRunning() or self.camera_worker.isRunning() or self.live_worker.isRunning():
            self.closing=True
            if self.worker.isRunning():self.worker.requestInterruption()
            if self.camera_worker.isRunning():self.camera_worker.requestInterruption()
            if self.live_worker.isRunning():self.live_worker.requestInterruption()
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
        window.ab.setText('12');window.dm.setCurrentText('1');window.threshold.setValue(.70)
        window.max_angle.setValue(3.0);window.date.setDate(QDate(2026,8,22))
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
