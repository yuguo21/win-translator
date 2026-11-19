"""端到端联调：测试窗口 → OCR 抓取 → 翻译 → 悬浮层渲染 → 截图。

用法：python tools/test_pipeline.py
产物：tools/pipeline_check.png（联调截图）
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
from app.translator import Translator
from app.winutil import WindowInfo

SAMPLES = [
    "Battlemage",
    "Ready weapon",
    "Press any key to continue",
    "Your health is low",
]

SHOT_PATH = os.path.join(BASE, "tools", "pipeline_check.png")
LOG = []


def build_window():
    root = tk.Tk()
    root.title("OCR_TEST_WINDOW")
    root.geometry("620x420+120+120")
    root.configure(bg="#000000")
    for s in SAMPLES:
        tk.Label(root, text=s, bg="#000000", fg="#FFFFFF",
                 font=("Consolas", 20), anchor="w").pack(anchor="w", padx=16, pady=10)
    root.update()
    return root


def main() -> int:
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    translator = Translator(cfg, BASE)

    root = build_window()
    hwnd = int(ctypes.WinDLL("user32").GetAncestor(root.winfo_id(), 2))
    win = WindowInfo(hwnd, "OCR_TEST_WINDOW", os.getpid(), "python.exe")

    key = cfg.get("overlay", "transparent_key", "#010203") or "#010203"
    overlay = Overlay(root, cfg, key)
    rect = winutil.get_client_rect(hwnd)
    overlay.place_over(rect)
    overlay.show()

    controller = Controller(cfg, translator, overlay,
                            on_log=lambda m: LOG.append(("log", m)),
                            on_status=lambda m: LOG.append(("status", m)))
    controller.attach_window(win)

    eng = engines.create_engine("ocr", cfg)
    eng.prepare(win)
    controller.start(eng)

    def shot():
        try:
            s = mss.MSS()
            box = winutil.get_client_rect(hwnd)
            img = s.grab({"left": box[0], "top": box[1],
                          "width": box[2] - box[0], "height": box[3] - box[1]})
            Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX").save(SHOT_PATH)
            LOG.append(("shot", SHOT_PATH))
        except Exception as e:
            LOG.append(("shot_fail", str(e)))

    root.after(7000, shot)
    root.after(9000, root.destroy)
    root.mainloop()

    controller.stop()
    print(f"截图: {SHOT_PATH}  存在={os.path.exists(SHOT_PATH)}")
    for kind, msg in LOG:
        print(f"  [{kind}] {msg}")
    print("翻译统计:", translator.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
