@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Weather Desk UK UK12
set "WEATHER_DESK_APP=%~dp0"
set "weather_runtime=%~dp0runtime\windows-x64"
if "%~1"=="--installed-python" goto installed
where powershell.exe >nul 2>&1
if errorlevel 1 goto no_powershell
echo Weather Desk UK UK12 - preparing bundled Python...
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference = 'Stop';$lock = $null;$stage = $null;try {;$root = [IO.Path]::GetFullPath($env:WEATHER_DESK_APP);if (-not (Test-Path -LiteralPath (Join-Path $root 'RUNTIME-CHECKSUMS.json') -PathType Leaf)) { throw 'The Python file checklist is missing.' };if (-not (Test-Path -LiteralPath (Join-Path $root 'RUNTIME-ARCHIVE.json') -PathType Leaf)) { throw 'The Python archive checklist is missing.' };$manifest = Get-Content -LiteralPath (Join-Path $root 'RUNTIME-CHECKSUMS.json') -Raw | ConvertFrom-Json;$factory = Get-Content -LiteralPath (Join-Path $root 'RUNTIME-ARCHIVE.json') -Raw | ConvertFrom-Json;$entries = @($manifest.files.PSObject.Properties);if ($manifest.schema -ne 1 -or $entries.Count -ne 39) { throw 'The Python file checklist is incomplete. Download the full new ZIP.' };foreach ($entry in $entries) {;if ($entry.Name -notmatch '^[A-Za-z0-9_.-]+$' -or $entry.Value.bytes -lt 1 -or $entry.Value.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'The Python file checklist is invalid.' };};function Find-RuntimeProblems($directory) {;foreach ($entry in $entries) {;$file = Join-Path $directory $entry.Name;try {;if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { Write-Output $entry.Name; continue };if ((Get-Item -LiteralPath $file).Length -ne $entry.Value.bytes) { Write-Output $entry.Name; continue };if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.Value.sha256) { Write-Output $entry.Name };} catch { Write-Output $entry.Name };};};$runtimeRoot = Join-Path $root 'runtime';$target = Join-Path $runtimeRoot 'windows-x64';New-Item -Path $runtimeRoot -ItemType Directory -Force | Out-Null;try { $lock = [IO.File]::Open((Join-Path $runtimeRoot '.setup.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) } catch { throw 'Another Weather Desk startup may be preparing Python. Wait for it to finish, then try again.' };$bad = @(Find-RuntimeProblems $target);if ($bad.Count -gt 0) {;if ($factory.schema -ne 1 -or $factory.file -ne 'python-windows-x64.zip' -or $factory.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'The Python archive checklist is invalid.' };$archive = Join-Path $runtimeRoot $factory.file;if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) { throw 'The included Python archive is missing. Download and extract the complete new Weather Desk ZIP.' };if ((Get-Item -LiteralPath $archive).Length -ne $factory.bytes -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $factory.sha256) { throw 'The included Python archive is damaged. Download the new Weather Desk ZIP again.' };Write-Host 'Preparing bundled Python from the included archive...';$stage = Join-Path $runtimeRoot ('prepare-' + [Guid]::NewGuid().ToString('N'));Expand-Archive -LiteralPath $archive -DestinationPath $stage;$stageBad = @(Find-RuntimeProblems $stage);if ($stageBad.Count -gt 0) { throw ('Python files were not available after extraction: ' + ($stageBad -join ', ') + '. Check whether Windows Security or another security program reported these files. The app will not retry repeatedly.') };New-Item -Path $target -ItemType Directory -Force | Out-Null;foreach ($name in $bad) { Copy-Item -LiteralPath (Join-Path $stage $name) -Destination (Join-Path $target $name) -Force };$remaining = @(Find-RuntimeProblems $target);if ($remaining.Count -gt 0) { throw ('Python files are still unavailable: ' + ($remaining -join ', ') + '. Close any old Weather Desk windows and check whether Windows Security reported a blocked file.') };};Write-Host 'Bundled Python verified: all 39 runtime files are present and match this download.';} catch {;Write-Host ('Weather Desk runtime preparation failed: ' + $_.Exception.Message);exit 1;} finally {;if ($stage -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue };if ($lock) { $lock.Dispose() };};exit 0"
if errorlevel 1 goto preparation_failed
"%weather_runtime%\python.exe" -I -X utf8 -B "%~dp0launch_weather.py" %*
set "weather_exit=%errorlevel%"
goto finish
:installed
echo Weather Desk UK UK12 - using installed Python.
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -I -X utf8 -B "%~dp0launch_weather.py" %*
set "weather_exit=%errorlevel%"
goto finish
:try_python
where python >nul 2>&1
if errorlevel 1 goto not_found
python -I -X utf8 -B "%~dp0launch_weather.py" %*
set "weather_exit=%errorlevel%"
goto finish
:no_powershell
echo Windows PowerShell is unavailable. Use START-WITH-INSTALLED-PYTHON.bat.
set "weather_exit=1"
goto finish
:not_found
echo No installed Python was found. Use the normal START launcher for bundled Python.
set "weather_exit=1"
goto finish
:preparation_failed
echo Bundled Python preparation failed. The message above identifies the failed step.
echo You can also use START-WITH-INSTALLED-PYTHON.bat with Python 3.10 or newer.
set "weather_exit=1"
:finish
if not "%weather_exit%"=="0" echo Weather Desk stopped with an error. Keep the message above for troubleshooting.
pause
exit /b %weather_exit%
