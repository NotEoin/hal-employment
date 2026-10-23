@echo off
rem Updates Hal Employment when the app cannot update itself. Double-click it.
rem git may replace this file while the update runs, and cmd.exe re-reads a batch file from disk after each
rem command, so it first copies itself to %TEMP% and runs from there.
set "HAL_COPY=%TEMP%\hal-update-%RANDOM%.cmd"
if not "%~1"=="--copy" (copy /y "%~f0" "%HAL_COPY%" >nul & "%HAL_COPY%" --copy "%~dp0" & exit /b)
set "HAL_SCRIPTS=%~2"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$f = Join-Path $env:APPDATA 'Hal Employment\data\state\install.json'; if (-not (Test-Path -LiteralPath $f)) { Write-Host 'Hal was not installed with its install file. Run that file again.'; exit 1 }; $i = Get-Content -Raw -LiteralPath $f | ConvertFrom-Json; $env:UV_PYTHON_INSTALL_DIR = $i.python_dir; $env:UV_CACHE_DIR = $i.uv_cache; & $i.uv run --no-project --python 3.13 (Join-Path $env:HAL_SCRIPTS 'updater.py'); exit $LASTEXITCODE" & echo Press any key to close. & pause >nul & exit /b
