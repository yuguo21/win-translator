"""悬浮层诊断：打印窗口样式、尺寸、canvas 内容，判断为什么不显示。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import tkinter as tk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app.config import Config
from app.overlay import Overlay
from app.engines.base import TextItem
from app import winutil

user32 = ctypes.WinDLL("user32")

GWL_EXSTYLE = -20


def ex_flags(hwnd):
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    names = []
    for bit, name in [(0x00080000, "LAYERED"), (0x00000020, "TRANSPARENT"),
                      (0x00000080, "TOOLWINDOW"), (0x08000000, "NOACTIVATE")]:
        if ex & bit:
            names.append(name)
    return f"0x{ex:X} " + ",".join(names)


def main():
    winutil.set_dpi_aware()
    cfg = Config(os.path.join(BASE, "config.json"))
    root = tk.Tk()
    root.title("OVERLAY_DIAG")
    root.geometry("600x300+150+150")
    root.configure(bg="#202020")
    root.update()
    hwnd_main = int(user32.GetAncestor(root.winfo_id(), 2))

    ov = Overlay(root, cfg, cfg.get("overlay", "transparent_key", "#010203"))
    rect = winutil.get_client_rect(hwnd_main)
    print("主窗口客户区:", rect)
    ov.place_over(rect)
    ov.show()
    root.update()

    def draw():
        ov.draw([
            (TextItem(20, 20, 140, 26, "Battlemage"), "战斗法师"),
            (TextItem(20, 80, 140, 26, "Ready weapon"), "准备武器"),
        ])
        root.update_idletasks()
        top = int(user32.GetAncestor(ov.root.winfo_id(), 2))
        print("overlay winfo_id :", ov.root.winfo_id())
        print("overlay top hwnd :", top)
        print("overlay exstyle  :", ex_flags(top))
        print("overlay exstyle(child):", ex_flags(ov.root.winfo_id()))
        print("IsWindowVisible  :", bool(user32.IsWindowVisible(top)))
        print("geometry         :", ov.root.geometry())
        wr = wt.RECT(); user32.GetWindowRect(top, ctypes.byref(wr))
        print("win rect         :", wr.left, wr.top, wr.right, wr.bottom)
        print("canvas size      :", ov.canvas.winfo_width(), ov.canvas.winfo_height())
        print("items on canvas  :", len(ov.canvas.find_all()))
        for iid in ov.canvas.find_all()[:4]:
            print("   ", iid, ov.canvas.type(iid), ov.canvas.bbox(iid), repr(ov.canvas.itemcget(iid, "text")[:20]) if ov.canvas.type(iid) == "text" else "")
        print("alpha attr       :", ov.root.attributes("-alpha"))

    root.after(800, draw)
    root.after(3500, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
