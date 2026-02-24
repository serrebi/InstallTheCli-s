@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SPEC=InstallTheCli.spec"
set "PYI_BASE_ARGS=--clean --noconfirm --log-level ERROR"
set "LOGFILE=.tmp_pyinstaller_build.log"

echo [build] Running PyInstaller with reduced log verbosity (ERROR)...
py -3.14 -m PyInstaller %PYI_BASE_ARGS% %SPEC% > "%LOGFILE%" 2>&1
if errorlevel 1 goto :fallback

if exist "dist\InstallTheCli.exe" echo [build] Success: dist\InstallTheCli.exe
if exist "%LOGFILE%" del /q "%LOGFILE%" >nul 2>nul
exit /b 0

:fallback
echo [build] Primary build failed. Retrying to timestamped output to avoid locked dist EXE...
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%I"

setlocal EnableDelayedExpansion
set "DISTDIR=dist-!TS!"
set "WORKDIR=build-!TS!"

py -3.14 -m PyInstaller %PYI_BASE_ARGS% --distpath "!DISTDIR!" --workpath "!WORKDIR!" %SPEC% > "%LOGFILE%" 2>&1
set "EC=!ERRORLEVEL!"
if not "!EC!"=="0" goto :fallback_fail

echo [build] Success: !DISTDIR!\InstallTheCli.exe
if exist "%LOGFILE%" del /q "%LOGFILE%" >nul 2>nul
endlocal & endlocal & exit /b 0

:fallback_fail
echo [build] Fallback build failed with exit code !EC!.
if exist "%LOGFILE%" (
  findstr /V /C:"DEPRECATION: Running PyInstaller as admin" "%LOGFILE%"
)
endlocal & endlocal & exit /b %EC%
