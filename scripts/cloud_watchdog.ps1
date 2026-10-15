param(
    [string]$Root = 'C:\Vigil',
    [string]$HealthUrl = 'http://127.0.0.1:8788/api/health'
)
# Run as SYSTEM once per minute; task MultipleInstances must be IgnoreNew.
# No credentials or model requests are needed for the local health probe.
$ErrorActionPreference = 'Stop'
$stateFile = Join-Path $Root 'data\run\cloud-watchdog.json'
$logFile = Join-Path $Root 'logs\cloud-watchdog.log'
New-Item -ItemType Directory -Force (Split-Path $stateFile), (Split-Path $logFile) | Out-Null
$failures = 0
if (Test-Path -LiteralPath $stateFile) {
    try { $failures = [int](Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json).failures } catch { $failures = 0 }
}
$healthy = $false
try {
    $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 8
    $healthy = $health.ok -eq $true
} catch { $healthy = $false }
if ($healthy) { $failures = 0 } else { $failures++ }
if ($failures -ge 3) {
    $task = Get-ScheduledTask -TaskName 'VigilBrain'
    if ($task.State -ne 'Disabled') {
        if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName 'VigilBrain' }
        Start-ScheduledTask -TaskName 'VigilBrain'
        Add-Content -LiteralPath $logFile -Value "$(Get-Date -Format o) Restarted VigilBrain after three failed health checks"
    }
    $failures = 0
}
$agent = Get-ScheduledTask -TaskName 'VigilAgent'
if ($agent.State -notin @('Running','Queued','Disabled')) {
    Start-ScheduledTask -TaskName 'VigilAgent'
    Add-Content -LiteralPath $logFile -Value "$(Get-Date -Format o) Started stopped VigilAgent"
}
@{failures=$failures;checkedAt=(Get-Date -Format o);healthy=$healthy} | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
