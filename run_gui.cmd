@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "GUI_SCRIPT=%SCRIPT_DIR%ai_cli_installer_gui.py"

if not exist "%GUI_SCRIPT%" (
  echo GUI script not found: "%GUI_SCRIPT%"
  exit /b 1
)

py -3.14 "%GUI_SCRIPT%"
exit /b %ERRORLEVEL%
