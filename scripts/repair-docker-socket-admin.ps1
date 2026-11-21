# Run the scoped directory-backup workaround in an elevated interactive-user process.
$ErrorActionPreference = 'Stop'
$reportRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.cache'
Start-Transcript -Path (Join-Path $reportRoot 'docker-socket-repair.txt') -Force
try {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator required.' }
    & (Join-Path $PSScriptRoot 'repair-docker-socket.ps1')
} finally { Stop-Transcript }
