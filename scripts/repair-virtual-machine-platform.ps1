# Two-stage VirtualMachinePlatform reinstall. Keeps payload and WSL/Docker data.
# Run Disable, restart if Windows requires it, then Enable and restart if required.
param([Parameter(Mandatory=$true)][ValidateSet('Disable','Enable')][string]$Phase)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator required.' }
if (Get-Process dism,sfc -ErrorAction SilentlyContinue) { throw 'Another DISM/SFC operation is running.' }
$reportRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.cache'
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
$statePath = Join-Path $reportRoot 'virtual-machine-platform-repair.json'
$bootTime = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
Start-Transcript -Path (Join-Path $reportRoot ('virtual-machine-platform-' + $Phase.ToLowerInvariant() + '.txt')) -Force
try {
    $feature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
    $feature | Format-List FeatureName,State
    if ($Phase -eq 'Disable') {
        if ($feature.State -ne 'Enabled') { throw 'Expected Enabled. Review current/pending feature state before proceeding.' }
        # Deliberately no -Remove: retain the repaired component payload.
        $result = Disable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
    } else {
        if (-not (Test-Path $statePath)) { throw 'Disable-stage record missing. Review state before enabling.' }
        $previous = Get-Content $statePath -Raw | ConvertFrom-Json
        if ($previous.phase -ne 'Disable') { throw 'Expected a completed disable-stage record.' }
        if ($previous.restart_needed -and $previous.boot_time -eq $bootTime) { throw 'Restart Windows after the disable stage before enabling.' }
        if ($feature.State -ne 'Disabled') { throw 'Expected Disabled after restart. Review pending feature state.' }
        $result = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
    }
    $result | Format-List Online,RestartNeeded
    $after = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
    $after | Format-List FeatureName,State
    [ordered]@{phase=$Phase;boot_time=$bootTime;finished_at=(Get-Date).ToUniversalTime().ToString('o');state=[string]$after.State;restart_needed=[bool]$result.RestartNeeded} | ConvertTo-Json | Set-Content -Path $statePath -Encoding UTF8
    Write-Output 'Stage completed. Review RestartNeeded and feature state before proceeding. No automatic reboot was issued.'
} finally { Stop-Transcript }
