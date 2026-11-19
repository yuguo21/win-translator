"""主界面：窗口选择与锁定、模式切换、运行控制、状态与日志。

界面用 tkinter（标准库，无需额外下载）。布局：
  左侧：窗口列表 + 刷新/锁定/鼠标拾取
  右上：模式选择（4 选 1）+ 开始/停止 + 设置
  右下：状态与日志
"""
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from . import engines, winutil
from .config import Config
from .controller import Controller
from .overlay import Overlay
from .translator import Translator

LOG_MAX_LINES = 300
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class App:
    def __init__(self, work_dir: str):
        self.work_dir = work_dir
        self.cfg = Config(os.path.join(work_dir, "config.json"))
        self.translator = Translator(self.cfg, work_dir)
        self.root = tk.Tk()
        self.root.title("游戏实时翻译工具")
        self.root.geometry("980x620")
        self.root.minsize(860, 520)

        self._windows = []
        self._lock_window = None      # 已锁定的 WindowInfo
        self._engine = None
        self._picking = False

        key = self.cfg.get("overlay", "transparent_key", "#010203") or "#010203"
        self.overlay = Overlay(self.root, self.cfg, key)
        self.overlay.hide()
        self.controller = Controller(self.cfg, self.translator, self.overlay,
                                     on_log=self.log, on_status=self.set_status)

        self._build_ui()
        self.refresh_windows()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        # 左：窗口列表
        left = ttk.LabelFrame(main, text="目标窗口", padding=6)
        left.pack(side="left", fill="both", expand=False)

        self.tree = ttk.Treeview(left, columns=("title", "proc"), show="headings", height=18)
        self.tree.heading("title", text="窗口标题")
        self.tree.heading("proc", text="进程")
        self.tree.column("title", width=260)
        self.tree.column("proc", width=150)
        self.tree.pack(side="top", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())

        btns = ttk.Frame(left)
        btns.pack(side="top", fill="x", pady=(6, 0))
        ttk.Button(btns, text="刷新列表", command=self.refresh_windows).pack(side="left")
        ttk.Button(btns, text="锁定选中窗口", command=self.lock_selected).pack(side="left", padx=4)
        ttk.Button(btns, text="鼠标拾取", command=self.start_pick).pack(side="left")

        self.lab_lock = ttk.Label(left, text="未锁定", foreground="#666")
        self.lab_lock.pack(side="top", anchor="w", pady=(6, 0))

        # 右：控制区
        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        mode_box = ttk.LabelFrame(right, text="捕获模式", padding=6)
        mode_box.pack(fill="x")
        self.mode_var = tk.StringVar(value=self.cfg.get("capture", "mode", "ocr") or "ocr")
        for m in engines.mode_list():
            rb = ttk.Radiobutton(mode_box, text=f"{m['name']}", value=m["mode"],
                                 variable=self.mode_var, command=self.on_mode_change)
            rb.pack(anchor="w")
            ttk.Label(mode_box, text=f"    {m['desc']}", foreground="#777",
                      wraplength=560, justify="left").pack(anchor="w")

        ctrl = ttk.Frame(right)
        ctrl.pack(fill="x", pady=8)
        self.btn_start = ttk.Button(ctrl, text="▶ 开始翻译", command=self.toggle_run)
        self.btn_start.pack(side="left")
        ttk.Button(ctrl, text="启动翻译浏览器", command=self.launch_web_browser).pack(side="left", padx=6)
        ttk.Button(ctrl, text="清空缓存", command=self.clear_cache).pack(side="left")
        ttk.Button(ctrl, text="设置", command=self.open_settings).pack(side="left", padx=6)

        # 显示参数：字号 + 字色，改完立即重画
        disp = ttk.Frame(right)
        disp.pack(fill="x", pady=(0, 6))
        ttk.Label(disp, text="译文字号").pack(side="left")
        self.font_var = tk.StringVar(value=str(self.cfg.get("overlay", "font_size", 12)))
        self.spin_font = ttk.Spinbox(disp, from_=8, to=48, width=4,
                                     textvariable=self.font_var, command=self.on_font_change)
        self.spin_font.pack(side="left", padx=(4, 0))
        self.spin_font.bind("<Return>", lambda e: self.on_font_change())
        self.spin_font.bind("<FocusOut>", lambda e: self.on_font_change())
        ttk.Label(disp, text="字色").pack(side="left", padx=(14, 0))
        self.color_var = tk.StringVar(value=self.cfg.get("overlay", "text_color", "#FFE066"))
        ent_color = ttk.Entry(disp, textvariable=self.color_var, width=10)
        ent_color.pack(side="left", padx=(4, 0))
        ent_color.bind("<Return>", lambda e: self.on_font_change())
        ttk.Button(disp, text="应用", command=self.on_font_change).pack(side="left", padx=6)
        self.lab_mode_hint = ttk.Label(right, text="模式提示：网页翻译=鼠标停在哪个段落就翻哪一段，中文直接写进网页；OCR 模式才用贴图悬浮层。",
                                       foreground="#777", wraplength=700, justify="left")
        self.lab_mode_hint.pack(anchor="w", pady=(0, 6))

        self.lab_status = ttk.Label(right, text="就绪", foreground="#0a7")
        self.lab_status.pack(anchor="w")

        log_box = ttk.LabelFrame(right, text="日志", padding=4)
        log_box.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_box, height=12, wrap="word", state="disabled",
                                bg="#111", fg="#ddd", insertbackground="#ddd")
        sb = ttk.Scrollbar(log_box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    # ---------------- 日志/状态 ----------------
    def log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self._log_to_file(line)

        def _append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line)
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        try:
            self.root.after(0, _append)
        except Exception:
            pass

    def _log_to_file(self, line: str) -> None:
        """同时把日志写进 logs\\ 目录的当天文件（内存模式的译文就落在这里，关掉界面也留得住）。"""
        try:
            log_dir = os.path.join(BASE_DIR, "logs")
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(log_dir, time.strftime("translate-%Y%m%d.log"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def set_status(self, msg: str) -> None:
        try:
            self.root.after(0, lambda: self.lab_status.configure(text=msg))
        except Exception:
            pass

    # ---------------- 窗口列表 ----------------
    def refresh_windows(self) -> None:
        exclude = {int(self.overlay.root.winfo_id()), int(self.root.winfo_id())}
        self._windows = winutil.list_windows(exclude_hwnds=exclude)
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for w in self._windows:
            self.tree.insert("", "end", iid=str(w.hwnd), values=(w.title, f"{w.process} ({w.pid})"))
        self.log(f"窗口列表已刷新，共 {len(self._windows)} 个可见窗口")

    def _selected_window(self):
        sel = self.tree.selection()
        if not sel:
            return None
        hwnd = int(sel[0])
        for w in self._windows:
            if w.hwnd == hwnd:
                return w
        return None

    def _on_select(self):
        w = self._selected_window()
        if w:
            self.lab_lock.configure(text=f"待锁定：{w.label()}", foreground="#b60")

    def lock_selected(self) -> None:
        w = self._selected_window()
        if not w:
            messagebox.showinfo("提示", "请先在列表里选择一个窗口")
            return
        self.do_lock(w)

    def do_lock(self, w) -> None:
        if self._running():
            self.stop_run()
        self._lock_window = w
        self.controller.attach_window(w)
        self.lab_lock.configure(text=f"已锁定：{w.label()}", foreground="#070")
        self.log(f"锁定窗口: {w.title} [{w.process}:{w.pid}] hwnd={w.hwnd}")
        rect = winutil.get_client_rect(w.hwnd)
        if rect:
            self.overlay.place_over(rect)
        self._auto_pick_mode(w)
        self.set_status("窗口已锁定，选择模式后点开始")

    BROWSER_PROCESSES = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe",
                         "opera.exe", "vivaldi.exe", "doubao.exe"}

    def _auto_pick_mode(self, w) -> None:
        """锁定的如果是浏览器/Electron 应用，自动切到"网页翻译"模式。"""
        proc = (w.process or "").lower()
        if proc in self.BROWSER_PROCESSES and self.mode_var.get() != "web":
            self.mode_var.set("web")
            self.cfg.set("capture", "mode", "web")
            self.log("检测到浏览器窗口，已自动选择「网页翻译」模式（最快、最准）")

    def start_pick(self) -> None:
        if self._picking:
            return
        self._picking = True
        self.log("鼠标拾取：3 秒后取光标下的窗口…把鼠标移到目标窗口上")
        self.root.after(3000, self._finish_pick)

    def _finish_pick(self) -> None:
        self._picking = False
        hwnd = winutil.get_window_hwnd_under_cursor()
        if not hwnd:
            self.log("拾取失败：光标下没有窗口")
            return
        exclude = {int(self.overlay.root.winfo_id()), int(self.root.winfo_id())}
        if hwnd in exclude:
            self.log("拾取到的是本工具自身窗口，请重试")
            return
        title = ""
        try:
            import ctypes
            import ctypes.wintypes as wt
            n = ctypes.WinDLL("user32").GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 2)
            ctypes.WinDLL("user32").GetWindowTextW(hwnd, buf, n + 2)
            title = buf.value
        except Exception:
            pass
        import ctypes
        import ctypes.wintypes as wt
        pid = wt.DWORD(0)
        ctypes.WinDLL("user32").GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        from .winutil import WindowInfo
        w = WindowInfo(hwnd, title or "(无标题)", int(pid.value), winutil.get_process_name(int(pid.value)))
        self.do_lock(w)

    # ---------------- 模式 ----------------
    def on_mode_change(self) -> None:
        mode = self.mode_var.get()
        self.cfg.set("capture", "mode", mode)
        self.log(f"捕获模式切换为 {mode}")
        if self._running():
            self.log("模式已改变，请重新点开始以重启抓取线程")

    def _running(self) -> bool:
        return self.controller._thread is not None and self.controller._thread.is_alive()

    # ---------------- 运行 ----------------
    def toggle_run(self) -> None:
        if self._running():
            self.stop_run()
        else:
            self.start_run()

    def start_run(self) -> None:
        mode = self.mode_var.get()
        # 网页模式自己开浏览器；Windows UI 模式在悬停注入时按鼠标自动跟随，都不用先锁窗口
        if mode not in ("web", "uia") and not self._lock_window:
            messagebox.showinfo("提示", "请先锁定一个目标窗口")
            return
        try:
            eng = engines.create_engine(mode, self.cfg)
            eng.prepare(self._lock_window)
        except Exception as e:
            self.log(f"启动 {mode} 失败: {e}")
            messagebox.showwarning("启动失败", f"{mode} 模式无法启动：\n{e}")
            return
        # 网页模式有明确的结构性错误（没有调试端口）时直接提示，不启动
        err = getattr(eng, "last_error", None)
        if mode == "web" and err and getattr(eng, "renders_in_page", False) and eng._page is None:
            self.log(f"网页模式未就绪：{err}")
            if messagebox.askyesno("网页模式需要浏览器调试通道", f"{err}\n\n现在帮您启动翻译浏览器吗？"):
                self.launch_web_browser()
            return
        self._engine = eng
        self.controller.attach_window(self._lock_window)
        self.controller.start(eng)
        if (getattr(eng, "renders_in_page", False) or getattr(eng, "auto_follow", False)
                or getattr(eng, "renders_in_log", False)):
            self.overlay.hide()          # 网页模式 / 注入模式 / 内存模式都不贴图
            if getattr(eng, "renders_in_log", False):
                self.log("译文不贴图，逐条写进下方日志（同时存到 logs\\translate-*.log）")
        else:
            self.overlay.show()
            minimized = bool(getattr(self._lock_window, "minimized", False)) if self._lock_window else False
            rect = winutil.get_client_rect(self._lock_window.hwnd) if self._lock_window else None
            if minimized:
                # 最小化窗口的客户区坐标是假的（-32000 之类），悬浮层先不贴出来
                self.overlay.hide()
                if getattr(eng, "needs_screen", True):
                    self.log("窗口处于最小化：还原它才会贴译文")
                else:
                    self.log("窗口最小化中，内存扫描照常进行（译文等窗口还原后显示）")
            elif rect:
                self.overlay.place_over(rect)
        self.btn_start.configure(text="■ 停止")
        self.set_status(f"运行中（{mode}）")
        self.log(f"已启动 {eng.display_name}")

    def stop_run(self) -> None:
        # 停止前先把网页还原成原文，避免留一堆中文在里面
        eng = self._engine
        if eng is not None and getattr(eng, "renders_in_page", False):
            try:
                n = eng.restore()
                if n:
                    self.log(f"已把网页还原为原文（{n} 条）")
            except Exception:
                pass
        self.controller.stop()
        self._engine = None
        self.overlay.clear()
        self.overlay.hide()
        self.btn_start.configure(text="▶ 开始翻译")
        self.set_status("已停止")

    # ---------------- 网页模式：启动翻译浏览器 ----------------
    def launch_web_browser(self) -> None:
        """用独立配置目录启动一个带调试端口的 Edge（不动用户现有浏览器）。"""
        import subprocess
        from . import cdp

        port = cdp.find_port()
        if port:
            self.log(f"已有调试端口 {port}，直接复用")
            self.set_status(f"网页模式可用（端口 {port}）")
            return
        exes = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        exe = next((p for p in exes if os.path.isfile(p)), None)
        if not exe:
            messagebox.showwarning("未找到浏览器", "没有找到 Edge 或 Chrome，无法启动翻译浏览器。")
            return
        profile = os.path.join(BASE_DIR, "browser_profile")
        os.makedirs(profile, exist_ok=True)
        args = [
            exe,
            "--remote-debugging-port=9222",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        try:
            subprocess.Popen(args, close_fds=True)
            self.log(f"已启动翻译浏览器（独立配置目录：{profile}）")
            self.log("请在刚打开的那个浏览器窗口里正常浏览网页，本工具会把译文直接写进网页。")
            self.set_status("翻译浏览器已启动，等它开好后点开始")
        except Exception as e:
            self.log(f"启动浏览器失败: {e}")
            messagebox.showwarning("启动失败", str(e))

    def clear_cache(self) -> None:
        n = self.translator.clear_cache()
        self.log(f"短期缓存已清空（{n} 条）；永久词库保留 {len(self.translator.tm)} 条（tm.json）")

    def on_font_change(self) -> None:
        """字号 / 字色改动：写入配置并立即重画已有译文。"""
        try:
            size = int(float(self.font_var.get()))
        except Exception:
            size = int(self.cfg.get("overlay", "font_size", 12))
        size = max(8, min(48, size))
        self.font_var.set(str(size))
        color = (self.color_var.get() or "#FFE066").strip()
        if not (color.startswith("#") and len(color) in (7, 4)):
            color = self.cfg.get("overlay", "text_color", "#FFE066")
            self.color_var.set(color)
        self.cfg.set("overlay", "font_size", size)
        self.cfg.set("overlay", "text_color", color)
        try:
            self.overlay.refresh()
        except Exception:
            pass
        self.log(f"译文字号 {size}，颜色 {color}（已立即生效）")

    # ---------------- 设置 ----------------
    def open_settings(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.geometry("420x320")
        win.transient(self.root)

        fields = [
            ("字号", "overlay", "font_size", self.cfg.get("overlay", "font_size", 14)),
            ("文字颜色", "overlay", "text_color", self.cfg.get("overlay", "text_color", "#FFE066")),
            ("捕获间隔(ms)", "capture", "interval_ms", self.cfg.get("capture", "interval_ms", 700)),
            ("最短文本长度", "capture", "min_text_len", self.cfg.get("capture", "min_text_len", 3)),
            ("OCR 置信度", "capture", "ocr_min_confidence", self.cfg.get("capture", "ocr_min_confidence", 0.55)),
            ("缓存秒数", "translator", "cache_seconds", self.cfg.get("translator", "cache_seconds", 1800)),
        ]
        entries = {}
        for row, (label, sec, key, val) in enumerate(fields):
            ttk.Label(win, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            var = tk.StringVar(value=str(val))
            e = ttk.Entry(win, textvariable=var, width=18)
            e.grid(row=row, column=1, padx=8, pady=4)
            entries[(sec, key)] = (var, type(val))

        def save():
            for (sec, key), (var, typ) in entries.items():
                raw = var.get().strip()
                try:
                    value = int(raw) if typ is int else (float(raw) if typ is float else raw)
                except Exception:
                    self.log(f"设置项 {key} 的值无效，已跳过")
                    continue
                self.cfg.set(sec, key, value)
            self.log("设置已保存（字号/间隔等对下一轮生效）")
            win.destroy()

        ttk.Button(win, text="保存", command=save).grid(row=len(fields), column=0, columnspan=2, pady=10)

    # ---------------- 生命周期 ----------------
    def run(self) -> None:
        self.root.mainloop()

    def on_close(self) -> None:
        try:
            self.stop_run()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
