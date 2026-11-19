"""MSAA（Microsoft Active Accessibility）读取：用 ctypes 直接调 IAccessible。

为什么需要它：
- 浏览器的网页内容是自渲染的，Win32 的 WM_GETTEXT 只返回窗口标题；
- 浏览器（Chrome/Edge）在被"无障碍客户端"请求时会激活无障碍树，
  通过 MSAA 的 IAccessible 就能读到网页里的文本、按钮名等；
- 本机 winsdk 只暴露了 WinRT 的 AutomationConnection，没有 from_handle，所以走 COM 老接口。

只实现读取需要的少数方法（vtable 下标固定）：
  3 get_accParent / 4 get_accChildCount / 5 get_accChild /
  6 get_accName / 18 accLocation
"""
import ctypes
from ctypes import POINTER, byref, c_void_p, c_long

oleacc = ctypes.WinDLL("oleacc", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

OBJID_CLIENT = 0xFFFFFFFC
COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
CHILDID_SELF = 0

# vtable 下标：IUnknown(0..2) + IDispatch(3..6) + IAccessible(7..27)
IDX_RELEASE = 2
IDX_GET_CHILD_COUNT = 8      # get_accChildCount
IDX_GET_CHILD = 9            # get_accChild
IDX_GET_NAME = 10            # get_accName
IDX_ACC_LOCATION = 22        # accLocation


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, s: str):
        super().__init__()
        parts = s.strip("{}").split("-")
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        d4 = parts[3] + parts[4]
        for i in range(8):
            self.Data4[i] = int(d4[i * 2:i * 2 + 2], 16)


IID_IAccessible = GUID("618736E0-3C3D-11CF-810C-00AA00389B71")


class VARIANT(ctypes.Structure):
    """VARIANT（x64 下 24 字节）。IAccessible 的 varChild 参数是按值传递。"""
    _fields_ = [
        ("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
        ("r3", ctypes.c_ushort), ("val", ctypes.c_longlong), ("pad2", ctypes.c_byte * 8),
    ]

    @classmethod
    def i4(cls, value: int):
        v = cls()
        v.vt = 3          # VT_I4
        v.val = int(value)
        return v


def _vtable_call(ptr: int, index: int, restype, *argtypes):
    """取 COM 对象 vtable 里第 index 个函数并调用。"""
    if not ptr:
        return None
    vt = ctypes.cast(c_void_p(ptr), POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    fn = proto(vt[index])
    return fn


class Accessible:
    """一个 IAccessible 节点的薄封装（用完必须 release）。"""

    def __init__(self, ptr: int):
        self.ptr = int(ptr)
        self._released = False

    @classmethod
    def from_window(cls, hwnd: int):
        p_acc = c_void_p()
        hr = oleacc.AccessibleObjectFromWindow(
            hwnd, OBJID_CLIENT, byref(IID_IAccessible), byref(p_acc))
        if hr != S_OK or not p_acc.value:
            return None
        return cls(p_acc.value)

    def name(self, child_id: int = CHILDID_SELF) -> str:
        fn = _vtable_call(self.ptr, IDX_GET_NAME, ctypes.HRESULT, VARIANT, POINTER(c_void_p))
        var = VARIANT.i4(child_id)
        bstr = c_void_p()
        try:
            hr = fn(c_void_p(self.ptr), var, byref(bstr))
        except Exception:
            return ""
        if hr != 0 or not bstr.value:
            return ""
        try:
            s = ctypes.wstring_at(bstr.value) or ""
        except Exception:
            s = ""
        try:
            ole32.SysFreeString(bstr)
        except Exception:
            pass
        return s.strip()

    def child_count(self) -> int:
        fn = _vtable_call(self.ptr, IDX_GET_CHILD_COUNT, ctypes.HRESULT, POINTER(c_long))
        n = c_long(0)
        try:
            hr = fn(c_void_p(self.ptr), byref(n))
        except Exception:
            return 0
        return int(n.value) if hr == 0 else 0

    def child(self, index: int):
        fn = _vtable_call(self.ptr, IDX_GET_CHILD, ctypes.HRESULT, VARIANT, POINTER(c_void_p))
        var = VARIANT.i4(index)
        p = c_void_p()
        try:
            hr = fn(c_void_p(self.ptr), var, byref(p))
        except Exception:
            return None
        if hr != 0 or not p.value:
            return None
        return Accessible(p.value)

    def location(self, child_id: int = CHILDID_SELF):
        fn = _vtable_call(self.ptr, IDX_ACC_LOCATION, ctypes.HRESULT,
                          POINTER(c_long), POINTER(c_long), POINTER(c_long), POINTER(c_long),
                          VARIANT)
        x, y, w, h = c_long(0), c_long(0), c_long(0), c_long(0)
        var = VARIANT.i4(child_id)
        try:
            hr = fn(c_void_p(self.ptr), byref(x), byref(y), byref(w), byref(h), var)
        except Exception:
            return None
        if hr != 0:
            return None
        return int(x.value), int(y.value), int(w.value), int(h.value)

    def release(self) -> None:
        if self._released or not self.ptr:
            return
        fn = _vtable_call(self.ptr, IDX_RELEASE, c_long)
        try:
            fn(c_void_p(self.ptr))
        except Exception:
            pass
        self._released = True


def co_initialize() -> None:
    try:
        ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    except Exception:
        pass


def read_window_texts(hwnd: int, max_items: int = 300, max_depth: int = 10):
    """返回 [(text, x, y, w, h)]（屏幕坐标），失败返回 []。

    MSAA 的树有两种子节点：
    - 简单元素：没有自己的 IAccessible，用**父对象的 child id** 读 name/location；
    - 完整对象：get_accChild 返回新的 IAccessible，可继续往下递归。
    两种都要处理，否则浏览器里大量节点会读成空。
    浏览器要传**内部渲染子窗口**的 hwnd（见 winutil.find_render_widget）。
    """
    co_initialize()
    root = Accessible.from_window(hwnd)
    if root is None:
        return []
    out = []
    seen_boxes = set()

    def add(text: str, loc):
        if not text or not loc:
            return False
        x, y, w, h = loc
        if w <= 2 or h <= 2:
            return False
        box = (x, y, w, h)
        if box in seen_boxes:
            return False
        seen_boxes.add(box)
        out.append((text, x, y, w, h))
        return True

    def walk(node: "Accessible", depth: int):
        if len(out) >= max_items or depth > max_depth:
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
            # 路径 A：把子节点当成"简单元素"，用父对象 + child id 读
            try:
                name = node.name(i)
            except Exception:
                name = ""
            loc = None
            if name:
                try:
                    loc = node.location(i)
                except Exception:
                    loc = None
            if name and add(name, loc):
                # 简单元素不会有自己的子节点；但完整对象也可能在此命中，
                # 所以继续尝试路径 B 来往下钻（命中过的不再重复记录）
                pass
            # 路径 B：子节点是完整对象，拿到它自己的 IAccessible 再递归
            try:
                child = node.child(i)
            except Exception:
                child = None
            if child is None:
                continue
            try:
                cname = child.name()
                if cname:
                    try:
                        cloc = child.location()
                    except Exception:
                        cloc = None
                    add(cname, cloc)
                walk(child, depth + 1)
            finally:
                child.release()

    try:
        walk(root, 0)
    finally:
        root.release()
    return out
