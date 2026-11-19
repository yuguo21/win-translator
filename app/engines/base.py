"""抓取引擎基类与公共数据结构。"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TextItem:
    """一条待翻译文本，坐标相对目标窗口客户区左上角（物理像素）。"""
    x: int
    y: int
    w: int
    h: int
    text: str
    conf: float = 1.0

    def key(self) -> str:
        return self.text.strip().lower()


class BaseEngine:
    mode: str = "base"
    display_name: str = "未实现"
    description: str = ""
    # 是否需要"能看见窗口画面"（OCR/UI/网页都要；内存扫描不需要，窗口最小化也能读）
    needs_screen: bool = True
    # 译文写到哪里：renders_in_page=写进网页；renders_in_log=只写日志（不贴图）；都不置=贴悬浮层
    renders_in_log: bool = False

    def __init__(self, cfg):
        self.cfg = cfg
        self.window = None          # type: ignore
        self.last_error: Optional[str] = None

    # --- 生命周期 ---
    def prepare(self, window) -> None:
        """绑定窗口，做模式特有的一次性初始化。"""
        self.window = window
        self.last_error = None

    def capture_once(self) -> List[TextItem]:
        raise NotImplementedError

    def close(self) -> None:
        """释放资源（解除 hook、关闭 mss 等）。"""
        self.window = None

    # --- 便捷 ---
    def fail(self, msg: str) -> List[TextItem]:
        self.last_error = msg
        return []

    def client_rect(self):
        from .. import winutil
        if not self.window:
            return None
        return winutil.get_client_rect(self.window.hwnd)
