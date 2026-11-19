"""悬浮层单独验证：黑底窗口 + 透明悬浮层画一行中文 → 截图看是否叠在画面上。"""
import os
import sys
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import mss
from PIL import Image

from app.config import Config
from app.overlay import Overlay
from app.engines.base import TextItem
from app import winutil

OUT = os.path.join(BASE, "tools", "overlay_check.png")


def main() -> int:
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    root = tk.Tk()
    root.title("OVERLAY_TEST")
    root.geometry("600x300+150+150")
    root.configure(bg="#202020")
    tk.Label(root, text="Battlemage      Ready weapon", bg="#202020", fg="white",
             font=("Consolas", 18)).pack(pady=40)
    root.update()

    hwnd = int(__import__("ctypes").WinDLL("user32").GetAncestor(root.winfo_id(), 2))
    key = cfg.get("overlay", "transparent_key", "#010203")
    ov = Overlay(root, cfg, key)
    rect = winutil.get_client_rect(hwnd)
    ov.place_over(rect)
    ov.show()
    root.update_idletasks()

    items = [
        (TextItem(20, 20, 140, 26, "Battlemage"), "战斗法师"),
        (TextItem(20, 80, 140, 26, "Ready weapon"), "准备武器"),
    ]

    def draw():
        ov.draw(items)
        root.update_idletasks()

    def shot():
        s = mss.MSS()
        box = winutil.get_client_rect(hwnd)
        img = s.grab({"left": box[0], "top": box[1],
                      "width": box[2] - box[0], "height": box[3] - box[1]})
        Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX").save(OUT)
        print("overlay geometry:", ov.root.geometry(), "hwnd:", ov._hwnd)

    root.after(1500, draw)
    root.after(3000, shot)
    root.after(4200, root.destroy)
    root.mainloop()
    print("截图:", OUT, os.path.exists(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
