"""探测：对 Chromium 内部渲染子窗口请求 MSAA，看能否读到网页正文。

Chromium 的网页内容不在顶层窗口的可访问树里，而在类名
`Chrome_RenderWidgetHostHWND`（标题 `Chrome Legacy Window`）的子窗口上。
本脚本枚举子窗口，对每个候选请求 IAccessible，统计可读条目。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from ctypes import byref, c_long, c_void_p, POINTER

from app import msaa, winutil

user32 = ctypes.WinDLL("user32", use_last_error=True)

BROWSERS = ("msedge.exe", "chrome.exe", "brave.exe", "opera.exe", "vivaldi.exe")


def child_windows(hwnd: int):
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(h, _l):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls, 256)
        ttl = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, ttl, 512)
        r = wt.RECT()
        user32.GetWindowRect(h, byref(r))
        out.append((h, cls.value, ttl.value, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumChildWindows(hwnd, _cb, 0)
    return out


def read_all(hwnd: int, max_items: int = 120, max_depth: int = 12):
    """对指定 hwnd 走完整 MSAA 递归，返回 [(text, x, y, w, h)]。"""
    root = msaa.Accessible.from_window(hwnd)
    if root is None:
        return []
    out = []

    def walk(node, depth):
        if depth > max_depth or len(out) >= max_items:
            return
        try:
            count = node.child_count()
        except Exception:
            return
        if count <= 0 or count > 2000:
            return
        for i in range(1, count + 1):
            if len(out) >= max_items:
                return
            try:
                name = node.name(i)
            except Exception:
                name = ""
            if name:
                try:
                    loc = node.location(i)
                except Exception:
                    loc = None
                if loc and loc[2] > 2 and loc[3] > 2:
                    out.append((name, loc[0], loc[1], loc[2], loc[3]))
                    continue
            try:
                child = node.child(i)
            except Exception:
                child = None
            if child is None:
                continue
            try:
                walk(child, depth + 1)
            finally:
                child.release()

    try:
        walk(root, 0)
    finally:
        root.release()
    return out


def main() -> int:
    winutil.set_dpi_aware()
    msaa.co_initialize()
    wins = [w for w in winutil.list_windows() if w.process.lower() in BROWSERS]
    if not wins:
        print("没有浏览器窗口")
        return 1
    w = wins[0]
    print("浏览器:", w.label())
    print("顶层 MSAA 条目:", len(read_all(w.hwnd)))

    print("\n子窗口清单：")
    for h, cls, ttl, x, y, cw, ch in child_windows(w.hwnd):
        print(f"  hwnd={h} class={cls!r} title={ttl!r} rect=({x},{y},{cw}x{ch})")

    cands = [c for c in child_windows(w.hwnd)
             if "Chrome_RenderWidgetHost" in c[1] or "Legacy Window" in c[2]
             or "Intermediate D3D" in c[1]]
    print(f"\n渲染窗口候选 {len(cands)} 个，逐个尝试 MSAA：")
    best = None
    for h, cls, ttl, x, y, cw, ch in cands:
        items = read_all(h)
        print(f"  {cls} / {ttl}: 读到 {len(items)} 条")
        for it in items[:8]:
            print(f"      ({it[1]},{it[2]},{it[3]}x{it[4]}) {it[0][:60]}")
        if items and (best is None or len(items) > len(best[1])):
            best = (h, items, cls)
    if best:
        print(f"\n最佳：hwnd={best[0]} class={best[2]} 条目={len(best[1])}")
        return 0
    print("\n所有候选都读不到")
    return 2


if __name__ == "__main__":
    sys.exit(main())
