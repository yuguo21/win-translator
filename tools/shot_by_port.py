"""按调试端口抓目标窗口的客户区截图（用于看注入效果）。

用法：python tools/shot_by_port.py 9400 tools/ui_inject_vscode.png
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x2


def pids_for_port(port: int):
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort %d -State Listen).OwningProcess | Select-Object -Unique" % port],
            capture_output=True, text=True, timeout=20)
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


def windows_of_pid(pid: int):
    found = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, lparam):
        q = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(q))
        if q.value == pid and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 200 and h > 200:
                found.append((hwnd, w, h))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def grab(hwnd) -> Image.Image:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    raw = ctypes.create_string_buffer(w * h * 4)
    bmpinfo = BITMAPINFOHEADER()
    bmpinfo.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmpinfo.biWidth = w
    bmpinfo.biHeight = -h
    bmpinfo.biPlanes = 1
    bmpinfo.biBitCount = 32
    gdi32.GetDIBits(mem, bmp, 0, h, raw, ctypes.byref(bmpinfo), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX", 0, 1)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9400
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "tools", "shot_port.png")
    pids = pids_for_port(port)
    print("端口", port, "-> PID", pids)
    for pid in pids:
        wins = windows_of_pid(pid)
        print("  PID", pid, "可见窗口", len(wins), [(w, h) for _, w, h in wins])
        if wins:
            hwnd, w, h = max(wins, key=lambda t: t[1] * t[2])
            img = grab(hwnd)
            img.save(out)
            print("已保存:", out, img.size)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
