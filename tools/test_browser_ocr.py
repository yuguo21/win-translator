"""对真实浏览器窗口跑一次 OCR，确认浏览器画面能被正确识别翻译。"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import mss
from PIL import Image

from app import winutil
from app.config import Config
from app.engines.ocr_engine import OcrEngine
from app.winutil import WindowInfo

BROWSERS = ("msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe")


def main() -> int:
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    wins = [w for w in winutil.list_windows() if w.process.lower() in BROWSERS]
    if not wins:
        print("没有找到浏览器窗口")
        return 1
    w = wins[0]
    print("目标:", w.label())

    eng = OcrEngine(cfg)
    eng.prepare(w)
    print("客户区:", eng.client_rect())
    items = []
    for i in range(3):
        items = eng.capture_once()
        if items:
            break
    print(f"识别到 {len(items)} 行，前 12 条：")
    for it in items[:12]:
        print(f"  ({it.x},{it.y},{it.w}x{it.h}) {it.text[:70]}")

    # 存一张实际参与识别的画面
    shot = winutil.capture_client_rgba(w.hwnd)
    if shot:
        sw, sh, bgra = shot
        Image.frombytes("RGB", (sw, sh), bgra, "raw", "BGRX").save(
            os.path.join(BASE, "tools", "browser_ocr_check.png"))
        print("画面已存: tools/browser_ocr_check.png")
    eng.close()
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
