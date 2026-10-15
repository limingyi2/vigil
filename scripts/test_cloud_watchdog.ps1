$ErrorActionPreference = 'Stop'
$sandbox = Join-Path ([IO.Path]::GetTempPath()) ('vigil-watchdog-test-' + [guid]::NewGuid())
$script:healthy = $false
$script:brainState = 'Running'
$script:agentState = 'Running'
$script:starts = @()
$script:stops = @()
function Invoke-RestMethod { param($Uri, $TimeoutSec) return @{ok=$script:healthy} }
function Get-ScheduledTask { param($TaskName) return @{State=$(if($TaskName -eq 'VigilBrain'){$script:brainState}else{$script:agentState})} }
function Start-ScheduledTask { param($TaskName) $script:starts += $TaskName }
function Stop-ScheduledTask { param($TaskName) $script:stops += $TaskName }
function Assert($condition, $message) { if(-not $condition){throw $message} }
$watchdog = Join-Path $PSScriptRoot 'cloud_watchdog.ps1'
& $watchdog -Root $sandbox
& $watchdog -Root $sandbox
Assert ($script:starts.Count -eq 0) 'Transient failures must not restart Brain'
& $watchdog -Root $sandbox
Assert ($script:starts.Count -eq 1 -and $script:stops.Count -eq 1) 'Three failures must restart Brain once'
& $watchdog -Root $sandbox
$script:healthy = $true
& $watchdog -Root $sandbox
$state = Get-Content (Join-Path $sandbox 'data/run/cloud-watchdog.json') -Raw | ConvertFrom-Json
Assert ($state.failures -eq 0) 'Health recovery must reset the failure count'
$script:agentState = 'Ready'
& $watchdog -Root $sandbox
Assert ($script:starts[-1] -eq 'VigilAgent') 'Normal agent exit must be recovered'
$script:brainState = 'Disabled'; $script:agentState = 'Disabled'; $script:healthy = $false
$before = $script:starts.Count
1..3 | ForEach-Object { & $watchdog -Root $sandbox }
Assert ($script:starts.Count -eq $before) 'Explicitly disabled tasks must stay disabled'
Write-Output 'PASS watchdog: failure threshold, recovery, normal exit, disabled tasks'
