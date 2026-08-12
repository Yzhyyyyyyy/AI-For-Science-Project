@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title AI Academic Review System v5.1
cd /d "%~dp0"

echo ============================================
echo   AI Academic Review System v5.1
echo ============================================
echo.

if not exist "backend\requirements.txt" goto :files_missing
if not exist "start_server.py" goto :files_missing
if not exist "find_python.ps1" goto :files_missing
if not exist "frontend\dist\index.html" goto :dist_missing

call :find_python
if not defined PYTHON_EXE goto :offer_python_install
echo [1/4] Python detected: !PYTHON_EXE!

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "!VENV_PY!" goto :create_venv
"!VENV_PY!" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :rebuild_venv
goto :check_dependencies

:rebuild_venv
set "VENV_BACKUP=%~dp0.venv_incompatible_!RANDOM!"
echo [2/4] The copied virtual environment belongs to another computer.
echo       Moving it to: !VENV_BACKUP!
move "!VENV_DIR!" "!VENV_BACKUP!" >nul
if errorlevel 1 goto :venv_rebuild_failed

:create_venv
echo [2/4] Creating a local Python environment for this computer...
"!PYTHON_EXE!" -m venv "!VENV_DIR!"
if errorlevel 1 goto :venv_failed

:check_dependencies
if not exist "!VENV_DIR!\.dependencies-v5.1" goto :install_dependencies
"!VENV_PY!" -c "import fastapi, uvicorn, multipart, openai, dotenv, fitz, PIL, docx, requests" >nul 2>nul
if errorlevel 1 goto :install_dependencies
echo [2/4] Required dependencies are ready.
goto :launch

:install_dependencies
echo [3/4] Installing required dependencies for this computer...
"!VENV_PY!" -m pip install --disable-pip-version-check -r "backend\requirements.txt"
if errorlevel 1 goto :dependencies_failed
"!VENV_PY!" -c "import fastapi, uvicorn, multipart, openai, dotenv, fitz, PIL, docx, requests" >nul 2>nul
if errorlevel 1 goto :dependencies_failed
type nul > "!VENV_DIR!\.dependencies-v5.1"

:launch
echo [4/4] Starting the local service...
"!VENV_PY!" "start_server.py"
set "START_EXIT=!errorlevel!"
if "!START_EXIT!"=="10" goto :already_running
if not "!START_EXIT!"=="0" goto :service_failed
echo.
echo The service has stopped.
pause
exit /b 0

:find_python
set "PYTHON_EXE="
for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0find_python.ps1"`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
exit /b 0

:offer_python_install
echo [ERROR] Python 3.10 or newer was not found on this computer.
where winget.exe >nul 2>nul
if errorlevel 1 goto :python_manual_install
echo.
choice /C YN /N /M "Install Python 3.12 for the current user now? [Y/N]: "
if errorlevel 2 goto :python_manual_install
winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :python_install_failed
call :find_python
if not defined PYTHON_EXE goto :python_install_failed
echo Python installation completed. Restarting the launcher...
start "" "%~f0"
exit /b 0

:python_manual_install
echo Download Python from: https://www.python.org/downloads/windows/
echo During installation, select "Add python.exe to PATH", then run this BAT again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:python_install_failed
echo [ERROR] Automatic Python installation failed.
echo Please install Python 3.10 or newer manually and run this BAT again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:already_running
echo.
echo The browser has been opened. The existing service remains active.
exit /b 0

:files_missing
echo [ERROR] Required files are missing. Keep the BAT, find_python.ps1,
echo         start_server.py and backend folder together.
pause
exit /b 1

:dist_missing
echo [ERROR] frontend\dist\index.html was not found.
echo Please rebuild frontend before running this launcher.
pause
exit /b 1

:venv_failed
echo [ERROR] Failed to create the local Python environment with:
echo         !PYTHON_EXE!
pause
exit /b 1

:venv_rebuild_failed
echo [ERROR] The incompatible .venv folder could not be moved.
echo Close all Python windows and try again.
pause
exit /b 1

:dependencies_failed
echo [ERROR] Dependency installation or validation failed.
echo Check the network, proxy and antivirus settings, then run this BAT again.
pause
exit /b 1

:service_failed
echo.
echo [ERROR] The service could not start. Exit code: !START_EXIT!
echo Follow the detailed message shown above.
pause
exit /b !START_EXIT!
