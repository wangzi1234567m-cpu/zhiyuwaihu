# Only for the exact Secrets Engine stale-socket error. Keeps original directory intact.
# Run in an INTERACTIVE administrator PowerShell; Codex's process can hit bind-filter limits.
$ErrorActionPreference='Stop'
$socketDirectory=Join-Path $env:LOCALAPPDATA 'docker-secrets-engine'
$resolvedSocketDirectory=[IO.Path]::GetFullPath($socketDirectory)
$expectedSocketDirectory=[IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'AppData/Local/docker-secrets-engine'))
if ($resolvedSocketDirectory -ne $expectedSocketDirectory) { throw 'Unexpected socket directory; refusing repair.' }
if (-not (Test-Path -LiteralPath $resolvedSocketDirectory)) { throw 'Socket directory not found. Do not use this repair for a different error.' }
$installedDocker=Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop'
$desktopExe=Join-Path $installedDocker 'Docker Desktop.exe'
if (-not (Test-Path -LiteralPath $desktopExe)) { throw 'Expected Docker installation not found.' }
Get-Process 'Docker Desktop','com.docker.backend' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($installedDocker,[StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process
$backupName='docker-secrets-engine.saved-'+(Get-Date -Format yyyyMMddHHmmss)
Rename-Item -LiteralPath $resolvedSocketDirectory -NewName $backupName
Write-Output "Backed up socket directory as $backupName. No images, volumes or WSL data were removed."
Start-Process -FilePath $desktopExe -WindowStyle Hidden
