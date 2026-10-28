param(
    [string]$OutputRoot = (Join-Path $PSScriptRoot "release_build_lite")
)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$resolvedOutput = [IO.Path]::GetFullPath($OutputRoot)
if (-not $resolvedOutput.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputRoot must stay inside the project: $projectRoot"
}
if (Test-Path -LiteralPath $resolvedOutput) {
    Remove-Item -LiteralPath $resolvedOutput -Recurse -Force
}
$work = Join-Path $resolvedOutput "work"
$dist = Join-Path $resolvedOutput "dist"
$spec = Join-Path $resolvedOutput "spec"
New-Item -ItemType Directory -Path $work,$dist,$spec -Force | Out-Null

# The portable runtime deliberately contains ONNX Runtime CPU and headless
# OpenCV only. PyTorch, EasyOCR, CUDA and the ONNX exporter are development
# dependencies and must not enter the package for the 8 GB target machine.
$vendor = Join-Path $projectRoot "packaging_onnx"
$ortDll = Join-Path $vendor "onnxruntime\capi\onnxruntime.dll"
$headlessCv = Join-Path $vendor "cv2\cv2.pyd"
if (-not (Test-Path -LiteralPath $ortDll)) {
    throw "Missing ONNX Runtime packaging files at $vendor"
}
if (-not (Test-Path -LiteralPath $headlessCv)) {
    throw "Missing headless OpenCV packaging files at $vendor"
}

$excludeArgs = @()
foreach ($module in @(
    'torch','torchvision','easyocr','PIL','onnx','ml_dtypes','scipy','skimage',
    'imageio','shapely','sympy','altair','IPython','jedi','jupyter','matplotlib',
    'nbformat','openai','openpyxl','pandas','polars','pyarrow','sklearn',
    'tensorflow','tokenizers','transformers','win32com','zmq',
    'PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick',
    'PySide6.QtPdf','PySide6.QtPdfWidgets','PySide6.QtQuick','PySide6.QtQml',
    'PySide6.QtMultimedia','PySide6.QtMultimediaWidgets','PySide6.Qt3DCore'
)) {
    $excludeArgs += '--exclude-module'
    $excludeArgs += $module
}

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $vendor + [IO.Path]::PathSeparator + $oldPythonPath
    python -m PyInstaller --noconfirm --clean --windowed --name OCR_DATE `
        --distpath $dist --workpath $work --specpath $spec `
        --paths $vendor `
        --hidden-import onnxruntime.capi._pybind_state `
        --collect-binaries onnxruntime `
        @excludeArgs `
        (Join-Path $projectRoot "gui.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with code $LASTEXITCODE" }
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

$generatedApp = Join-Path $dist "OCR_DATE"
# The host PATH contains unrelated Poppler ICU 78 DLLs. PyInstaller can pick
# them up while resolving optional DLLs; if shipped beside Qt they shadow Qt's
# own runtime and QtCore fails with "procedure could not be found". The other
# entries are optional video/QML/PDF/OpenGL fallback components unused by this
# fixed image + Qt Widgets application. Each removal is covered by the clean
# extraction smoke test after packaging.
$pruneRelative = @(
    '_internal\icuuc.dll',
    '_internal\icudt78.dll',
    '_internal\cv2\opencv_videoio_ffmpeg4140_64.dll',
    '_internal\libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll',
    '_internal\PySide6\opengl32sw.dll',
    '_internal\PySide6\Qt6Quick.dll',
    '_internal\PySide6\Qt6Qml.dll',
    '_internal\PySide6\Qt6QmlMeta.dll',
    '_internal\PySide6\Qt6QmlModels.dll',
    '_internal\PySide6\Qt6QmlWorkerScript.dll',
    '_internal\PySide6\Qt6Pdf.dll',
    '_internal\PySide6\Qt6VirtualKeyboard.dll',
    '_internal\PySide6\plugins\imageformats\qpdf.dll',
    '_internal\PySide6\plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll'
)
foreach ($relative in $pruneRelative) {
    $target = Join-Path $generatedApp $relative
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Force
    }
}
$packageRoot = Join-Path $resolvedOutput "package"
$app = Join-Path $packageRoot "OCR_DATE_CPU_Lite"
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
Copy-Item -LiteralPath $generatedApp -Destination $app -Recurse
New-Item -ItemType Directory -Path `
    (Join-Path $app "config"), `
    (Join-Path $app "data\calibration"), `
    (Join-Path $app "data\models"), `
    (Join-Path $app "data\test_images"), `
    (Join-Path $app "data\debug"), `
    (Join-Path $app "data\logs") -Force | Out-Null

Copy-Item -LiteralPath `
    (Join-Path $projectRoot "config\config.json"), `
    (Join-Path $projectRoot "config\sample_rois.json") `
    -Destination (Join-Path $app "config")
Copy-Item -LiteralPath `
    (Join-Path $projectRoot "data\models\english_g2_recognizer.onnx"), `
    (Join-Path $projectRoot "data\models\english_g2_recognizer.json") `
    -Destination (Join-Path $app "data\models")
Get-ChildItem -LiteralPath (Join-Path $projectRoot "data\test_images") -File |
    Copy-Item -Destination (Join-Path $app "data\test_images")
Get-ChildItem -LiteralPath (Join-Path $projectRoot "data\calibration") -File |
    Copy-Item -Destination (Join-Path $app "data\calibration")
Copy-Item -LiteralPath `
    (Join-Path $projectRoot "README.md"), `
    (Join-Path $projectRoot "HIKROBOT_SETUP.txt") `
    -Destination $app

@"
OCR DATE - CPU LITE - HIKROBOT TRIGGER
======================================

1. Giai nen TOAN BO file ZIP vao o dia may dich.
2. Cai HIKROBOT MVS 64-bit neu dung camera that.
3. Mo thu muc OCR_DATE_CPU_Lite va bam dup OCR_DATE.exe.
4. Dung CAMERA LIVE de chinh lens, exposure va den.
5. Dung TRIGGER TEST (SOFTWARE) de thu mot anh; khi san xuat dung Line0/Line1.

Ban CPU Lite khong can Python, Internet, GPU roi, CUDA hay PyTorch. ONNX Runtime
CPU dung 4 luong mac dinh, phu hop muc tieu Core i5 Gen 8 / RAM 8 GB. Nut LUU CAU
HINH luu serial camera, Line0/Line1, ROI, AB, DM, ngay NSX, confidence va gioi han
lech goc de lan mo sau khoi phuc giong lan truoc.

Can benchmark lai 100 ms tren dung may dich, camera, resolution, exposure va chu ky
trigger truoc khi dua vao day chuyen. Log: data\logs. Anh/report: data\debug.
"@ | Set-Content -LiteralPath (Join-Path $app "HUONG_DAN_SU_DUNG.txt") -Encoding UTF8

@"
OCR DATE PORTABLE - CPU LITE - HIKROBOT HARDWARE TRIGGER
Build: 2026-10-01
Windows 10/11 64-bit; Python 3.12 runtime bundled
ONNX Runtime 1.30.0 CPU; OpenCV headless 4.14.0; PySide6 6.11.2
No PyTorch, EasyOCR runtime, CUDA or discrete GPU required.
HIKROBOT MVS 64-bit is required only for a real camera and is not bundled.
"@ | Set-Content -LiteralPath (Join-Path $app "PHIEN_BAN.txt") -Encoding UTF8

$zip = Join-Path $resolvedOutput "OCR_DATE_HIKROBOT_CPU_Lite_Windows_Portable_2026-10-01.zip"
Compress-Archive -LiteralPath $app -DestinationPath $zip -CompressionLevel Optimal
Write-Output $zip
