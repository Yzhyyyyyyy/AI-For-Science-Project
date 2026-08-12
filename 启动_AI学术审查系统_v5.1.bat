@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title AI学术审查系统 v5.1 一键启动

cd /d "%~dp0"

echo.
echo ==================================================
echo   AI学术审查系统 v5.1 正式发布版一键启动
echo ==================================================
echo.

rem ---------- 关键文件检查 ----------
if not exist "backend\requirements.txt" goto :files_missing
if not exist "start_server.py" goto :files_missing
if not exist "find_python.ps1" goto :files_missing
if not exist "frontend\dist\index.html" goto :dist_missing
if not exist "backend\.env" goto :env_missing

rem ---------- 端口占用检查（防止误开旧 v4.8）----------
echo [检查] 检测 8000 端口是否被占用...
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo.
    echo [警告] 8000 端口已被占用，可能是旧版 v4.8 或其他服务在运行。
    echo 请先关闭旧服务窗口，或在任务管理器结束占用 8000 端口的 Python 进程。
    echo 为避免误打开旧版页面，本脚本将停止启动。
    echo.
    pause
    exit /b 1
)

rem ---------- 智能查找 Python ----------
call :find_python
if not defined PYTHON_EXE goto :offer_python_install
echo [1/5] Python 已找到: !PYTHON_EXE!

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

rem ---------- 本地虚拟环境 ----------
if not exist "!VENV_PY!" goto :create_venv
"!VENV_PY!" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :rebuild_venv
goto :check_dependencies

:rebuild_venv
set "VENV_BACKUP=%~dp0.venv_incompatible_!RANDOM!"
echo [2/5] 检测到属于其他电脑的虚拟环境，正在重建...
move "!VENV_DIR!" "!VENV_BACKUP!" >nul
if errorlevel 1 goto :venv_rebuild_failed

:create_venv
echo [2/5] 正在为本机创建本地 Python 环境...
"!PYTHON_EXE!" -m venv "!VENV_DIR!"
if errorlevel 1 goto :venv_failed

:check_dependencies
if not exist "!VENV_DIR!\.dependencies-v5.1" goto :install_dependencies
"!VENV_PY!" -c "import fastapi, uvicorn, multipart, openai, dotenv, fitz, PIL, docx, requests" >nul 2>nul
if errorlevel 1 goto :install_dependencies
echo [3/5] 依赖已就绪。
goto :launch

:install_dependencies
echo [4/5] 正在安装依赖（首次运行需数分钟，请耐心等待）...
"!VENV_PY!" -m pip install --upgrade pip -q
"!VENV_PY!" -m pip install -r backend\requirements.txt
if errorlevel 1 goto :dependencies_failed
"!VENV_PY!" -c "import fastapi, uvicorn, multipart, openai, dotenv, fitz, PIL, docx, requests" >nul 2>nul
if errorlevel 1 goto :dependencies_failed
type nul > "!VENV_DIR!\.dependencies-v5.1"
echo [4/5] 依赖安装完成。

:launch
echo [5/5] 正在启动服务并打开浏览器...
echo       前端来源：%~dp0frontend\dist
echo       服务地址：http://127.0.0.1:8000/
echo.
rem start_server.py 会等后端就绪并自动打开浏览器（含版本校验）
"!VENV_PY!" start_server.py
set "START_EXIT=!ERRORLEVEL!"
if !START_EXIT! equ 2 goto :version_conflict
if !START_EXIT! neq 0 goto :service_failed
echo.
echo 服务已启动。浏览器若显示旧版，请 Ctrl+F5 强制刷新。
echo 使用期间请保持本窗口开启；关闭本窗口或按 Ctrl+C 停止服务。
echo.
pause
exit /b 0

:version_conflict
echo.
echo [错误] 8000 端口正在运行其他版本的服务。
echo 请关闭旧服务后重新运行本脚本。
pause
exit /b 1

:files_missing
echo [错误] 缺少必需文件。请保持 bat、find_python.ps1、start_server.py、backend、frontend 目录在一起。
pause
exit /b 1

:dist_missing
echo [错误] 未找到 frontend\dist\index.html，前端构建产物缺失。
echo 请先构建前端（cd frontend && npm run build）后重试。
pause
exit /b 1

:env_missing
echo [提示] 未检测到 backend\.env。
echo 请复制 backend\.env.example 为 backend\.env，填写 API Key 后重新运行。
echo.
pause
exit /b 1

:offer_python_install
echo [错误] 未找到 Python 3.10 或更高版本。
echo 请先安装 Python 3.10+，然后重新运行本脚本。
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:venv_failed
echo [错误] 创建本地 Python 环境失败，使用的解释器为: !PYTHON_EXE!
pause
exit /b 1

:venv_rebuild_failed
echo [错误] 无法移动不兼容的 .venv 目录。请关闭所有 Python 窗口后重试。
pause
exit /b 1

:dependencies_failed
echo [错误] 依赖安装或校验失败。请检查网络、代理和杀毒软件设置后重试。
pause
exit /b 1

:service_failed
echo.
echo [错误] 服务启动失败，退出码: !START_EXIT!
echo 请查看上方详细报错信息。
pause
exit /b !START_EXIT!

rem ---------- find_python ----------
:find_python
set "PYTHON_EXE="
if exist "%~dp0runtime\python\python.exe" (
    set "PYTHON_EXE=%~dp0runtime\python\python.exe"
    exit /b 0
)
for %%P in (python.exe python3.exe) do (
    where %%P >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=%%P"
        exit /b 0
    )
)
if exist "%ProgramFiles%\Python312\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python312\python.exe" & exit /b 0
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe" & exit /b 0
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe" & exit /b 0
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python310\python.exe" & exit /b 0
exit /b 0
