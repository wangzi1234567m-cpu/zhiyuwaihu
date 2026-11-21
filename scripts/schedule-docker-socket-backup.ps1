# Preserve the exact stale Secrets Engine directory through a boot-time rename.
# Does not delete anything, replace an existing destination, or reboot Windows.
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator required.' }
$reportRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.cache'
$planPath = Join-Path $reportRoot 'docker-socket-boot-backup.json'
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
Start-Transcript -Path (Join-Path $reportRoot 'docker-socket-boot-backup.txt') -Force
try {
    $source = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'docker-secrets-engine'))
    $expected = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'AppData\Local\docker-secrets-engine'))
    if ($source -ne $expected) { throw 'Unexpected source path.' }
    $directory = Get-Item -LiteralPath $source -Force
    if (-not $directory.PSIsContainer -or ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Expected an ordinary directory.' }
    $children = @(Get-ChildItem -LiteralPath $source -Force)
    if ($children.Count -ne 1 -or $children[0].Name -ne 'engine.sock' -or $children[0].Length -ne 0 -or -not ($children[0].Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Directory does not contain only the known zero-byte stale socket. Review manually.' }
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
    if (Test-Path $planPath) {
        $previous = Get-Content $planPath -Raw | ConvertFrom-Json
        if ($previous.boot_time -eq $boot) { throw 'A boot-time backup was already queued during this boot. Do not queue another.' }
    }
    $destination = Join-Path (Split-Path $source -Parent) ('docker-secrets-engine.boot-backup-' + (Get-Date -Format yyyyMMddHHmmss))
    if ((Split-Path $destination -Parent) -ne (Split-Path $source -Parent) -or (Test-Path -LiteralPath $destination)) { throw 'Invalid backup destination.' }
    $dockerRoot = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop'
    Get-Process 'Docker Desktop','com.docker.backend' -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dockerRoot,[StringComparison]::OrdinalIgnoreCase) } | Stop-Process
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ZhiyuBootRename {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileExW(string source, string destination, uint flags);
}
'@
    # 4 = MOVEFILE_DELAY_UNTIL_REBOOT. A non-null destination preserves the directory.
    if (-not [ZhiyuBootRename]::MoveFileExW($source,$destination,4)) {
        throw (New-Object ComponentModel.Win32Exception([Runtime.InteropServices.Marshal]::GetLastWin32Error()))
    }
    [ordered]@{source=$source;destination=$destination;boot_time=$boot;queued_at=(Get-Date).ToUniversalTime().ToString('o');status='queued_not_verified'} | ConvertTo-Json | Set-Content $planPath -Encoding UTF8
    Write-Output 'Boot-time directory backup queued. This is not proof the rename or Docker startup succeeded. No reboot was issued.'
} finally { Stop-Transcript }
