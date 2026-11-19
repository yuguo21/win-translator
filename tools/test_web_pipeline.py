"""网页翻译模式端到端联调：读取浏览器页面 → 翻译 → 悬浮层叠加 → 截图。

用 DummyTranslator（不请求 API、不花钱），只验证"读页 → 摆位 → 渲染"整条链路。

用法：python tools/test_web_pipeline.py
产物：tools/web_pipeline_check.png
"""
import ctypes
import os
import sys
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import mss
from PIL import Image

from app import engines, winutil
from app.config import Config
from app.controller import Controller
from app.overlay import Overlay
from app.winutil import WindowInfo

BROWSERS = ("msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe")
SHOT_PATH = os.path.join(BASE, "tools", "web_pipeline_check.png")
LOG = []


class DummyTranslator:
    stats = {"hits": 0, "misses": 0, "errors": 0}

    def translate_lines(self, lines):
        return {t: f"〔译〕{t[:24]}" for t in lines}


def main() -> int:
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    wins = [w for w in winutil.list_windows() if w.process.lower() in BROWSERS]
    if not wins:
        print("没有浏览器窗口可测")
        return 1
    target = [w for w in wins if "English Test Page" in w.title] or wins[:1]
    w = target[0]
    print("目标:", w.label())

    root = tk.Tk()
    root.withdraw()                       # 测试脚本不需要自己的主窗口
    key = cfg.get("overlay", "transparent_key", "#010203") or "#010203"
    overlay = Overlay(root, cfg, key)
    overlay.show()

    ctl = Controller(cfg, DummyTranslator(), overlay,
                     on_log=lambda m: (LOG.append(("log", m)), print("  [log]", m)),
                     on_status=lambda m: (LOG.append(("status", m)), print("  [status]", m)))
    ctl.attach_window(w)

    eng = engines.create_engine("web", cfg)
    eng.prepare(w)
    ctl.start(eng)

    def shot():
        try:
            s = mss.MSS()
            box = winutil.get_client_rect(w.hwnd)
            img = s.grab({"left": box[0], "top": box[1],
                          "width": box[2] - box[0], "height": box[3] - box[1]})
            Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX").save(SHOT_PATH)
            print("截图:", SHOT_PATH)
        except Exception as e:
            print("截图失败:", e)

    root.after(9000, shot)
    root.after(11000, root.destroy)
    root.mainloop()

    ctl.stop()
    print("截图存在:", os.path.exists(SHOT_PATH))
    return 0 if os.path.exists(SHOT_PATH) else 2


if __name__ == "__main__":
    sys.exit(main())
