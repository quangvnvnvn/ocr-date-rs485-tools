param(
    [switch]$SkipPortableBuild,
    [string]$Compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$BuildDate = '2026-11-21',
    [ValidatePattern('^\d+\.\d+\.\d+\.\d+$')]
    [string]$ReleaseVersion = '2026.11.21.1',
    [string]$DriverInstaller
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not (Test-Path -LiteralPath $Compiler)) { throw 'Install Inno Setup 6 or pass -Compiler with the ISCC.exe path.' }
$portableOutput = Join-Path $projectRoot "release_build_lite\$BuildDate"
if (-not $SkipPortableBuild) {
    & (Join-Path $projectRoot 'build_portable.ps1') -OutputRoot $portableOutput -BuildDate $BuildDate
}
$payload = Join-Path $portableOutput 'package\OCR_DATE_CPU_Lite'
foreach ($required in @('OCR_DATE.exe','_internal\python312.dll','config\config.json',
                       'data\models\english_g2_recognizer.onnx','data\models\en_PP-OCRv5_rec_mobile.onnx')) {
    if (-not (Test-Path -LiteralPath (Join-Path $payload $required))) { throw "Missing payload: $required" }
}
$output = Join-Path $projectRoot "release_installer\$BuildDate"
$delivery = Join-Path $output 'delivery'
New-Item -ItemType Directory -Path $delivery -Force | Out-Null
$config = Get-Content -LiteralPath (Join-Path $payload 'config\config.json') -Raw | ConvertFrom-Json
$config.PSObject.Properties.Remove('last_image')
$config.PSObject.Properties.Remove('training_directory')
$config.hardware.source_mode = 'static'
$config | Add-Member -NotePropertyName operator_tab -NotePropertyValue 1 -Force
$config.hardware.serial_number = ''
$config.ejector.enabled = $false
$config.ejector.port = ''
$config.ejector.decision_delay_ms = 0
$initialConfig = Join-Path $output 'initial_config.json'
[IO.File]::WriteAllText($initialConfig, ($config | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
$script = Join-Path $projectRoot 'installer\OCR_DATE.iss'
& $Compiler /Qp "/DPayloadDir=$payload" "/DInitialConfig=$initialConfig" "/DReleaseVersion=$ReleaseVersion" "/DBuildDate=$BuildDate" "/O$delivery" $script
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed: $LASTEXITCODE" }
Copy-Item -LiteralPath (Join-Path $projectRoot 'installer\HUONG_DAN_CAI_DAT.txt') -Destination $delivery -Force
$setup = Join-Path $delivery "OCR_DATE_Setup_$BuildDate.exe"
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
$archiveItems = @($setup, (Join-Path $delivery 'HUONG_DAN_CAI_DAT.txt'))
$checksums = @("$hash  $(Split-Path -Leaf $setup)")
$zip = Join-Path $output "OCR_DATE_BAN_CAI_DAT_Windows_64bit_$BuildDate.zip"
if ($DriverInstaller) {
    $driver = Get-Item -LiteralPath $DriverInstaller
    $signature = Get-AuthenticodeSignature -LiteralPath $driver.FullName
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Hikrobot') {
        throw 'The camera driver must have a valid Hikrobot digital signature.'
    }
    $drivers = Join-Path $delivery 'Driver_Hikrobot'
    New-Item -ItemType Directory -Path $drivers -Force | Out-Null
    Copy-Item -LiteralPath $driver.FullName -Destination (Join-Path $drivers $driver.Name) -Force
    $checksums += "$((Get-FileHash -LiteralPath (Join-Path $drivers $driver.Name)).Hash)  Driver_Hikrobot\$($driver.Name)"
    $archiveItems += $drivers
    $zip = Join-Path $output "OCR_DATE_Bo_cai_va_Driver_Hikrobot_$BuildDate.zip"
}
[IO.File]::WriteAllText((Join-Path $delivery 'SHA256.txt'), ($checksums -join "`r`n") + "`r`n", [Text.UTF8Encoding]::new($false))
$archiveItems += Join-Path $delivery 'SHA256.txt'
Compress-Archive -LiteralPath $archiveItems -DestinationPath $zip -CompressionLevel NoCompression -Force
Write-Output $zip
