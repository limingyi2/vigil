param([Parameter(Mandatory=$true)][string]$RequestPath,[switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
function Get-Sha256([string]$Path) {
  $algorithm = [Security.Cryptography.SHA256]::Create()
  $stream = [IO.File]::OpenRead($Path)
  try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
  finally { $stream.Dispose(); $algorithm.Dispose() }
}
$request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
$install = [IO.Path]::GetFullPath($request.installDir).TrimEnd('\')
$backup = [IO.Path]::GetFullPath($request.backupDir).TrimEnd('\')
$homeDir = [IO.Path]::GetFullPath($request.userData).TrimEnd('\')
if ($install.Length -lt 8 -or $install -eq [IO.Path]::GetPathRoot($install) -or
    -not $backup.StartsWith(($homeDir + '\updates\rollback-'),[StringComparison]::OrdinalIgnoreCase) -or
    $homeDir.StartsWith(($install + '\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe recovery paths' }
foreach ($item in $request.files.PSObject.Properties) {
  $file = [IO.Path]::GetFullPath((Join-Path $backup $item.Name))
  if (-not $file.StartsWith(($backup + '\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe backup entry' }
  if ((Get-Sha256 $file) -ne $item.Value) { throw 'Backup hash mismatch' }
}
$appExecutable = [string]$request.appExecutable
if ($appExecutable -notmatch '^[A-Za-z0-9][A-Za-z0-9 ._-]*\.exe$') {
  $rootExecutables = @($request.files.PSObject.Properties |
    Where-Object { $_.Name -notmatch '[\\/]' -and $_.Name -match '\.exe$' -and $_.Name -notmatch '^Uninstall\b' })
  if ($rootExecutables.Count -ne 1) { throw 'Incomplete backup: app executable is ambiguous' }
  $appExecutable = $rootExecutables[0].Name
}
$appRecord = $request.files.PSObject.Properties |
  Where-Object { $_.Name -ceq $appExecutable }
if (-not $appRecord -or -not (Test-Path -LiteralPath (Join-Path $backup $appExecutable))) {
  throw 'Incomplete backup: app executable is missing'
}
$stage = $install + '.restore-' + $request.id
$failed = $install + '.failed-' + $request.id
if ((Test-Path -LiteralPath $stage) -or (Test-Path -LiteralPath $failed)) { throw 'Recovery staging already exists' }
Copy-Item -LiteralPath $backup -Destination $stage -Recurse
foreach ($item in $request.files.PSObject.Properties) {
  if ((Get-Sha256 (Join-Path $stage $item.Name)) -ne $item.Value) { throw 'Recovery staging hash mismatch' }
}
if ($request.waitPid -gt 0) { Wait-Process -Id $request.waitPid -ErrorAction SilentlyContinue }
# All three paths are validated absolute siblings of the explicitly named install dir.
Move-Item -LiteralPath $install -Destination $failed
try { Move-Item -LiteralPath $stage -Destination $install }
catch { Move-Item -LiteralPath $failed -Destination $install; throw }
$request.status = 'rolled_back'
$request | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $homeDir 'updates\pending.json') -Encoding UTF8
if (-not $NoLaunch) { Start-Process -FilePath (Join-Path $install $appExecutable) -WindowStyle Hidden }
