$ErrorActionPreference = 'Stop'
$sandbox = Join-Path ([IO.Path]::GetTempPath()) ('vigil-watchdog-test-' + [guid]::NewGuid())
$global:VigilWatchdogTest_healthy = $false
$global:VigilWatchdogTest_brainState = 'Running'
$global:VigilWatchdogTest_agentState = 'Running'
$global:VigilWatchdogTest_starts = @()
$global:VigilWatchdogTest_stops = @()
function Invoke-RestMethod { param($Uri, $TimeoutSec) return @{ok=$global:VigilWatchdogTest_healthy} }
function Get-ScheduledTask { param($TaskName) return @{State=$(if($TaskName -eq 'VigilBrain'){$global:VigilWatchdogTest_brainState}else{$global:VigilWatchdogTest_agentState})} }
function Start-ScheduledTask { param($TaskName) $global:VigilWatchdogTest_starts += $TaskName }
function Stop-ScheduledTask { param($TaskName) $global:VigilWatchdogTest_stops += $TaskName }
function Assert($condition, $message) { if(-not $condition){throw $message} }
$watchdog = Join-Path $PSScriptRoot 'cloud_watchdog.ps1'
& $watchdog -Root $sandbox
& $watchdog -Root $sandbox
Assert ($global:VigilWatchdogTest_starts.Count -eq 0) 'Transient failures must not restart Brain'
& $watchdog -Root $sandbox
Assert ($global:VigilWatchdogTest_starts.Count -eq 1 -and $global:VigilWatchdogTest_stops.Count -eq 1) 'Three failures must restart Brain once'
& $watchdog -Root $sandbox
$global:VigilWatchdogTest_healthy = $true
& $watchdog -Root $sandbox
$state = Get-Content (Join-Path $sandbox 'data/run/cloud-watchdog.json') -Raw | ConvertFrom-Json
Assert ($state.failures -eq 0) 'Health recovery must reset the failure count'
$global:VigilWatchdogTest_agentState = 'Ready'
& $watchdog -Root $sandbox
Assert ($global:VigilWatchdogTest_starts[-1] -eq 'VigilAgent') 'Normal agent exit must be recovered'
$global:VigilWatchdogTest_brainState = 'Disabled'; $global:VigilWatchdogTest_agentState = 'Disabled'; $global:VigilWatchdogTest_healthy = $false
$before = $global:VigilWatchdogTest_starts.Count
1..3 | ForEach-Object { & $watchdog -Root $sandbox }
Assert ($global:VigilWatchdogTest_starts.Count -eq $before) 'Explicitly disabled tasks must stay disabled'
Write-Output 'PASS watchdog: failure threshold, recovery, normal exit, disabled tasks'
