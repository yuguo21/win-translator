"""诊断：为什么工具里看不到某个游戏窗口（默认查 h5_game）。

用法：python tools\\diag_game_window.py [进程名关键字]
"""
import ctypes
import ctypes.wintypes as wt
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import winutil

user32 = ctypes.windll.user32
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
WS_VISIBLE = 0x10000000


def main() -> int:
    winutil.set_dpi_aware()
    key = sys.argv[1].lower() if len(sys.argv) > 1 else "h5"

    print("=== 工具能看到的窗口（list_windows 的结果）===")
    for w in winutil.list_windows():
        if key in (w.process or "").lower() or key in (w.title or "").lower():
            print("  命中: hwnd=%s pid=%s 进程=%s 标题=%r" % (w.hwnd, w.pid, w.process, w.title))
    else:
        pass
    listed = [w for w in winutil.list_windows()]
    print("  总计 %d 个窗口；其中与 %r 相关: %d 个"
          % (len(listed), key,
             sum(1 for w in listed if key in (w.process or "").lower() or key in (w.title or "").lower())))

    print("\n=== 原始枚举：所有顶层窗口里与 %r 相关的进程 ===" % key)
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lparam):
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = (winutil.get_process_name(int(pid.value)) or "")
        if key and key not in proc.lower():
            return True
        hits.append((int(hwnd), int(pid.value), proc))
        return True

    user32.EnumWindows(cb, 0)
    if not hits:
        print("  没有找到任何该进程的顶层窗口")
        return 2

    for hwnd, pid, proc in hits:
        style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        owner = user32.GetWindow(hwnd, 4)          # GW_OWNER
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        print("  hwnd=%s pid=%s 进程=%s" % (hwnd, pid, proc))
        print("     标题=%r" % winutil.get_window_title(hwnd))
        print("     类名=%s 可见=%s 最小化=%s 有owner=%s"
              % (cls.value, bool(user32.IsWindowVisible(hwnd)), bool(user32.IsIconic(hwnd)), bool(owner)))
        print("     尺寸=(%d,%d)-(%d,%d) 即 %dx%d"
              % (r.left, r.top, r.right, r.bottom, r.right - r.left, r.bottom - r.top))
        print("     style=0x%08X（WS_VISIBLE=%s）" % (style, bool(style & WS_VISIBLE)))
        print("     exstyle=0x%08X（TOOLWINDOW=%s APPWINDOW=%s）"
              % (ex, bool(ex & WS_EX_TOOLWINDOW), bool(ex & WS_EX_APPWINDOW)))
        print("     _is_tool_window=%s" % winutil._is_tool_window(hwnd))
        # 逐个说明会被哪条过滤规则刷掉
        reasons = []
        if not user32.IsWindowVisible(hwnd):
            reasons.append("IsWindowVisible=False")
        if winutil._is_tool_window(hwnd):
            reasons.append("被判成工具窗口(WS_EX_TOOLWINDOW)")
        if not (winutil.get_window_title(hwnd) or "").strip():
            reasons.append("标题为空")
        if r.right - r.left < 80 or r.bottom - r.top < 60:
            reasons.append("尺寸小于 80x60")
        print("     >>> 被过滤的原因: %s" % ("、".join(reasons) if reasons else "无（应该能在列表里看到）"))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
