"""Windows UI 自动化抓取引擎。

两条路径：
1) Win32 路径（默认、可靠）：EnumChildWindows + WM_GETTEXT，读取标准控件原生文本。
2) UIA 路径（尽力而为）：winsdk 的 UI Automation，读取 Name / TextPattern，浏览器、Qt 应用更有效。

对自渲染窗口（Unity、DOSBox、DirectDraw 游戏）本条路径读不到内容，属于已知限制。
"""
import ctypes
import ctypes.wintypes as wt
from typing import List

from .. import winutil
from .base import BaseEngine, TextItem

user32 = ctypes.WinDLL("user32", use_last_error=True)
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
MAPVK_VK = 0

user32.SendMessageW.restype = ctypes.c_long


def _child_text(hwnd: int) -> str:
    try:
        length = int(user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0))
        if length <= 0 or length > 4096:
            return ""
        buf = ctypes.create_unicode_buffer(length + 2)
        user32.SendMessageW(hwnd, WM_GETTEXT, ctypes.c_wchar_p(length + 1), buf)
        return buf.value.strip()
    except Exception:
        return ""


def _child_rect_on_screen(hwnd: int):
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


NOISE_PREFIX = ("UI", "Ribbon", "Dock", "Mso", "AutoHidden", "Toolbar")
NOISE_EXACT = {"", "导航按钮", "地址", "搜索", "查看", "编辑"}
# 这些是窗口/控件把自己的类名当文本报出来的典型值，不是用户可见内容
NOISE_SUFFIX = (" Window", " Window Class", "WndClass", "Legacy Window")
NOISE_KEYWORDS = ("Chrome Legacy", "Intermediate D3D", "Default IME", "MSCTFIME", "GDI+ Window")


def _is_noise(text: str) -> bool:
    t = text.strip()
    if not t or t in NOISE_EXACT:
        return True
    for kw in NOISE_KEYWORDS:
        if kw.lower() in t.lower():
            return True
    for p in NOISE_PREFIX:
        if t.startswith(p):
            return True
    for suf in NOISE_SUFFIX:
        if t.endswith(suf):
            return True
    # 全大写且不含空格的类名字符串通常是控件内部名
    if t.isascii() and t.upper() == t and " " not in t and any(ch.isalpha() for ch in t) and len(t) > 12:
        return True
    return False


class UiaEngine(BaseEngine):
    mode = "uia"
    display_name = "Windows UI 自动化"
    description = "读取标准控件原生文本，无识别误差；Electron/Chromium 程序（VS Code 等）走注入读写 DOM。"

    # 默认值，避免 prepare 之前访问到不存在的属性
    _inject = None
    _uia_root = None
    _msaa_ok = None
    _auto_follow = False
    _bound_hwnd = None

    @property
    def auto_follow(self) -> bool:
        """没锁窗口、按鼠标所在窗口自动跟随。主循环据此允许 window 为 None。"""
        return bool(self._auto_follow)

    @property
    def renders_in_page(self) -> bool:
        """注入可用时译文直接写进目标程序的界面，不需要悬浮层贴图。"""
        return bool(self._inject and self._inject.attached)

    def prepare(self, window) -> None:
        self.window = window
        self.last_error = None
        self._uia_root = None
        self._msaa_ok = None          # None=还没试过，False=这个窗口不支持，不再尝试
        self._inject = None
        self._bound_hwnd = getattr(window, "hwnd", None)
        # 没锁窗口 = 自动跟随鼠标：每轮看鼠标在哪个窗口上，是 Electron 就现场连注入。
        # 悬停模式下本来就用不着固定窗口，所以不必强制先锁定。
        self._auto_follow = window is None
        if self._auto_follow:
            self.last_error = "未锁定窗口：按鼠标位置自动跟随（把鼠标移到目标窗口上即可）"
            return
        # 先试注入：Electron/Chromium 窗口（VS Code、Discord、各种套壳应用）的界面文本
        # UIA/MSAA 读不到，但只要有调试端口就能直接读写 DOM。
        if self.cfg.get("capture", "prefer_inject", True):
            try:
                from .inject import InjectChannel
                ch = InjectChannel(self.cfg)
                if ch.attach(window):
                    self._inject = ch
                    self.last_error = None
                    return
            except Exception as e:
                self.last_error = f"注入通道初始化失败: {e}"
        try:
            self._uia_root = self._build_uia_root(window.hwnd)
        except Exception as e:
            self._uia_root = None
            self.last_error = f"UIA 初始化失败（已回退 Win32 文本读取）: {e}"

    def follow_mouse(self) -> None:
        """自动跟随模式：把绑定切到鼠标所在的窗口（只支持可注入的程序）。"""
        from .. import winutil
        hwnd = winutil.get_window_hwnd_under_cursor()
        if not hwnd:
            return
        root = winutil.toplevel_hwnd(hwnd)
        if self._inject is not None and self._inject.attached and root == self._bound_hwnd:
            return                      # 还是同一个窗口，不用重连
        title = winutil.get_window_title(root) or ""
        win = winutil.WindowInfo(hwnd=root, title=title, pid=0, process="")
        ch = None
        ok = False
        try:
            from .inject import InjectChannel
            ch = InjectChannel(self.cfg)
            ok = ch.attach(win)
        except Exception as e:
            self.last_error = f"注入连接失败: {e}"
        if ok:
            if self._inject is not None:
                try:
                    self._inject.close()
                except Exception:
                    pass
            self._inject = ch
            self.window = win
            self._bound_hwnd = root
            self.last_error = None
        else:
            if ch is not None:
                try:
                    ch.close()
                except Exception:
                    pass
            if self._inject is not None:
                self._inject.close()
                self._inject = None
            self.window = win
            self._bound_hwnd = root
            self.last_error = (
                f"鼠标所在窗口「{(title[:30] + '...') if len(title) > 30 else title}」没有注入通道："
                "这个程序要先完全退出，再用带 --remote-debugging-port 的快捷方式启动"
                "（VS Code 用桌面那个图标）")

    # ---------- UIA ----------
    def _build_uia_root(self, hwnd: int):
        from winsdk.windows.ui.uiautomation import AutomationElement  # type: ignore

        # 不同版本 winsdk 暴露的方法名不同，逐个尝试
        for name in ("from_handle", "create_from_handle", "from_hwnd"):
            fn = getattr(AutomationElement, name, None)
            if fn is None:
                continue
            try:
                return fn(hwnd)
            except Exception:
                continue
        raise RuntimeError("winsdk 未提供 AutomationElement.from_handle")

    def _capture_uia(self) -> List[TextItem]:
        from winsdk.windows.ui.uiautomation import TreeScope, AutomationElementMode  # type: ignore

        root = self._uia_root
        if root is None:
            return []
        rect = self.client_rect()
        if not rect:
            return []
        ox, oy = rect[0], rect[1]
        out: List[TextItem] = []

        def walk(element, depth=0):
            if depth > 12 or len(out) >= 200:
                return
            try:
                name = (element.current.name or "").strip()
                br = element.current.bounding_rectangle
                if name and br and br.width > 2 and br.height > 2:
                    out.append(TextItem(
                        x=int(br.left - ox), y=int(br.top - oy),
                        w=int(br.width), h=int(br.height), text=name, conf=1.0,
                    ))
            except Exception:
                pass
            try:
                walker = element.get_current_pattern  # 占位，避免误用
            except Exception:
                pass
            try:
                children = element.find_all(TreeScope.CHILDREN, AutomationElementMode.FULL) \
                    if hasattr(element, "find_all") else None
            except Exception:
                children = None
            if not children:
                try:
                    from winsdk.windows.ui.uiautomation import Condition  # type: ignore
                except Exception:
                    return
            for child in list(children or []):
                walk(child, depth + 1)

        walk(root)
        return out

    # ---------- Win32 ----------
    def _capture_win32(self) -> List[TextItem]:
        rect = self.client_rect()
        if not rect:
            return []
        ox, oy = rect[0], rect[1]
        out: List[TextItem] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def _cb(hwnd, lparam):
            text = _child_text(hwnd)
            if not text or len(text) < 2 or _is_noise(text):
                return True
            r = _child_rect_on_screen(hwnd)
            if r:
                x0, y0, x1, y1 = r
                out.append(TextItem(x0 - ox, y0 - oy, x1 - x0, y1 - y0, text, 1.0))
            return True

        user32.EnumChildWindows(self.window.hwnd, _cb, 0)
        return out

    def capture_once(self) -> List[TextItem]:
        # 自动跟随模式：先按鼠标位置决定要翻哪个窗口
        if self._auto_follow:
            self.follow_mouse()

        if not self._auto_follow and (not self.window or not winutil.is_window(self.window.hwnd)):
            return self.fail("窗口不存在或已关闭")

        # 注入通道优先：直接读目标程序的 DOM，不受 UIA/MSAA 限制
        if self._inject is not None and self._inject.attached:
            try:
                blocks = self._inject.read()
            except Exception as e:
                self.last_error = f"注入读取失败: {e}"
                return []
            if not blocks:
                self.last_error = "这个窗口的界面上没有可翻的英文（或已经全翻完了）"
                return []
            self.last_error = None
            return [TextItem(int(b.get("x") or 0), int(b.get("y") or 0),
                             int(b.get("w") or 0), int(b.get("h") or 0),
                             str(b.get("text") or ""), 1.0) for b in blocks]

        # 自动跟随模式只走注入：没连上就是鼠标下那个窗口不支持（或没带端口启动）
        if self._auto_follow:
            return self.fail(self.last_error or "鼠标所在的窗口不支持注入")

        rect = self.client_rect()
        if not rect:
            return []
        ox, oy = rect[0], rect[1]

        merged: List[TextItem] = []
        if self._uia_root is not None:
            try:
                merged = self._capture_uia()
            except Exception as e:
                self.last_error = f"UIA 读取失败: {e}"
                merged = []

        # MSAA（仅对支持它的程序有效；浏览器的深层节点会被拒绝，属正常）
        # 每个窗口只试一次，失败就本次会话关掉，避免每轮都产生异常开销
        if self._msaa_ok is not False:
            got = 0
            try:
                from .. import msaa
                for text, x, y, w, h in msaa.read_window_texts(self.window.hwnd, max_items=80):
                    if w < 3 or h < 3 or _is_noise(text):
                        continue
                    merged.append(TextItem(x - ox, y - oy, w, h, text, 1.0))
                    got += 1
            except Exception:
                pass
            if got == 0:
                self._msaa_ok = False
            else:
                self._msaa_ok = True

        win32_items = self._capture_win32()
        keys = {(i.x // 4, i.y // 4, i.text.lower()) for i in merged}
        for it in win32_items:
            k = (it.x // 4, it.y // 4, it.text.lower())
            if k not in keys:
                keys.add(k)
                merged.append(it)
        title = (self.window.title or "").strip()
        merged = [i for i in merged if i.text.strip() != title]
        if not merged:
            self.last_error = "该窗口没有可读控件文本（Chromium/Electron 程序需要带 --remote-debugging-port 启动才能注入）"
        return merged

    # ---------- 写回 / 还原（仅注入通道有） ----------
    def apply(self, pairs) -> int:
        if self._inject is not None and self._inject.attached:
            try:
                return self._inject.apply(pairs)
            except Exception as e:
                self.last_error = f"注入写入失败: {e}"
        return 0

    def restore(self) -> int:
        if self._inject is not None and self._inject.attached:
            try:
                return self._inject.restore()
            except Exception:
                return 0
        return 0

    def close(self) -> None:
        if self._inject is not None:
            try:
                self._inject.close()
            except Exception:
                pass
            self._inject = None
        self._uia_root = None
        super().close()
