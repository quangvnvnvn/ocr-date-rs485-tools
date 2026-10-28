"""Explicit light palette: readable even when Windows uses dark mode."""
from PySide6.QtGui import QPalette,QColor

STYLE = '''
QWidget { color:#203549; font-family:"Segoe UI"; font-size:13px; }
QMainWindow { background:#eef3f7; }
QLabel { background:transparent; }
QWidget#controls, QWidget#imagePanel, QWidget#resultPanel {
 background:white; border:1px solid #dce5ed; border-radius:10px;
}
QLabel#title { font-size:25px; font-weight:700; color:#123951; }
QLabel#subtitle, QLabel#fileLabel { color:#536d80; }
QLabel#sectionLabel { color:#36566d; font-weight:700; }
QPushButton { color:#23465e; background:#ffffff; border:1px solid #bfceda;
 border-radius:6px; padding:8px 13px; min-height:20px; font-weight:600; }
QPushButton:hover { background:#eaf3fa; border-color:#5b97ba; }
QPushButton:pressed { background:#dbeaf4; }
QPushButton:disabled { color:#8494a1; background:#edf1f4; border-color:#dce3e9; }
QPushButton#primary { background:#127454; color:white; border-color:#127454; padding:9px 24px; }
QPushButton#primary:hover { background:#0a6245; }
QPushButton#primary:disabled { background:#a4c8bc; border-color:#a4c8bc; color:#f6fffa; }
QLineEdit,QComboBox,QDoubleSpinBox,QDateEdit { color:#203549; background:white;
 border:1px solid #bfceda; border-radius:5px; padding:5px 8px; min-height:20px;
 selection-background-color:#d8ebf8; selection-color:#183d57; }
QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus,QDateEdit:focus {border:1px solid #2387b4;}
QLineEdit:disabled,QComboBox:disabled,QDoubleSpinBox:disabled,QDateEdit:disabled {color:#8193a0;background:#f1f4f7;}
QComboBox QAbstractItemView {background:white;color:#203549;selection-background-color:#d8ebf8;selection-color:#183d57;}
QCheckBox {spacing:7px;color:#36566d;}
QTextEdit { background:#ffffff; color:#203549; border:1px solid #dce5ed; border-radius:6px; padding:7px; }
QTabWidget::pane {border:1px solid #dce5ed; background:#f6f9fb;}
QTabBar::tab {background:#edf3f7;color:#4d687b;padding:7px 11px;border:1px solid #dce5ed;}
QTabBar::tab:selected {background:#dceef8;color:#124e70;font-weight:600;border-bottom:2px solid #268ab4;}
QTableWidget {background:white;alternate-background-color:#f5f8fb;color:#203549;
 gridline-color:#e6edf2;border:1px solid #dce5ed;border-radius:6px;selection-background-color:#dcedf8;selection-color:#163e58;}
QHeaderView::section {background:#e9f0f6;color:#36566d;padding:8px;border:0;border-bottom:1px solid #dce5ed;font-weight:600;}
QTableCornerButton::section {background:#e9f0f6;border:0;}
QSplitter::handle {background:#eef3f7;}
QScrollBar:vertical {background:#f1f5f8;width:10px;}
QScrollBar::handle:vertical {background:#b8cbd8;min-height:24px;border-radius:4px;}
QToolTip {background:#173e56;color:white;border:0;padding:6px;}
QCalendarWidget QWidget {background:white;color:#203549;}
QCalendarWidget QToolButton {background:#e7f0f6;color:#203549;}
QCalendarWidget QAbstractItemView {background:white;color:#203549;selection-background-color:#d8ebf8;selection-color:#173e56;}
'''

def apply_theme(app):
    app.setStyle('Fusion')
    palette=QPalette()
    colors={'Window':'#eef3f7','WindowText':'#203549','Base':'#ffffff',
            'AlternateBase':'#f5f8fb','ToolTipBase':'#173e56','ToolTipText':'#ffffff',
            'Text':'#203549','Button':'#ffffff','ButtonText':'#23465e',
            'BrightText':'#ffffff','Highlight':'#d8ebf8','HighlightedText':'#173e56',
            'Link':'#177da7','PlaceholderText':'#7c919f'}
    for role,color in colors.items():palette.setColor(getattr(QPalette.ColorRole,role),QColor(color))
    for role in ('Text','WindowText','ButtonText'):
        palette.setColor(QPalette.Disabled,getattr(QPalette.ColorRole,role),QColor('#8193a0'))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
