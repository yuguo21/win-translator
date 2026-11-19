"""图形 API Hook 抓取引擎（占位架构，需外部 DLL 才能工作）。

为什么不能纯 Python 实现：
- Hook D3D9/11 的 Present / DrawText 或 Unity 的 TMP 绘制调用，要把自己的代码写进目标进程，
  必须注入一个 DLL（C++），Python 无法直接在被注入的进程里执行。
- 因此本项目预留了"外部 Hook 模块"接口：一旦有 DLL（自带共享内存/命名管道把截获的字符串与坐标
  回传给本进程），把 dll 路径与通道名填进 config.json -> gfx，本引擎即可接管。

当前状态：未注入（gfx.dll 不存在时明确报错，不影响其他三种模式）。
"""
import ctypes
import json
from typing import List

from .. import winutil
from .base import BaseEngine, TextItem

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GfxEngine(BaseEngine):
    mode = "gfx"
    display_name = "图形 API Hook（需 DLL）"
    description = "拦截 D3D9/D3D11/OpenGL 的文本绘制调用，直接拿到原文；需要配套注入 DLL。"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._dll = None

    def prepare(self, window) -> None:
        super().prepare(window)
        path = self.cfg.get("gfx", "dll_path", "") or ""
        if not path:
            raise RuntimeError("未配置 gfx.dll_path：图形 Hook 需要配套 DLL 才能工作（详见 README）")
        try:
            self._dll = ctypes.WinDLL(path)
        except Exception as e:
            raise RuntimeError(f"加载 Hook DLL 失败: {e}")
        hook_fn = getattr(self._dll, "StartHook", None)
        if hook_fn is None:
            raise RuntimeError("Hook DLL 未导出 StartHook(pid) 函数")
        hook_fn.argtypes = [ctypes.c_int]
        hook_fn.restype = ctypes.c_int
        rc = hook_fn(window.pid)
        if rc != 0:
            raise RuntimeError(f"StartHook 返回失败码 {rc}")

    def capture_once(self) -> List[TextItem]:
        if not self.window or not winutil.is_window(self.window.hwnd):
            return self.fail("窗口不存在或已关闭")
        if self._dll is None:
            return self.fail("图形 Hook 未启动")
        poll = getattr(self._dll, "PollText", None)
        if poll is None:
            return self.fail("Hook DLL 未导出 PollText()")
        poll.restype = ctypes.c_char_p
        raw = poll()
        if not raw:
            return []
        try:
            payload = json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            return []
        items: List[TextItem] = []
        for it in payload.get("items", []):
            try:
                items.append(TextItem(
                    int(it.get("x", 0)), int(it.get("y", 0)),
                    int(it.get("w", 0)), int(it.get("h", 0)),
                    str(it.get("text", "")).strip(), float(it.get("conf", 1.0)),
                ))
            except Exception:
                continue
        return [i for i in items if i.text]

    def close(self) -> None:
        if self._dll is not None:
            stop = getattr(self._dll, "StopHook", None)
            if stop is not None:
                try:
                    stop()
                except Exception:
                    pass
            self._dll = None
        super().close()
