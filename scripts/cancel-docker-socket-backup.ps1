# Remove only the exact boot-time rename pair previously scheduled by this project.
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator required.' }
$reportRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.cache'
$planPath = Join-Path $reportRoot 'docker-socket-boot-backup.json'
$plan = Get-Content $planPath -Raw | ConvertFrom-Json
$expectedSource = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'AppData\Local\docker-secrets-engine'))
if ($plan.source -ne $expectedSource -or (Split-Path $plan.destination -Parent) -ne (Split-Path $expectedSource -Parent) -or (Split-Path $plan.destination -Leaf) -notmatch '^docker-secrets-engine\.boot-backup-\d{14}$') { throw 'Unexpected backup plan.' }
Start-Transcript -Path (Join-Path $reportRoot 'docker-socket-backup-cancel.txt') -Force
try {
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('SYSTEM\CurrentControlSet\Control\Session Manager',$true)
    try {
        $original = [string[]]$key.GetValue('PendingFileRenameOperations',@())
        if ($original.Count % 2 -ne 0) { throw 'Unexpected pending-operation layout. No changes made.' }
        $retained = New-Object 'System.Collections.Generic.List[string]'
        $removed = 0
        for ($i=0; $i -lt $original.Count; $i+=2) {
            if ($original[$i].EndsWith(('\??\'+$plan.source),[StringComparison]::OrdinalIgnoreCase) -and $original[$i+1].EndsWith(('\??\'+$plan.destination),[StringComparison]::OrdinalIgnoreCase)) { $removed++; continue }
            $retained.Add($original[$i]); $retained.Add($original[$i+1])
        }
        if ($removed -gt 0) {
            ConvertTo-Json -InputObject $original | Set-Content (Join-Path $reportRoot 'pending-renames-before-socket-cancel.json') -Encoding UTF8
            $key.SetValue('PendingFileRenameOperations',$retained.ToArray(),[Microsoft.Win32.RegistryValueKind]::MultiString)
            $after = [string[]]$key.GetValue('PendingFileRenameOperations',@())
            if (($after -join "`0") -cne ($retained.ToArray() -join "`0")) { throw 'Registry readback mismatch. Review saved report.' }
        }
        $plan.status = 'cancelled'
        $plan | Add-Member -NotePropertyName cancelled_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
        $plan | ConvertTo-Json | Set-Content $planPath -Encoding UTF8
        Write-Output ('Exact backup pairs removed: '+$removed+'. Other pending operations preserved: '+($retained.Count/2)+'.')
    } finally { if ($key) { $key.Close() } }
} finally { Stop-Transcript }
