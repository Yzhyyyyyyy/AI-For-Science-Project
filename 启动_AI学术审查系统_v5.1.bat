@echo off
chcp 65001 >nul
title AI学术审查系统 v5.1

echo ==========================================
echo   AI学术审查系统 v5.1
echo ==========================================
echo.

cd /d "%~dp0"

if not exist "backend\.env" (
    echo [提示] 未检测到 backend\.env
    echo   请复制 backend\.env.example 为 backend\.env
    echo   并填写你的 API Key 后重新运行本脚本。
    echo.
    pause
    exit /b 1
)

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 或更高版本。
    pause
    exit /b 1
)

echo [1/3] 检查 Python 依赖...
if exist "backend\requirements.txt" (
    pip install -r backend\requirements.txt -q
) else (
    echo [警告] 未找到 requirements.txt，请确认依赖已手动安装。
)

echo [2/3] 启动后端服务...
echo   浏览器地址: http://127.0.0.1:8000/
echo   使用期间请保持此窗口开启。
echo   按 Ctrl+C 停止服务。
echo.

cd backend
start "" http://127.0.0.1:8000/
python api.py

pause
