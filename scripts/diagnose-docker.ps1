# Read-only. Run in an interactive administrator PowerShell for complete results.
$ErrorActionPreference='Continue'
Write-Output 'WSL status'
wsl --status
wsl --list --verbose
Write-Output 'Windows optional features (administrator required)'
Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform | Select-Object FeatureName,State
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux | Select-Object FeatureName,State
Get-CimInstance Win32_ComputerSystem | Select-Object HypervisorPresent,Manufacturer,Model
Get-CimInstance Win32_Processor | Select-Object VirtualizationFirmwareEnabled,SecondLevelAddressTranslationExtensions
Get-CimInstance Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform' OR Name='Microsoft-Windows-Subsystem-Linux'" | Select-Object Name,InstallState
Get-Service vmcompute,hns,WslService -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType
sc.exe qc vmcompute
Write-Output ('vmcompute.exe present: '+(Test-Path (Join-Path $env:WINDIR 'System32/vmcompute.exe')))
Write-Output 'Docker engine'
docker version
Write-Output 'Socket directory (no content read)'
Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'docker-secrets-engine') -Force -ErrorAction SilentlyContinue | Select-Object Name,Length,Attributes
