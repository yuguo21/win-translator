"""Win32 封装：窗口枚举、客户区坐标、进程名、置顶穿透、DPI 感知。

全部基于 ctypes，不依赖 pywin32。
"""
import ctypes
import ctypes.wintypes as wt
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str
    minimized: bool = False

    def label(self) -> str:
        tag = "  [已最小化]" if self.minimized else ""
        return f"{self.title}  [{self.process}:{self.pid}]{tag}"


class _POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class _RECT_(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
        ("ptMinPosition", _POINT), ("ptMaxPosition", _POINT), ("rcNormalPosition", _RECT_),
    ]


def get_restored_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """窗口"还原后"的矩形。最小化窗口的 GetWindowRect 会返回 (-32000…) 这种假坐标，
    这里读 rcNormalPosition，用来判断窗口原本有多大。"""
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        return None
    r = wp.rcNormalPosition
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def set_dpi_aware() -> None:
    """开启 Per-Monitor DPI 感知 V2，保证高分屏坐标是物理像素。"""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def get_process_name(pid: int) -> str:
    if pid <= 0:
        return "?"
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return "?"
    finally:
        kernel32.CloseHandle(handle)


def _window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def _is_tool_window(hwnd: int) -> bool:
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    return bool(ex & WS_EX_TOOLWINDOW) and not bool(ex & WS_EX_APPWINDOW)


def get_window_title(hwnd: int) -> str:
    """实时读窗口标题（浏览器切标签/翻页后标题会变，不能用缓存的那份）。"""
    try:
        return _window_text(hwnd) if is_window(hwnd) else ""
    except Exception:
        return ""


def get_foreground_hwnd() -> int:
    """当前前台窗口的 hwnd（用来判断用户这会儿正在看哪个浏览器窗口）。"""
    try:
        return int(user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def list_windows(exclude_hwnds=None) -> List[WindowInfo]:
    """枚举所有可见、有标题的顶层窗口。"""
    exclude = set(exclude_hwnds or [])
    result: List[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, lparam):
        try:
            if hwnd in exclude:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, 4) != 0:  # GW_OWNER：跳过纯工具窗口的子层
                pass
            if _is_tool_window(hwnd):
                return True
            title = _window_text(hwnd).strip()
            if not title:
                return True
            minimized = bool(user32.IsIconic(hwnd))
            if minimized:
                # 最小化窗口：GetWindowRect 是 (-32000,-32000) 的假坐标，
                # 改用"还原后"的尺寸判断，否则游戏/浏览器最小化就彻底找不到
                rect = get_restored_rect(hwnd)
            else:
                r = wt.RECT()
                rect = ((r.left, r.top, r.right, r.bottom)
                        if user32.GetWindowRect(hwnd, ctypes.byref(r)) else None)
            if not rect:
                return True
            if rect[2] - rect[0] < 80 or rect[3] - rect[1] < 60:
                return True
            pid = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            result.append(WindowInfo(int(hwnd), title, int(pid.value),
                                     get_process_name(int(pid.value)), minimized))
        except Exception:
            pass
        return True

    user32.EnumWindows(_cb, 0)
    result.sort(key=lambda w: (w.process.lower(), w.title.lower()))
    return result


def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def get_client_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """返回客户区在屏幕坐标系下的 (left, top, right, bottom)，单位物理像素。"""
    r = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wt.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return int(pt.x), int(pt.y), int(pt.x + (r.right - r.left)), int(pt.y + (r.bottom - r.top))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


# Chromium 系浏览器把网页正文画在内部子窗口上，类名是这些
RENDER_WINDOW_CLASSES = (
    "Chrome_RenderWidgetHostHWND",     # Chrome / Edge / Electron
    "WebViewHost",                     # Electron WebView
    "Internet Explorer_Server",        # IE / 旧版内嵌
    "MozillaContentWindowClass",       # Firefox 内容区
)


def list_child_windows(hwnd: int) -> List[Tuple[int, str, str]]:
    """返回直接子窗口 [(hwnd, 类名, 标题)]。"""
    out: List[Tuple[int, str, str]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(h, _l):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls, 256)
        ttl = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, ttl, 512)
        out.append((int(h), cls.value, ttl.value))
        return True

    user32.EnumChildWindows(hwnd, _cb, 0)
    return out


def find_render_widget(hwnd: int) -> Optional[int]:
    """在窗口里找"画网页内容"的那个子窗口，找不到返回 None。

    浏览器正文不在顶层窗口的可访问树里，而在这个内部子窗口上，
    对它请求 MSAA 才能读到整页文本和坐标。
    """
    for h, cls, _ttl in list_child_windows(hwnd):
        for want in RENDER_WINDOW_CLASSES:
            if cls == want:
                r = get_window_rect(h)
                if r and (r[2] - r[0]) > 100 and (r[3] - r[1]) > 100:
                    return h
    return None


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


def is_occluded(hwnd: int) -> bool:
    """用客户区中心点做遮挡检测：该点上的顶层窗口不是自身即视为被遮挡。"""
    rect = get_client_rect(hwnd)
    if not rect:
        return False
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    pt = wt.POINT(cx, cy)
    hit = user32.WindowFromPoint(pt)
    if not hit:
        return False
    root = user32.GetAncestor(hit, 2)  # GA_ROOT
    return root != hwnd


def set_click_through(hwnd: int) -> None:
    """设置悬浮层：鼠标穿透、不抢焦点、不进任务栏。

    注意：不要在这里加 WS_EX_LAYERED —— tkinter 的 -transparentcolor 已经给顶层窗口
    设置了分层属性；对不带 SetLayeredWindowAttributes 的窗口额外加 LAYERED，
    会让该窗口整体全透明（内容看不见）。
    """
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex = ex | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


def toplevel_hwnd(hwnd: int) -> int:
    """把任意子窗口句柄换成所属顶层窗口句柄。"""
    root = user32.GetAncestor(hwnd, 2)  # GA_ROOT
    return int(root) if root else int(hwnd)


def set_topmost(hwnd: int, topmost: bool = True) -> None:
    user32.SetWindowPos(hwnd, -1 if topmost else -2, 0, 0, 0, 0, 0x0001 | 0x0002)


def get_cursor_pos() -> Tuple[int, int]:
    """当前鼠标位置（屏幕物理像素）。"""
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def get_window_hwnd_under_cursor() -> Optional[int]:
    """鼠标拾取：返回光标下的顶层窗口句柄（用于点选窗口模式）。"""
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hit = user32.WindowFromPoint(pt)
    if not hit:
        return None
    return int(user32.GetAncestor(hit, 2))


def find_window_by_pid(pid: int):
    """按进程找第一个可见顶层窗口，供内存/图形模式使用。"""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, lparam):
        p = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if int(p.value) == pid and user32.IsWindowVisible(hwnd) and _window_text(hwnd).strip():
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


# ---------------- 窗口内容抓取（不含覆盖层） ----------------
PW_RENDERFULLCONTENT = 0x00000002
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


def capture_client_rgba(hwnd: int):
    """用 PrintWindow 抓目标窗口客户区，返回 (w, h, bgra_bytes)，失败返回 None。

    为什么不用屏幕截图：悬浮层就叠在目标窗口上，屏幕截图会把中文译文也抓进来，
    形成"翻译自己的译文"的循环。PrintWindow 直接向目标窗口要画面，不含覆盖层。
    """
    rect = get_client_rect(hwnd)
    if not rect:
        return None
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None

    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        if not ok:
            ok = user32.PrintWindow(hwnd, hdc_mem, 0)
        if not ok:
            return None
        bi = _BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h          # 负数 = 自上而下
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0      # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), 0)
        if got == 0:
            return None
        return w, h, bytes(buf.raw)
    finally:
        try:
            gdi32.SelectObject(hdc_mem, old)
        except Exception:
            pass
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
