param(
    [switch]$SkipPortableBuild,
    [string]$Compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not (Test-Path -LiteralPath $Compiler)) { throw 'Install Inno Setup 6 or pass -Compiler with the ISCC.exe path.' }
if (-not $SkipPortableBuild) { & (Join-Path $projectRoot 'build_portable.ps1') }
$payload = Join-Path $projectRoot 'release_build_lite\package\OCR_DATE_CPU_Lite'
foreach ($required in @('OCR_DATE.exe','_internal\python312.dll','config\config.json','data\models\english_g2_recognizer.onnx')) {
    if (-not (Test-Path -LiteralPath (Join-Path $payload $required))) { throw "Missing payload: $required" }
}
$output = Join-Path $projectRoot 'release_installer'
$delivery = Join-Path $output 'delivery'
New-Item -ItemType Directory -Path $delivery -Force | Out-Null
$config = Get-Content -LiteralPath (Join-Path $payload 'config\config.json') -Raw | ConvertFrom-Json
$config.PSObject.Properties.Remove('last_image')
$config.hardware.source_mode = 'static'
$config | Add-Member -NotePropertyName operator_tab -NotePropertyValue 1 -Force
$config.hardware.serial_number = ''
$config.ejector.enabled = $false
$config.ejector.port = ''
$config.ejector.decision_delay_ms = 0
$initialConfig = Join-Path $output 'initial_config.json'
[IO.File]::WriteAllText($initialConfig, ($config | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
$script = Join-Path $projectRoot 'installer\OCR_DATE.iss'
& $Compiler /Qp "/DPayloadDir=$payload" "/DInitialConfig=$initialConfig" "/O$delivery" $script
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed: $LASTEXITCODE" }
Copy-Item -LiteralPath (Join-Path $projectRoot 'installer\HUONG_DAN_CAI_DAT.txt') -Destination $delivery -Force
$setup = Join-Path $delivery 'OCR_DATE_Setup_2026-10-29.exe'
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
[IO.File]::WriteAllText((Join-Path $delivery 'SHA256.txt'), "$hash  OCR_DATE_Setup_2026-10-29.exe`r`n", [Text.UTF8Encoding]::new($false))
$zip = Join-Path $output 'OCR_DATE_BAN_CAI_DAT_Windows_64bit_2026-10-29.zip'
Compress-Archive -LiteralPath $setup,(Join-Path $delivery 'HUONG_DAN_CAI_DAT.txt'),(Join-Path $delivery 'SHA256.txt') -DestinationPath $zip -CompressionLevel NoCompression -Force
Write-Output $zip
