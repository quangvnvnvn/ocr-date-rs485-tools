param()
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$release = Join-Path $project 'release_installer'
$setup = Join-Path $release 'delivery\OCR_DATE_Setup_2026-10-29.exe'
$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'OCR DATE - Hikrobot RS485.lnk'
$registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{3C38704D-89B2-44FB-ABA1-0AF396C43EB8}_is1'
if ((Test-Path -LiteralPath $registry) -or (Test-Path -LiteralPath $shortcut)) {
    throw 'Existing OCR DATE installation/shortcut detected; test will not replace it.'
}
$verify = Join-Path $release ('verify-' + [guid]::NewGuid().ToString('N'))
$install = [IO.Path]::GetFullPath((Join-Path $verify 'installed'))
if (-not $install.StartsWith($release + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Test installation must stay inside release_installer'
}
New-Item -ItemType Directory -Path $verify -Force | Out-Null
$common = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOCLOSEAPPLICATIONS','/SP-',
    ('/DIR="' + $install + '"'),'/TASKS=desktopicon')
$oldMarker = $env:OCR_DATE_PORTABLE_SMOKE_MARKER
$oldQt = $env:QT_QPA_PLATFORM
$oldConfig = $env:OCR_DATE_CONFIG
$report = [ordered]@{ install = $false; shortcut = $false; safe_defaults = $false; upgrade_preserves_config = $false }
try {
    $p = Start-Process -FilePath $setup -ArgumentList ($common + ('/LOG="' + (Join-Path $verify 'install.log') + '"')) -PassThru -Wait -WindowStyle Hidden
    if ($p.ExitCode -ne 0) { throw "Installation failed: $($p.ExitCode)" }
    $report.install = $true
    if (-not (Test-Path -LiteralPath $registry)) { throw 'Missing uninstall registration' }
    # WScript.Shell exposes an ANSI target on some Windows builds; Shell.Application
    # reads the Unicode link target, including Vietnamese install paths.
    $shell = New-Object -ComObject Shell.Application
    $link = $shell.NameSpace((Split-Path -Parent $shortcut)).ParseName((Split-Path -Leaf $shortcut)).GetLink
    if ($link.Path -ne (Join-Path $install 'OCR_DATE.exe')) { throw "Incorrect Desktop shortcut: $($link.Path); expected $(Join-Path $install 'OCR_DATE.exe')" }
    $report.shortcut = $true
    $configPath = Join-Path $install 'config\config.json'
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($config.ejector.enabled -or $config.ejector.decision_delay_ms -ne 0 -or $config.ejector.port -ne '' -or
        $config.hardware.serial_number -ne '' -or $config.hardware.source_mode -ne 'static' -or $config.last_image) {
        throw 'Fresh-install defaults contain machine-specific or active I/O settings'
    }
    $report.safe_defaults = $true
    $env:OCR_DATE_CONFIG = $null
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:OCR_DATE_PORTABLE_SMOKE_MARKER = Join-Path $verify 'ocr-smoke.json'
    $p = Start-Process -FilePath (Join-Path $install 'OCR_DATE.exe') -WorkingDirectory $install -PassThru -Wait -WindowStyle Hidden
    if ($p.ExitCode -ne 0) { throw "Installed OCR smoke failed: $($p.ExitCode)" }
    $smoke = Get-Content -LiteralPath $env:OCR_DATE_PORTABLE_SMOKE_MARKER -Raw | ConvertFrom-Json
    if ($smoke.result -ne 'PASS' -or -not $smoke.deadline_met) { throw 'OCR smoke did not pass' }
    $report.ocr = $smoke
    $config.ejector.decision_delay_ms = 525
    [IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    $before = (Get-FileHash -LiteralPath $configPath).Hash
    $retainedLog = Join-Path $install 'data\logs\installer-retention-test.txt'
    [IO.File]::WriteAllText($retainedLog, 'Retain operator data on uninstall')
    $p = Start-Process -FilePath $setup -ArgumentList ($common + ('/LOG="' + (Join-Path $verify 'upgrade.log') + '"')) -PassThru -Wait -WindowStyle Hidden
    if ($p.ExitCode -ne 0 -or (Get-FileHash -LiteralPath $configPath).Hash -ne $before) { throw 'Upgrade changed existing config' }
    $report.upgrade_preserves_config = $true
} finally {
    $env:OCR_DATE_PORTABLE_SMOKE_MARKER = $oldMarker
    $env:QT_QPA_PLATFORM = $oldQt
    $env:OCR_DATE_CONFIG = $oldConfig
    $uninstaller = Join-Path $install 'unins000.exe'
    if (Test-Path -LiteralPath $uninstaller) {
        $p = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="' + (Join-Path $verify 'uninstall.log') + '"')) -PassThru -Wait -WindowStyle Hidden
        if ($p.ExitCode -ne 0) { throw "Test uninstall failed: $($p.ExitCode)" }
    }
}
if ((Test-Path -LiteralPath (Join-Path $install 'OCR_DATE.exe')) -or
    (Test-Path -LiteralPath $shortcut) -or (Test-Path -LiteralPath $registry)) { throw 'Uninstall left executable/shortcut/registration' }
if (-not (Test-Path -LiteralPath $configPath) -or -not (Test-Path -LiteralPath $retainedLog)) { throw 'Uninstall removed operator data' }
$report.uninstall_preserves_data = $true
$report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $verify 'verification.json') -Encoding UTF8
$report | ConvertTo-Json -Depth 20
