"""悬浮渲染层：透明置顶、鼠标穿透、跟随锁定窗口。

实现要点（Windows）：
- tkinter Toplevel + overrideredirect(True) + -topmost True
- wm_attributes("-transparentcolor", key) 让底色透明，只留文字
- ctypes 加 WS_EX_LAYERED | WS_EX_TRANSPARENT，实现鼠标点击穿透
- 译文位置：默认放在原文右侧空白处；放不下就换下方/上方，全部失败则覆盖在原文上
"""
import tkinter as tk
from typing import Dict, List, Optional

from . import winutil

PAD = 4


class Overlay:
    def __init__(self, master: tk.Tk, cfg, transparent_key: str):
        self.cfg = cfg
        self.root = tk.Toplevel(master)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", transparent_key)
        self.root.configure(bg=transparent_key)
        self.canvas = tk.Canvas(self.root, bg=transparent_key, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._tags: List[str] = []
        self._last_items = []
        self._last_cover = None
        self._seq = 0
        self._hwnd = None
        self._geo = None

    def _ensure_click_through(self) -> None:
        if self._hwnd is None:
            self.root.update_idletasks()
            self._hwnd = winutil.toplevel_hwnd(int(self.root.winfo_id()))
            winutil.set_click_through(self._hwnd)

    def place_over(self, rect) -> None:
        """把悬浮层对齐到目标窗口客户区；rect=(left, top, right, bottom)。"""
        if not rect:
            return
        left, top, right, bottom = rect
        w = max(1, right - left)
        h = max(1, bottom - top)
        geo = f"{w}x{h}+{left}+{top}"
        if geo != self._geo:
            self.root.geometry(geo)
            self._geo = geo
        self._ensure_click_through()

    def clear(self) -> None:
        for tag in self._tags:
            try:
                self.canvas.delete(tag)
            except Exception:
                pass
        self._tags.clear()

    def _is_blank(self, box) -> bool:
        """简单空白判定：该区域没有已绘制的译文标签即视为空白。"""
        x0, y0, x1, y1 = box
        for tag in self._tags:
            try:
                b = self.canvas.bbox(tag)
            except Exception:
                continue
            if not b:
                continue
            if not (b[2] < x0 or b[0] > x1 or b[3] < y0 or b[1] > y1):
                return False
        return True

    def draw(self, items, cover: Optional[bool] = None) -> None:
        """items: [(TextItem, 译文)]

        cover=True 时译文直接压在原文行上（网页翻译推荐：读起来像中文网页）；
        默认取 config.json 的 `overlay.cover_original`。
        """
        self._last_items = list(items)
        self._last_cover = cover
        self.clear()
        if cover is None:
            cover = bool(self.cfg.get("overlay", "cover_original", False))
        font_size = int(self.cfg.get("overlay", "font_size", 14) or 14)
        family = self.cfg.get("overlay", "font_family", "Microsoft YaHei") or "Microsoft YaHei"
        fg = self.cfg.get("overlay", "text_color", "#FFE066") or "#FFE066"
        bg = self.cfg.get("overlay", "box_color", "#101010") or "#101010"
        auto_place = bool(self.cfg.get("overlay", "auto_place", True))
        font = (family, font_size)

        W = self.canvas.winfo_width() or 1
        H = self.canvas.winfo_height() or 1
        # 原文所在区域视为障碍：译文不覆盖原文
        obstacles = [(it.x - 2, it.y - 2, it.x + it.w + 2, it.y + it.h + 2) for it, _ in items]
        for item, trans in items:
            if not trans:
                continue
            self._seq += 1
            tag = f"t{self._seq}"
            # 先量不换行时的尺寸
            tid = self.canvas.create_text(
                item.x, item.y, text=trans, anchor="nw", font=font, fill=fg, width=0)
            bbox = self.canvas.bbox(tid)
            if not bbox:
                self.canvas.delete(tid)
                continue
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            # 覆盖模式：原文够宽且译文更宽时，按原文宽度换行（短导航项不换行）
            if cover and item.w >= 160 and tw > item.w:
                self.canvas.itemconfigure(tid, width=item.w - 8)
                bbox = self.canvas.bbox(tid) or bbox
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            if cover:
                # 覆盖式：直接用译文遮住原文那一行，宽度跟着原文走，太窄就允许横向撑开
                pos = (item.x, item.y + max(0, (item.h - th) // 2))
                bw, bh = max(item.w, tw + 4), max(item.h, th + 2)
            else:
                pos = (item.x, item.y + item.h + 2)
                bw, bh = tw + 4, th + 2
                if auto_place:
                    pos = self._place(item, tw, th, W, H, obstacles)
            self.canvas.coords(tid, pos[0] + 2, pos[1] + 1)
            self.canvas.create_rectangle(
                pos[0] - 2, pos[1] - 1, pos[0] + bw - 2, pos[1] + bh - 1,
                fill=bg, outline=bg, tags=(tag + "_box",))
            self.canvas.tag_lower(tag + "_box", tid)
            try:
                self.canvas.addtag_withtag(tag, tid)
            except Exception:
                pass
            self._tags.append(tid)
            self._tags.append(tag + "_box")

    def _place(self, item, tw: int, th: int, W: int, H: int, obstacles=None):
        """候选位置：右 → 下 → 上 → 右下 → 更下一行 → 左侧。

        约束：不越出窗口；不覆盖原文（obstacles）；不与已画的译文重叠。
        """
        obstacles = obstacles or []

        def hit_obstacle(x, y):
            for (ox0, oy0, ox1, oy1) in obstacles:
                if not (x + tw < ox0 or x > ox1 or y + th < oy0 or y > oy1):
                    return True
            return False

        cands = [
            (item.x + item.w + 6, item.y),
            (item.x, item.y + item.h + 4),
            (item.x, item.y - th - 4),
            (item.x + item.w + 6, item.y + item.h + 4),
            (item.x, item.y + item.h + th + 8),
            (item.x - tw - 6, item.y),
        ]
        for x, y in cands:
            if x < 0 or y < 0 or x + tw > W or y + th > H:
                continue
            if hit_obstacle(x, y):
                continue
            if self._is_blank((x, y, x + tw, y + th)):
                return x, y
        # 全部失败：退到原文正下方（可能压住原文，属最后手段）
        return item.x, item.y + item.h + 2

    def refresh(self) -> None:
        """按当前配置重画上一次的内容（改字号/颜色后立即生效）。"""
        if self._last_items:
            self.draw(self._last_items, getattr(self, "_last_cover", None))

    def hide(self) -> None:
        try:
            self.root.withdraw()
        except Exception:
            pass

    def show(self) -> None:
        try:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        except Exception:
            pass
