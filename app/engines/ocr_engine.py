"""OCR 抓取引擎。

主引擎：Windows 10/11 内置 OCR（Windows.Media.Ocr，经 winsdk 调用）——不额外下载任何模型。
可选引擎：paddleocr（若用户自行安装，自动优先使用）。

流程：mss 截取客户区 → PIL 预处理（放大/灰度/增强）→ OCR → 按行输出坐标。
"""
import asyncio
import io
import threading
from typing import List

import mss
from PIL import Image, ImageOps

from .base import BaseEngine, TextItem

ANGLES = ("en-US", "en-GB", "en")

# 窗口控制按钮的提示文字：这些按钮本身就是图标（最小化/最大化/关闭），
# 鼠标停上去会弹出 tooltip，OCR 抓到后就翻译成一块中文盖在按钮上 ——
# 除了挡住按钮没有任何阅读价值，所以整行命中就直接不翻。
BUTTON_WORDS = {
    "minimize", "minimise", "maximize", "maximise", "restore", "close",
    "min", "max", "restore down", "close window",
    "最小化", "最大化", "还原", "还原窗口", "关闭", "关闭窗口", "向下还原",
}

_PUNCT = " \t\r\n.,!?:;·-—–_()[]{}<>\"'“”‘’/\\|"


def _is_decorative(text: str) -> bool:
    """判断一行 OCR 文字是不是"纯装饰"（按钮提示、纯符号），不值得翻译。"""
    t = (text or "").strip().strip(_PUNCT).strip().lower()
    if not t:
        return True
    if t in BUTTON_WORDS:
        return True
    # 去掉符号后既没有字母也没有汉字（如 "×" "▢" "—"），没有可翻的东西
    if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in t):
        return True
    return False


def _make_ocr_engine():
    """创建英文 OCR 引擎；优先 en-US。"""
    from winsdk.windows.globalization import Language
    from winsdk.windows.media.ocr import OcrEngine

    for tag in ANGLES:
        try:
            eng = OcrEngine.try_create_from_language(Language(tag))
            if eng is not None:
                return eng
        except Exception:
            continue
    # 回退到系统已装语言
    try:
        return OcrEngine.try_create_from_user_profile_languages()
    except Exception:
        return None


async def _recognize_async(engine, png_bytes: bytes):
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png_bytes)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    return await engine.recognize_async(bitmap)


class OcrEngine(BaseEngine):
    mode = "ocr"
    display_name = "OCR 截图识别"
    description = "截取锁定窗口画面，用系统内置 OCR 识别像素文字；绝大多数自渲染画面可用。"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._local = threading.local()
        self._ocr = None
        self._paddle = None

    def _mss_instance(self):
        """mss 实例按线程隔离：截图必须在使用的那个线程里创建。"""
        inst = getattr(self._local, "mss", None)
        if inst is None:
            inst = mss.mss()
            self._local.mss = inst
        return inst

    def prepare(self, window) -> None:
        super().prepare(window)
        # 有 paddleocr 就优先用（用户自备），否则用系统 OCR
        try:
            from paddleocr import PaddleOCR  # type: ignore
            self._paddle = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
        except Exception:
            self._paddle = None

    def capture_once(self) -> List[TextItem]:
        rect = self.client_rect()
        if not rect:
            return self.fail("窗口不存在或已关闭")
        left, top, right, bottom = rect
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return self.fail("窗口尺寸为 0")

        from .. import winutil

        img = None
        # 首选：PrintWindow 直接取窗口画面（不含悬浮层，避免"翻译自己的译文"）
        try:
            shot = winutil.capture_client_rgba(self.window.hwnd)
            if shot:
                sw, sh, bgra = shot
                img = Image.frombytes("RGB", (sw, sh), bgra, "raw", "BGRX")
        except Exception:
            img = None
        # 回退：屏幕截图（部分游戏/全屏窗口 PrintWindow 返回黑图时用）
        if img is None or self._looks_black(img):
            try:
                grab = self._mss_instance().grab({"left": left, "top": top, "width": w, "height": h})
                img = Image.frombytes("RGB", grab.size, grab.bgra, "raw", "BGRX")
            except Exception as e:
                return self.fail(f"截图失败: {e}")

        scale = float(self.cfg.get("capture", "ocr_scale", 2.0) or 1.0)
        if scale != 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        gray = ImageOps.autocontrast(img.convert("L"))
        proc = gray.convert("RGB")
        min_conf = float(self.cfg.get("capture", "ocr_min_confidence", 0.55) or 0.0)

        if self._paddle is not None:
            items = self._run_paddle(proc, scale, min_conf)
        else:
            items = self._run_windows_ocr(proc, scale, min_conf)
        # 丢掉窗口按钮 tooltip 之类没意义的行（最小化/最大化/关闭、纯符号）
        return [i for i in items if not _is_decorative(i.text)]

    @staticmethod
    def _looks_black(img: Image.Image) -> bool:
        """判断截图是否几乎全黑（PrintWindow 对某些 GPU 窗口会返回黑图）。"""
        try:
            g = img.convert("L")
            hist = g.histogram()
            total = sum(hist)
            if total <= 0:
                return True
            dark = sum(hist[:24])
            return dark / total > 0.985
        except Exception:
            return False

    # --- 系统 OCR ---
    def _ocr_engine(self):
        """OCR 引擎同样按线程隔离（WinRT 对象不宜跨线程复用）。"""
        eng = getattr(self._local, "ocr", None)
        if eng is None:
            eng = _make_ocr_engine()
            self._local.ocr = eng
        return eng

    def _run_windows_ocr(self, img: Image.Image, scale: float, min_conf: float) -> List[TextItem]:
        engine = self._ocr_engine()
        if engine is None:
            return self.fail("系统 OCR 不可用：可能缺少英语语言包（设置-时间和语言-语言-添加 English）")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        try:
            result = asyncio.run(_recognize_async(engine, buf.getvalue()))
        except Exception as e:
            return self.fail(f"OCR 识别失败: {e}")
        items: List[TextItem] = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            x0 = min(w.bounding_rect.x for w in words)
            y0 = min(w.bounding_rect.y for w in words)
            x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
            y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
            text = line.text.strip()
            if not text:
                continue
            items.append(TextItem(
                x=int(x0 / scale), y=int(y0 / scale),
                w=int((x1 - x0) / scale), h=int((y1 - y0) / scale),
                text=text, conf=1.0 if line.text else min_conf,
            ))
        return items

    # --- PaddleOCR（可选） ---
    def _run_paddle(self, img: Image.Image, scale: float, min_conf: float) -> List[TextItem]:
        import numpy as np

        arr = np.array(img)
        try:
            res = self._paddle.ocr(arr, cls=False)
        except Exception as e:
            return self.fail(f"PaddleOCR 失败: {e}")
        items: List[TextItem] = []
        for group in res or []:
            for box, (text, conf) in group or []:
                if conf < min_conf or not text.strip():
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x0, x1 = min(xs) / scale, max(xs) / scale
                y0, y1 = min(ys) / scale, max(ys) / scale
                items.append(TextItem(int(x0), int(y0), int(x1 - x0), int(y1 - y0), text.strip(), float(conf)))
        return items

    def close(self) -> None:
        inst = getattr(self._local, "mss", None)
        if inst is not None:
            try:
                inst.close()
            except Exception:
                pass
            self._local.mss = None
        self._ocr = None
        self._paddle = None
        super().close()
