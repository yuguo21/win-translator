"""列出所有可见顶层窗口（标题/pid/矩形），调试抓图用。

用法：python tools/list_wins.py [关键字]
"""
import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32


def main() -> int:
    key = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if key and key not in title.lower():
            return True
        q = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(q))
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        print("pid=%-6s hwnd=%-10s %4dx%-4d %-20s %s" % (
            q.value, hwnd, r.right - r.left, r.bottom - r.top, cls.value[:20], title[:70]))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
