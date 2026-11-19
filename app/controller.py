"""抓取循环 + 翻译 + 渲染调度（单后台线程）。

流程：定时 capture_once → 过滤短文本/上轮已显示 → 批量翻译 → 回主线程渲染。
窗口最小化或长时间不可见时暂停抓取。
"""
import threading
import time
from typing import List, Optional

from . import winutil
from .engines.base import TextItem


class Controller:
    def __init__(self, cfg, translator, overlay, on_log=None, on_status=None):
        self.cfg = cfg
        self.translator = translator
        self.overlay = overlay
        self.on_log = on_log or (lambda msg: None)
        self.on_status = on_status or (lambda msg: None)
        self.window = None
        self.engine = None
        self._thread: Optional[threading] = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._shown: dict = {}
        self.last_error = None
        self._empty_rounds = 0
        # 读不到文本时**不自动**切 OCR：模式由用户自己选（config: capture.auto_fallback 才开）
        try:
            self.auto_fallback = bool(cfg.get("capture", "auto_fallback", False))
        except Exception:
            self.auto_fallback = False

    # ---------- 生命周期 ----------
    def attach_window(self, window) -> None:
        self.window = window

    def start(self, engine) -> None:
        self.stop()
        self.engine = engine
        self._stop.clear()
        self._pause.clear()
        self._shown.clear()
        self._thread = threading.Thread(target=self._loop, name="capture-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:
                pass
            self.engine = None
        self.on_status("已停止")

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    # ---------- 主循环 ----------
    def _loop(self) -> None:
        interval = float(self.cfg.get("capture", "interval_ms", 700) or 700) / 1000.0
        min_len = int(self.cfg.get("capture", "min_text_len", 3) or 3)
        max_new = int(self.cfg.get("capture", "max_new_per_round", 12) or 12)
        while not self._stop.is_set():
            try:
                in_page = bool(getattr(self.engine, "renders_in_page", False)) if self.engine else False
                # 网页模式是"鼠标停在哪翻哪"，轮询要跟得上鼠标，间隔取更短
                if in_page:
                    interval = float(self.cfg.get("capture", "web_poll_ms", 250) or 250) / 1000.0
                if self._pause.is_set():
                    time.sleep(interval)
                    continue
                # 网页模式走浏览器调试通道，不依赖窗口是否在前台/最小化
                if not in_page:
                    # 没锁窗口的 UI 模式（按鼠标自动跟随）允许 window 为空，交给引擎自己跟随
                    allow_no_window = bool(getattr(self.engine, "auto_follow", False)) if self.engine else False
                    if not self.window:
                        if not allow_no_window:
                            time.sleep(interval)
                            continue
                    elif not winutil.is_window(self.window.hwnd):
                        self.on_status("窗口已关闭，等待重新锁定")
                        self.overlay_call(lambda: self.overlay.hide())
                        time.sleep(interval)
                        continue
                    elif winutil.is_minimized(self.window.hwnd):
                        if getattr(self.engine, "needs_screen", True):
                            self.on_status("窗口已最小化，暂停捕获")
                            self.overlay_call(lambda: self.overlay.hide())
                            time.sleep(interval)
                            continue
                        # 内存扫描不需要画面：最小化也继续读，只是没有地方贴译文
                        self.overlay_call(lambda: self.overlay.hide())
                    else:
                        rect = winutil.get_client_rect(self.window.hwnd)
                        if rect:
                            def _place(r=rect):
                                self.overlay.show()      # 之前可能因最小化被藏起来过
                                self.overlay.place_over(r)
                            self.overlay_call(_place)

                items: List[TextItem] = self.engine.capture_once() if self.engine else []
                if not items:
                    err = getattr(self.engine, "last_error", None) if self.engine else None
                    if err and err != self.last_error:
                        self.last_error = err
                        self.on_log(f"[{self.engine.mode}] {err}")
                    self._empty_rounds += 1
                    # 自动降级规则：
                    # - 网页模式：只在拿不到浏览器调试通道 / 没有网页标签时降级
                    # - UI / 内存 / 图形模式：连续 3 轮读不到内容（浏览器、自渲染画面）就降级
                    hard = bool(err) and ("浏览器调试端口" in err or "可用的网页标签" in err
                                          or "连接浏览器失败" in err)
                    if self.auto_fallback and self.engine is not None and self.engine.mode != "ocr":
                        if (self.engine.mode == "web" and hard) or \
                                (self.engine.mode != "web" and self._empty_rounds >= 3):
                            self._switch_to_ocr()
                            continue
                    time.sleep(interval)
                    continue
                self._empty_rounds = 0
                self.last_error = None

                fresh = self._filter(items, min_len, max_new, dedupe=not in_page, loose=in_page)
                if not fresh:
                    time.sleep(interval)
                    continue

                lines = [i.text for i in fresh]
                # 来源决定翻完记到哪：网页只进 sqlite 缓存，界面模式短词条进 tm.json
                mode = getattr(self.engine, "mode", "uia") if self.engine else "uia"
                source = "web" if in_page else mode
                try:
                    result = self.translator.translate_lines(lines, source=source)
                except Exception as e:
                    self.on_log(f"翻译失败: {e}")
                    time.sleep(interval)
                    continue

                pairs = [(i, result.get(i.text)) for i in fresh if result.get(i.text)]
                if pairs:
                    # 网页模式：直接写进网页正文；内存模式：只写日志不贴图；其他模式：悬浮层贴图
                    if getattr(self.engine, "renders_in_page", False):
                        n = self.engine.apply(pairs)
                        self.on_status(f"已写入网页 {n} 条（web）")
                    elif getattr(self.engine, "renders_in_log", False):
                        for item, text in pairs:
                            self.on_log(f"[译] {item.text}  →  {text}")
                        self.on_status(f"本轮 {len(pairs)} 条（写入日志，不贴图）")
                    else:
                        cover = self.engine.mode == "web"
                        self.overlay_call(lambda p=pairs, c=cover: self.overlay.draw(p, cover=c))
                        self.on_status(f"本轮 {len(pairs)} 条（{self.engine.mode}）")
                time.sleep(interval)
            except Exception as e:
                self.on_log(f"循环异常: {e}")
                time.sleep(max(1.0, interval))

    def _switch_to_ocr(self) -> None:
        """把当前引擎换成 OCR（保留窗口绑定，用户无需手动切换）。"""
        try:
            from . import engines
            old = self.engine
            new = engines.create_engine("ocr", self.cfg)
            new.prepare(self.window)
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            self.engine = new
            self._empty_rounds = 0
            self.on_log(f"{old.mode if old else '当前模式'} 读不到文本，已自动切换为 OCR 模式")
            self.on_status("已自动切换到 OCR 模式")
        except Exception as e:
            self.on_log(f"自动切换 OCR 失败: {e}")
            self.engine = self.engine

    def _filter(self, items, min_len: int, max_new: int, dedupe: bool = True,
                loose: bool = False) -> List[TextItem]:
        now = time.time()
        out = []
        ttl = 30.0
        for it in items:
            text = (it.text or "").strip()
            if len(text) < (2 if loose else min_len):
                continue
            # 已经是中文（自己的译文被误抓回来）直接丢弃，避免自我循环
            en = sum(1 for ch in text if ch.isascii() and ch.isalpha())
            cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
            if loose:
                # 网页悬停：只要求还有英文，且中文不比英文多（中英混排的段落也能翻）
                if en < 2 or cjk > en:
                    continue
            else:
                if cjk > 0:
                    continue
                letters = sum(ch.isalpha() for ch in text)
                if letters < 2 or letters / max(1, len(text)) < 0.45:
                    continue
            if dedupe:
                k = text.lower()
                last = self._shown.get(k)
                if last and now - last < ttl:
                    continue
                self._shown[k] = now
            out.append(it)
        # 老记录清理
        if len(self._shown) > 400:
            self._shown = {k: v for k, v in self._shown.items() if now - v < ttl}
        return out[:max_new]

    def overlay_call(self, fn) -> None:
        """把 UI 操作转交给主线程执行（tkinter 线程安全）。"""
        try:
            self.overlay.root.after(0, fn)
        except Exception:
            try:
                fn()
            except Exception:
                pass
