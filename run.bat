@echo off
rem Start the prototype: run.bat [database-file] [port]
rem Defaults: siemens_uat.sqlite3 in this folder, port 8765.
setlocal
cd /d "%~dp0"
set "DB=%~1"
if "%DB%"=="" set "DB=siemens_uat.sqlite3"
set "PORT=%~2"
if "%PORT%"=="" set "PORT=8765"

if exist "%DB%" goto havedb
echo Database not found: %DB%
echo Put siemens_uat.sqlite3 next to run.bat, or pass its path: run.bat path\to\file.sqlite3
pause
exit /b 1

:havedb
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto havepy
python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto havepy
echo Python 3.11 or newer was not found. Install it from python.org, tick "Add to PATH", and run this again.
pause
exit /b 1

:havepy
if exist ".venv\Scripts\python.exe" goto havevenv
echo First start: creating a private Python environment...
%PY% -m venv .venv
if errorlevel 1 goto fail

:havevenv
.venv\Scripts\python.exe -c "import openpyxl" >nul 2>&1
if not errorlevel 1 goto run
echo Installing the one required package, openpyxl...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check --quiet -r requirements.txt
if errorlevel 1 goto fail

:run
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:%PORT%"
echo Starting on http://127.0.0.1:%PORT% with database %DB%. Close this window or press Ctrl+C to stop.
.venv\Scripts\python.exe server.py --db "%DB%" --port %PORT%
pause
exit /b 0

:fail
echo Setup failed. See the message above and the Troubleshooting section of README.md.
pause
exit /b 1
