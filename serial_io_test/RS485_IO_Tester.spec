# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/admin/Documents/ChatGPT/ORC vỉ viên treo nhận diện date/serial_io_test/app.py'],
    pathex=['C:/Users/admin/Documents/ChatGPT/ORC vỉ viên treo nhận diện date/serial_io_test/_vendor'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RS485_IO_Tester',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RS485_IO_Tester',
)
