"""像素级取证：悬浮层画一块大黄块，截图后统计黄色像素，判断到底有没有画到屏幕上。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import mss
from PIL import Image

from app.config import Config
from app import winutil

user32 = ctypes.WinDLL("user32")


def count_color(img: Image.Image, rgb, tol=40):
    n = 0
    px = img.convert("RGB").load()
    for y in range(img.height):
        for x in range(img.width):
            p = px[x, y]
            if abs(p[0] - rgb[0]) < tol and abs(p[1] - rgb[1]) < tol and abs(p[2] - rgb[2]) < tol:
                n += 1
    return n


def main():
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    root = tk.Tk()
    root.title("PIXEL_TEST")
    root.geometry("500x260+200+200")
    root.configure(bg="#303030")
    root.update()
    hwnd = int(user32.GetAncestor(root.winfo_id(), 2))

    key = cfg.get("overlay", "transparent_key", "#010203")

    # 方案 A：普通 Toplevel + transparentcolor
    top_a = tk.Toplevel(root)
    top_a.overrideredirect(True)
    top_a.attributes("-topmost", True)
    top_a.attributes("-transparentcolor", key)
    top_a.geometry("400x120+210+210")
    c_a = tk.Canvas(top_a, bg=key, highlightthickness=0)
    c_a.pack(fill="both", expand=True)
    c_a.create_text(10, 10, text="方案A 测试文字", anchor="nw",
                    font=("Microsoft YaHei", 16), fill="#FFE066")
    c_a.create_rectangle(10, 50, 200, 90, fill="#FF00FF", outline="#FF00FF")
    root.update()

    def shot(tag):
        s = mss.MSS()
        img = Image.frombytes("RGB", (500, 260), s.grab({"left": 200, "top": 200, "width": 500, "height": 260}).bgra, "raw", "BGRX")
        path = os.path.join(BASE, "tools", f"pixel_{tag}.png")
        img.save(path)
        return img, path

    results = {}

    def step1():
        img, p = shot("a")
        results["A_yellow"] = count_color(img, (255, 224, 102))
        results["A_magenta"] = count_color(img, (255, 0, 255))
        print(f"[A] {p} 黄像素={results['A_yellow']} 品红像素={results['A_magenta']}")

        # 方案 B：不用 transparentcolor，只用 alpha
        top_a.destroy()
        top_b = tk.Toplevel(root)
        top_b.overrideredirect(True)
        top_b.attributes("-topmost", True)
        top_b.attributes("-alpha", 0.95)
        top_b.geometry("400x120+210+210")
        top_b.configure(bg="#000000")
        c_b = tk.Canvas(top_b, bg="#000000", highlightthickness=0)
        c_b.pack(fill="both", expand=True)
        c_b.create_text(10, 10, text="方案B 测试文字", anchor="nw",
                        font=("Microsoft YaHei", 16), fill="#FFE066")
        root.update()
        root.after(900, step2)

    def step2():
        img, p = shot("b")
        y = count_color(img, (255, 224, 102))
        print(f"[B] {p} 黄像素={y}")
        root.destroy()

    root.after(900, step1)
    root.after(3500, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
