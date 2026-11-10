$ErrorActionPreference = 'Stop'
$name = 'en_PP-OCRv5_rec_mobile.onnx'
$sha256 = 'c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8'
$url = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/$name"
$modelDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'data\models'
$model = Join-Path $modelDir $name
if ((Test-Path -LiteralPath $model) -and (Get-FileHash -LiteralPath $model).Hash -eq $sha256) {
    Write-Output $model
    return
}
New-Item -ItemType Directory -Path $modelDir -Force | Out-Null
$download = Join-Path $modelDir "$name.$([guid]::NewGuid().ToString('N')).tmp"
try {
    Invoke-WebRequest -Uri $url -OutFile $download -TimeoutSec 90
    if ((Get-FileHash -LiteralPath $download).Hash -ne $sha256) { throw 'PP-OCR model SHA256 mismatch' }
    Move-Item -LiteralPath $download -Destination $model -Force
    Write-Output $model
} finally {
    if (Test-Path -LiteralPath $download) { Remove-Item -LiteralPath $download -Force }
}
