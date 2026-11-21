# Repairs the Windows component store, then protected system files. No automatic reboot.
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator required.' }
if (Get-Process dism,sfc -ErrorAction SilentlyContinue) { throw 'Another DISM/SFC process is already running.' }
$reportRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.cache'
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
Start-Transcript -Path (Join-Path $reportRoot 'windows-component-repair.txt') -Force
try {
    Get-Date
    & "$env:WINDIR\System32\dism.exe" /Online /Cleanup-Image /RestoreHealth
    $repairExit = $LASTEXITCODE
    Write-Output ('DISM RestoreHealth exit code: ' + $repairExit)
    if ($repairExit -notin @(0,3010)) { throw ('DISM failed: ' + $repairExit + '. SFC was not started.') }
    & "$env:WINDIR\System32\sfc.exe" /scannow
    Write-Output ('SFC exit code: ' + $LASTEXITCODE)
    Get-Service vmcompute,hns,WslService -ErrorAction SilentlyContinue | Format-List Name,Status,StartType
    Write-Output ('vmcompute.exe present: ' + (Test-Path "$env:WINDIR\System32\vmcompute.exe"))
    Write-Output 'Repair commands completed. Review their output before restarting Windows.'
} finally { Stop-Transcript }
