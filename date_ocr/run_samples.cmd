@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py --folder data/test_images --roi-manifest config/sample_rois.json --ab 12 --dm 1
) else (
  python main.py --folder data/test_images --roi-manifest config/sample_rois.json --ab 12 --dm 1
)
pause
