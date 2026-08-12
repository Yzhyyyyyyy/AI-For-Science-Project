"""Start the local server and open the browser only after health checks pass."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8000
VERSION = "5.1"
ROOT = Path(__file__).resolve().parent
APP_URL = f"http://{HOST}:{PORT}/"
HEALTH_URL = f"http://{HOST}:{PORT}/api/health?client_version={VERSION}"


def read_health(timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def port_is_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, PORT)) == 0


def open_browser_when_ready() -> None:
    for _ in range(120):
        health = read_health()
        if health and str(health.get("version")) == VERSION:
            print(f"[4/4] 页面已就绪，正在打开 {APP_URL}", flush=True)
            webbrowser.open(APP_URL, new=2)
            return
        time.sleep(0.5)
    print(f"[提示] 浏览器未自动打开，请手动访问 {APP_URL}", flush=True)


def main() -> int:
    health = read_health()
    if health:
        running_version = str(health.get("version") or "unknown")
        if running_version == VERSION:
            print(f"[提示] v{VERSION} 服务已经在运行，直接打开网页。", flush=True)
            webbrowser.open(APP_URL, new=2)
            return 10
        print(
            f"[错误] 8000 端口正在运行 v{running_version}，但当前程序是 v{VERSION}。\n"
            "请先关闭旧版服务窗口，再重新启动。",
            flush=True,
        )
        return 2

    if port_is_in_use():
        print(
            "[错误] 8000 端口已被其他程序占用，但对方不是可识别的 AI学术审查系统服务。\n"
            "请关闭占用 8000 端口的程序后重试。",
            flush=True,
        )
        return 2

    backend_dir = ROOT / "backend"
    if not (backend_dir / "api.py").is_file():
        print(f"[错误] 未找到后端程序：{backend_dir / 'api.py'}", flush=True)
        return 2

    os.chdir(backend_dir)
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    import uvicorn

    print(f"[3/4] 正在启动 v{VERSION} 服务：{APP_URL}", flush=True)
    print("使用期间请保持此窗口开启；按 Ctrl+C 可停止服务。", flush=True)
    uvicorn.run("api:app", host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
