@echo off
rem ---------------------------------------------------------------------------
rem SoundCloud Discovery - double-click this to launch.
rem
rem The first run sets itself up: it makes a private Python environment in
rem .venv and installs what requirements.txt lists. After that it just starts.
rem
rem Uses pythonw.exe rather than python.exe so no empty console window sits
rem behind the app. That also means a startup crash has nowhere to print, so
rem src/ui/app.py catches it, writes crash.log and shows a dialog.
rem ---------------------------------------------------------------------------

cd /d "%~dp0"
set "VENV_PY=.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto :check_libraries

echo Setting up SoundCloud Discovery. This happens once and takes a minute.
echo.
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul && set "PY=python"
if not defined PY goto :no_python
%PY% -m venv .venv
if errorlevel 1 goto :no_python

rem Checked here because a missing import fails before the app's own crash
rem handler exists, and under pythonw.exe that failure is silent.
:check_libraries
"%VENV_PY%" -c "import customtkinter, requests" >nul 2>nul
if not errorlevel 1 goto :launch
echo Installing the libraries the app needs...
"%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :install_failed

rem discovery.db and config.json need no setup: the app makes both, and walks
rem a first launch through connecting to SoundCloud.
:launch
start "" ".venv\Scripts\pythonw.exe" -m src.ui.app
exit /b 0

:no_python
echo.
echo SoundCloud Discovery needs Python 3.12 or newer, and none was found.
echo.
echo Install it from https://www.python.org/downloads/ - in the installer,
echo tick "Add python.exe to PATH" - then double-click run.bat again.
echo.
pause
exit /b 1

:install_failed
echo.
echo Installing failed - see the messages above. Check your internet
echo connection, then double-click run.bat again.
echo.
pause
exit /b 1
