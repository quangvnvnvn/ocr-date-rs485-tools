$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
$Python = (Get-Command python).Source
& $Python -m pip install --target (Join-Path $Here '_vendor') 'pyserial>=3.5,<4'
if ($LASTEXITCODE -ne 0) { throw 'pyserial install failed.' }
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name 'RS485_IO_Tester' --distpath (Join-Path $Here 'dist') --workpath (Join-Path $Here 'build') --specpath $Here --paths (Join-Path $Here '_vendor') (Join-Path $Here 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
$appDir = Join-Path $Here 'dist\RS485_IO_Tester'
# Remove unrelated host ICU and optional Qt modules that can shadow QtCore at runtime.
$prune = @(
    '_internal\icuuc.dll', '_internal\icudt78.dll',
    '_internal\PySide6\opengl32sw.dll',
    '_internal\PySide6\Qt6Quick.dll', '_internal\PySide6\Qt6Qml.dll',
    '_internal\PySide6\Qt6QmlMeta.dll', '_internal\PySide6\Qt6QmlModels.dll',
    '_internal\PySide6\Qt6QmlWorkerScript.dll', '_internal\PySide6\Qt6Pdf.dll',
    '_internal\PySide6\Qt6VirtualKeyboard.dll',
    '_internal\PySide6\plugins\imageformats\qpdf.dll',
    '_internal\PySide6\plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll'
)
foreach ($relative in $prune) {
    $target = Join-Path $appDir $relative
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
}
Write-Host "Build complete: $(Join-Path $appDir 'RS485_IO_Tester.exe')"
