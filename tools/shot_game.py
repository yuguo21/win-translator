"""抓游戏窗口画面，判断能不能用 OCR（全屏独占的 D3D 游戏经常抓到黑屏）。

用法：
  python tools\\shot_game.py                 # 只抓，不切窗口（最小化时多半是黑图）
  python tools\\shot_game.py --activate      # 先把窗口还原并切到前台再抓（会占用屏幕）
  python tools\\shot_game.py homm5 --activate

输出：tools\\game_printwindow.png、tools\\game_screen.png，并打印黑像素占比。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import winutil

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 0x00000002


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def dark_ratio(img) -> float:
    gray = img.convert("L")
    hist = gray.histogram()
    dark = sum(hist[:16])
    total = float(img.width * img.height) or 1.0
    return dark / total


def capture_printwindow(hwnd: int, path: str):
    from PIL import Image
    rect = winutil.get_restored_rect(hwnd)
    if not rect:
        return False, "拿不到窗口矩形"
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w <= 0 or h <= 0:
        return False, "窗口尺寸异常 %dx%d" % (w, h)
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    ok = user32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)
    buf = ctypes.create_string_buffer(w * h * 4)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    got = gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    img.save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    return bool(ok), "PrintWindow=%s GetDIBits=%s 尺寸=%dx%d 黑像素占比=%.1f%%" % (
        bool(ok), int(got), w, h, dark_ratio(img) * 100)


def capture_screen(path: str):
    import mss
    from PIL import Image
    with mss.mss() as sct:
        mon = sct.monitors[1]
        grab = sct.grab(mon)
        img = Image.frombytes("RGB", grab.size, grab.bgra, "raw", "BGRX")
    img.save(path)
    return "尺寸=%dx%d 黑像素占比=%.1f%%" % (img.width, img.height, dark_ratio(img) * 100)


def main() -> int:
    args = sys.argv[1:]
    activate = "--activate" in args
    key = "h5"
    for a in args:
        if not a.startswith("--"):
            key = a

    winutil.set_dpi_aware()
    wins = [w for w in winutil.list_windows()
            if key in (w.process or "").lower() or key in (w.title or "").lower()]
    if not wins:
        print("没找到进程名/标题含 %r 的窗口" % key)
        return 2
    w = wins[0]
    print("目标: %s  hwnd=%s  最小化=%s" % (w.label(), w.hwnd, w.minimized))

    prev_fg = user32.GetForegroundWindow()
    if activate:
        print("还原窗口并切到前台（抓完会切回原来那个窗口）…")
        user32.ShowWindow(w.hwnd, 9)      # SW_RESTORE
        user32.SetForegroundWindow(w.hwnd)
        time.sleep(1.5)

    path_pw = os.path.join(BASE, "tools", "game_printwindow.png")
    try:
        ok, msg = capture_printwindow(w.hwnd, path_pw)
        print("PrintWindow: %s  %s" % ("成功" if ok else "失败", msg))
    except Exception as e:
        print("PrintWindow 抓取失败:", e)

    path_sc = os.path.join(BASE, "tools", "game_screen.png")
    try:
        print("整屏截图:", capture_screen(path_sc))
    except Exception as e:
        print("整屏截图失败:", e)

    if activate and prev_fg:
        try:
            user32.SetForegroundWindow(prev_fg)
            print("已切回原来的窗口")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
