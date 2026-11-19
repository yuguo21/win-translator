"""字号测试：同一批原文，用 9 / 11 / 14 / 18 四种字号各渲染一次并截图，比较效果。"""
import ctypes
import os
import sys
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import mss
from PIL import Image

from app.config import Config
from app.engines.base import TextItem
from app.overlay import Overlay
from app import winutil

OUT = os.path.join(BASE, "tools", "font_size_check.png")

SAMPLES = [
    ("Battlemage", "战斗法师"),
    ("Ready weapon", "准备武器"),
    ("Press any key to continue", "按任意键继续"),
    ("Your health is low", "你的生命值过低"),
]


def main() -> int:
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    root = tk.Tk()
    root.title("FONT_SIZE_TEST")
    root.geometry("620x420+120+120")
    root.configure(bg="#000000")
    for en, _ in SAMPLES:
        tk.Label(root, text=en, bg="#000000", fg="#FFFFFF",
                 font=("Consolas", 20), anchor="w").pack(anchor="w", padx=16, pady=12)
    root.update()

    hwnd = int(ctypes.WinDLL("user32").GetAncestor(root.winfo_id(), 2))
    key = cfg.get("overlay", "transparent_key", "#010203")
    ov = Overlay(root, cfg, key)
    rect = winutil.get_client_rect(hwnd)
    ov.place_over(rect)
    ov.show()

    items = []
    y = 12
    for en, zh in SAMPLES:
        items.append((TextItem(16, y, 220, 26, en), zh))
        y += 58

    def render(size):
        cfg.set("overlay", "font_size", size, False)
        ov.draw(items)
        root.update_idletasks()

    def shot(tag):
        s = mss.MSS()
        box = winutil.get_client_rect(hwnd)
        img = s.grab({"left": box[0], "top": box[1],
                      "width": box[2] - box[0], "height": box[3] - box[1]})
        im = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        path = os.path.join(BASE, "tools", f"font_{tag}.png")
        im.save(path)
        print(f"字号 {tag}: {path}")

    sizes = [9, 11, 14, 18]
    seq = []

    def step(i=0):
        if i >= len(sizes):
            root.destroy()
            return
        size = sizes[i]
        render(size)
        root.update()
        root.after(400, lambda: (shot(size), root.after(600, lambda: step(i + 1))))

    root.after(600, step)
    root.after(9000, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
