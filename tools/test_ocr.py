"""功能自测（真实窗口）：开一个英文测试窗口 → 后台线程截图 OCR → 打印识别结果。

要点：Tk 主线程保持运行，OCR 在后台线程执行（与主程序 controller 的做法一致）。
用法：python tools/test_ocr.py
"""
import ctypes
import os
import sys
import threading
import time
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import winutil
from app.config import Config
from app.engines.ocr_engine import OcrEngine
from app.winutil import WindowInfo

SAMPLES = [
    "Battlemage",
    "The Elder Scrolls II: Daggerfall",
    "Ready weapon",
    "Press any key to continue",
    "Your health is low",
]

RESULT = {}


def build_window():
    root = tk.Tk()
    root.title("OCR_TEST_WINDOW")
    root.geometry("620x420+120+120")
    root.configure(bg="#000000")
    for s in SAMPLES:
        tk.Label(root, text=s, bg="#000000", fg="#FFFFFF",
                 font=("Consolas", 20), anchor="w").pack(anchor="w", padx=16, pady=8)
    root.update()
    return root


def worker(hwnd: int):
    time.sleep(2.0)                      # 等窗口画完
    cfg = Config(os.path.join(BASE, "config.json"))
    eng = OcrEngine(cfg)
    win = WindowInfo(hwnd, "OCR_TEST_WINDOW", os.getpid(), "python.exe")
    eng.prepare(win)
    RESULT["rect"] = eng.client_rect()
    items = []
    for i in range(3):
        items = eng.capture_once()
        if items:
            break
        time.sleep(0.8)
    RESULT["items"] = items
    RESULT["err"] = eng.last_error
    eng.close()


def main() -> int:
    winutil.set_dpi_aware()
    root = build_window()
    root.update()
    hwnd = int(ctypes.WinDLL("user32").GetAncestor(root.winfo_id(), 2))

    t = threading.Thread(target=worker, args=(hwnd,), daemon=True)
    t.start()
    root.after(12000, root.destroy)      # 兜底：12 秒后自动关窗
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass

    items = RESULT.get("items") or []
    print("客户区:", RESULT.get("rect"))
    print(f"识别到 {len(items)} 行：")
    for it in items:
        print(f"  ({it.x},{it.y},{it.w}x{it.h})  {it.text}")
    if RESULT.get("err"):
        print("last_error:", RESULT["err"])
    joined = " ".join(i.text.lower() for i in items)
    hit = sum(1 for s in SAMPLES if s.lower() in joined)
    print(f"命中 {hit}/{len(SAMPLES)}")
    return 0 if hit >= 3 else 1


if __name__ == "__main__":
    sys.exit(main())
