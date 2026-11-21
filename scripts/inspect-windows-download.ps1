# Read-only inspection of Windows Update download tasks. Administrator required for all-user BITS.
$ErrorActionPreference = 'Continue'
$reportRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.cache'
Start-Transcript -Path (Join-Path $reportRoot 'windows-download-diagnostic.txt') -Force
try {
    Get-Date
    Import-Module BitsTransfer
    Get-BitsTransfer -AllUsers | Select-Object DisplayName,JobState,BytesTransferred,BytesTotal,ErrorDescription | Format-List
    Get-DeliveryOptimizationStatus | Select-Object Status,TotalBytesDownloaded,FileSize,BytesFromHttp,Duration | Format-List
    Get-Process dism,DismHost,TiWorker,TrustedInstaller -ErrorAction SilentlyContinue | Select-Object ProcessName,Id,CPU,StartTime | Format-Table
    Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WindowsUpdateClient/Operational';StartTime=(Get-Date).AddMinutes(-20)} -MaxEvents 8 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id,Message | Format-List
} finally { Stop-Transcript }
