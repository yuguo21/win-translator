"""测试专用浏览器：独立端口 + 独立配置目录，绝不碰用户正在用的翻译浏览器。

用户日常会把「启动翻译浏览器」（9222 端口、browser_profile）拿来读真实网页，
所以自测脚本一律只能连这里起的实例（9333 端口、browser_profile_test）。
"""
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp

PORT = 9333
PROFILE = os.path.join(BASE, "browser_profile_test")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def ensure(open_url: str = "") -> int:
    """确保测试浏览器在跑，返回端口。"""
    if cdp.find_port((PORT,)):
        if open_url:
            _navigate(open_url)
        return PORT
    args = [
        EDGE,
        "--remote-debugging-port=%d" % PORT,
        "--user-data-dir=%s" % PROFILE,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if open_url:
        args.append(open_url)
    else:
        args.append("about:blank")
    subprocess.Popen(args, close_fds=True)
    for _ in range(60):
        if cdp.find_port((PORT,)):
            if open_url:
                time.sleep(1.0)
                _navigate(open_url)
            return PORT
        time.sleep(0.5)
    raise RuntimeError("测试浏览器没能启动（端口 %d）" % PORT)


def _navigate(url: str) -> None:
    pages = cdp.list_pages(PORT)
    if not pages:
        return
    pg = cdp.CDPPage(pages[0]["webSocketDebuggerUrl"])
    try:
        pg.eval("location.href = %r; 1" % url)
    finally:
        pg.close()


def page_url(name: str) -> str:
    return "file:///" + os.path.join(BASE, "tools", name).replace("\\", "/")


def window_for_port(port: int):
    """按「谁在监听这个调试端口」找到浏览器窗口，避免抓到用户自己的 Edge。"""
    from app import winutil
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort %d -State Listen).OwningProcess" % port],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:
        out = ""
    pids = set()
    for tok in out.split():
        if tok.strip().isdigit():
            pids.add(int(tok.strip()))
    for w in winutil.list_windows():
        if "msedge.exe" not in (w.process or "").lower():
            continue
        if w.pid in pids:
            return w
    return None
