"""给网页悬停翻译拍一张干净的成品图：只抓浏览器窗口，不混进桌面其它窗口。

用法：先跑 tools\\test_web_hover.py --real（或手动翻几段），再执行本脚本。
本脚本只负责找到那个调试浏览器窗口、把它置前、抓窗口客户区存 PNG。
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import winutil
from PIL import Image

OUT = os.path.join(BASE, "tools", "web_hover_clean.png")


def main() -> int:
    target = None
    for w in winutil.list_windows():
        if "msedge.exe" in (w.process or "").lower() and "edge" in (w.title or "").lower():
            target = w
            break
    if target is None:
        print("没找到 Edge 窗口")
        return 1
    winutil.set_topmost(target.hwnd, True)
    time.sleep(0.6)
    data = winutil.capture_client_rgba(target.hwnd)
    if not data:
        print("抓图失败")
        return 2
    w, h, raw = data
    img = Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX", 0, 1)
    img.save(OUT)
    print("已保存:", OUT, img.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
