# Read-only diagnostics. Writes a report in the ignored workspace .cache directory.
$ErrorActionPreference = 'Continue'
$reportRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.cache'
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
$reportPath = Join-Path $reportRoot 'windows-admin-diagnostic.txt'
Start-Transcript -Path $reportPath -Force
try {
    Get-Date
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    Write-Output ('Administrator: ' + $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
    Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform | Format-List FeatureName,State,RestartRequired
    Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux | Format-List FeatureName,State,RestartRequired
    Get-Service vmcompute,hns,WslService -ErrorAction Continue | Format-List Name,Status,StartType
    & "$env:WINDIR\System32\bcdedit.exe" /enum
    & "$env:WINDIR\System32\dism.exe" /Online /Cleanup-Image /CheckHealth
    Write-Output ('DISM CheckHealth exit code: ' + $LASTEXITCODE)
    Get-ChildItem "$env:WINDIR\WinSxS" -Filter '*hyperv-compute-host-service_*' -Directory | ForEach-Object {
        Get-ChildItem -LiteralPath $_.FullName -Filter 'vmcompute.exe' | Select-Object FullName,Length
    }
    Write-Output ('CBS reboot pending: ' + (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'))
} finally {
    Stop-Transcript
}
