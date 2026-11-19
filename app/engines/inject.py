"""注入通道：给 Electron/Chromium 类程序（VS Code、Discord、各种套壳应用）
提供"读 DOM 文本 + 把译文写回 DOM"的能力。

背景：Windows UI 模式（UIA/MSAA）对 Chromium 系窗口只能读到窗口标题，
拿不到里面的文字；但这类程序的界面本身就是 HTML，只要有调试端口就能读写 DOM。

**硬性前提**：Electron 只在启动时带 `--remote-debugging-port` 才会开放调试端口，
已经开着的窗口连不进去。所以目标程序必须用带端口的方式启动：

    code --remote-debugging-port=9400

没端口时本通道 attach() 会失败，UI 模式自动回退到 UIA/MSAA/OCR。
"""
import json
import os
import sys
from typing import Dict, List, Optional, Tuple


READ_JS = r"""
(() => {
  const st = window.__ti = window.__ti || {seq: Date.now() % 1e6, blocks: {}};
  const EN = /[A-Za-z]{2}/;
  const MAXLEN = 600;
  const SKIP = __SKIP__;
  const out = [];
  // 只看"元素自己的直接文本节点"，并要求和它的整体可见文字完全一致：
  // 这样只有真正的文字叶子（span、label、a 之类）会入选，
  // 而 .monaco-menu / 菜单栏 这种外面只包一层的容器不会被当成一段文字整块翻掉。
  const DIRECT = (e) => {
    let s = '';
    for (const n of e.childNodes) if (n.nodeType === 3) s += (n.nodeValue || '');
    return s.replace(/\s+/g, ' ').trim();
  };
  const WHOLE = (e) => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const cands = document.querySelectorAll('span,div,a,label,button,p,li,td,h1,h2,h3,h4,h5,h6');
  for (const el of cands) {
    if (el.getAttribute('data-ti-done') === '1') continue;
    const t = DIRECT(el);
    if (!t) continue;
    if (t !== WHOLE(el)) continue;          // 文字分散在子元素里 = 容器，跳过
    if (t.length < 3 || t.length > MAXLEN || !EN.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    // 只翻屏幕上看得见的
    if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) continue;
    const cs = window.getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    if (SKIP && el.closest(SKIP)) continue;
    // 只取"最内层"：子元素文本和它完全一样就跳过，否则同一段会被翻很多遍
    let dup = false;
    for (const c of el.children) {
      if (((c.innerText || '').replace(/\s+/g, ' ').trim()) === t) { dup = true; break; }
    }
    if (dup) continue;
    // 排版容器（菜单栏、工具栏、多列列表）：里面本来就有好几个各自的文字块，
    // 整块翻会把它们压成一段中文，界面直接崩掉 —— 这类一律跳过，只翻里面的单个控件
    let textKids = 0;
    for (const c of el.children) {
      if (((c.innerText || '').trim()).length > 0) textKids++;
    }
    if (textKids >= 3) continue;
    if (textKids >= 2 && t.length > 60) continue;
    let id = el.getAttribute('data-ti-id');
    if (!id) { id = 'ti' + (++st.seq) + '_' + Math.random().toString(36).slice(2, 6); el.setAttribute('data-ti-id', id); }
    st.blocks[id] = el;
    // 浮层优先：菜单、右键菜单、弹窗这些是"鼠标一停就要看"的东西，
    // 而它们 append 在 body 末尾，按 DOM 顺序排最后，会被每轮的新翻配额挤掉。
    const popup = el.closest('.monaco-menu, .context-view, .monaco-popup, [role="menu"], [role="dialog"], .quick-input-widget, .notification-toast') ? 1 : 0;
    out.push({id: id, text: t, x: Math.round(r.left), y: Math.round(r.top),
              w: Math.round(r.width), h: Math.round(r.height), pri: popup});
  }
  out.sort((a, b) => (b.pri || 0) - (a.pri || 0));
  return JSON.stringify(out);
})()
"""

# 写回：清空元素里的文本节点，在开头插一个替换 span 承载译文。
# 不隐藏任何子元素 —— UI 控件里那些 span 往往还带着图标、边框、点击区域，
# 整个藏起来会让菜单/按钮变成空壳（网页模式才需要"隐藏+插入"那套更重的做法）。
APPLY_JS = r"""
((payload) => {
  const st = window.__ti;
  const DIRECT = (e) => {
    let s = '';
    for (const n of e.childNodes) if (n.nodeType === 3) s += (n.nodeValue || '');
    return s.replace(/\s+/g, ' ').trim();
  };
  const ownNodes = (e) => {
    const out = [];
    for (const n of e.childNodes) if (n.nodeType === 3 && (n.nodeValue || '').trim()) out.push(n);
    return out;
  };
  let el = (st && st.blocks && st.blocks[payload.id]) || null;
  // 菜单/弹窗这类浮层一关掉 DOM 就没了，原引用会失效；
  // 这时按原文重新找一个还没翻的可见元素顶上
  if (!el || (el.isConnected === false)) {
    el = null;
    const cands = document.querySelectorAll('span,a,label,button,p,li,td');
    for (const c of cands) {
      if (DIRECT(c) !== payload.orig) continue;      // 只认"自身文字就是这个原文"的叶子
      if ((c.innerText || '').replace(/\s+/g, ' ').trim() !== payload.orig) continue;  // 不许是容器
      if (c.getAttribute('data-ti-done') === '1') continue;
      const rr = c.getBoundingClientRect();
      if (rr.width < 3 || rr.height < 3) continue;
      el = c;
      break;
    }
  }
  if (!el) return 0;
  const old = el.querySelector('[data-ti-repl="1"]');
  if (old) { old.textContent = payload.t; return 1; }
  if (el.getAttribute('data-ti-done') === '1') return 0;
  // 兜底保险：只替换"元素自己的文字"。没有自己的文字 = 这是容器（菜单、面板、工具栏），
  // 清空它会连带把里面所有子控件变成空白 —— 宁可不翻，也不能动它。
  const nodes = ownNodes(el);
  if (!nodes.length) return -1;
  for (const nd of nodes) {
    if (nd.__tiOrig === undefined) nd.__tiOrig = nd.nodeValue;
    nd.nodeValue = '';
  }
  const rep = document.createElement('span');
  rep.setAttribute('data-ti-repl', '1');
  rep.textContent = payload.t;
  el.insertBefore(rep, el.firstChild);
  el.setAttribute('data-ti-done', '1');
  return 1;
})(__PAYLOAD__)
"""

RESTORE_JS = r"""
(() => {
  let n = 0;
  for (const el of document.querySelectorAll('[data-ti-done="1"]')) {
    for (const r of el.querySelectorAll('[data-ti-repl="1"]')) {
      try { r.remove(); } catch (e) {}
    }
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    let t;
    while ((t = w.nextNode())) {
      if (t.__tiOrig !== undefined) { t.nodeValue = t.__tiOrig; delete t.__tiOrig; n++; }
    }
    el.removeAttribute('data-ti-done');
  }
  return n;
})()
"""

DEFAULT_SKIP = ".monaco-editor,.monaco-list,.editor,.terminal,.xterm,.output,.debug-console,.notebook"

# ---- 悬停模式：鼠标停在哪个文字上就只翻那一个（跟网页模式同一套思路）----
# 注入进来以后就是同一个页面，窗口里的鼠标移动会正常触发 DOM mousemove，
# 所以只需要记下坐标，每轮用 elementFromPoint 找鼠标下的文字块。
HOVER_INIT_JS = r"""
(() => {
  const st = window.__ti = window.__ti || {seq: Date.now() % 1e6, blocks: {}};
  if (st.hoverInited) return 1;
  st.hoverInited = true;
  st.mx = null;
  st.my = null;
  document.addEventListener('mousemove', (e) => {
    st.mx = e.clientX;
    st.my = e.clientY;
  }, true);
  window.addEventListener('blur', () => { st.mx = null; st.my = null; }, true);
  return 1;
})()
"""

# 由 Python 端按"系统鼠标位置"直接写坐标（CSS 像素）。
# 只靠页面 mousemove 有个坑：程序刚连上时监听还没装，那一轮的鼠标移动就丢了；
# 而用户往往是"鼠标先停在那里、再点开始翻译"，所以这里要能主动同步。
SET_POS_JS = r"""
((x, y) => {
  const st = window.__ti = window.__ti || {seq: Date.now() % 1e6, blocks: {}};
  st.hoverInited = true;
  st.mx = x;
  st.my = y;
  return 1;
})(__MX__, __MY__)
"""

DPR_JS = "(window.devicePixelRatio || 1)"


def _set_pos_js(mx, my) -> str:
    """占位符替换（不能用 % 格式化：模板里本身就有 `Date.now() % 1e6`）。"""
    return SET_POS_JS.replace("__MX__", str(mx)).replace("__MY__", str(my))

HOVER_READ_JS = r"""
(() => {
  const st = window.__ti;
  if (!st || st.mx === null || st.my === null) return "[]";
  const SKIP = __SKIP__;
  const EN = /[A-Za-z]{2}/;
  const MEDIA = 'IMG,SVG,VIDEO,CANVAS,IFRAME,PICTURE,AUDIO,OBJECT,EMBED';
  // 只认"元素自身的直接文字"，且必须等于它的整体可见文字 —— 否则就是容器
  const own = (e) => {
    let s = '';
    for (const n of e.childNodes) if (n.nodeType === 3) s += (n.nodeValue || '');
    return s.replace(/\s+/g, ' ').trim();
  };
  const whole = (e) => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const mx = st.mx, my = st.my;

  const pt = document.elementFromPoint(mx, my);
  if (!pt) return "[]";
  // 鼠标停在图标/图片上：没有文字可翻，直接不翻
  if (pt.closest && pt.closest(MEDIA)) return "[]";

  // 取"鼠标底下最短的那块文字"：悬浮在菜单项/按钮上时，
  // elementFromPoint 可能返回容器，这时挑落在鼠标位置上、文字最短的那个子元素
  let el = null;
  const cands = [pt, ...pt.querySelectorAll('span,a,label,button,li,td,p')];
  for (const c of cands) {
    const t = own(c);
    if (!t || t !== whole(c)) continue;     // 容器/文字不完整的，跳过
    if (t.length < 3 || t.length > 600 || !EN.test(t)) continue;
    const r = c.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    if (mx < r.left || mx > r.right || my < r.top || my > r.bottom) continue;
    if (!el || t.length < own(el).length) el = c;
  }
  if (!el) return "[]";
  if (SKIP && el.closest(SKIP)) return "[]";
  if (el.getAttribute('data-ti-done') === '1') return "[]";
  // 祖先或后代已经翻过（同一段文字的不同层），不重复翻
  if (el.closest('[data-ti-done="1"]')) return "[]";
  for (const d of el.querySelectorAll('[data-ti-done="1"]')) return "[]";

  // 里面有多个各自的文字块 = 排版容器，整块翻会把它们压成一段，跳过
  let textKids = 0;
  for (const c of el.children) if (own(c).length > 0) textKids++;
  if (textKids >= 2) return "[]";

  const t = own(el);
  let id = el.getAttribute('data-ti-id');
  if (!id) {
    id = 'ti' + (++st.seq) + '_' + Math.random().toString(36).slice(2, 6);
    el.setAttribute('data-ti-id', id);
  }
  st.blocks[id] = el;
  const r = el.getBoundingClientRect();
  return JSON.stringify([{id: id, text: t, x: Math.round(r.left), y: Math.round(r.top),
                          w: Math.round(r.width), h: Math.round(r.height), pri: 1}]);
})()
"""


class InjectChannel:
    """一个窗口对应一条 CDP 会话。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.attached = False
        self.last_error: Optional[str] = None
        self.port: Optional[int] = None
        self._page = None
        self._pending: List[dict] = []      # [{'id':..., 'text':...}]
        self._bound_root = 0                # 绑定的顶层窗口句柄（同步鼠标位置时用来判断）
        self._dpr = 1.0

    # ---------- 连接 ----------
    def _ports(self) -> Tuple[int, ...]:
        raw = self.cfg.get("capture", "inject_ports", None)
        if isinstance(raw, (list, tuple)) and raw:
            out = []
            for x in raw:
                try:
                    out.append(int(x))
                except Exception:
                    continue
            if out:
                return tuple(out)
        return (9222, 9223, 9333, 9400, 9515)

    @staticmethod
    def _match_score(win_title: str, page_title: str) -> int:
        w = (win_title or "").strip()
        p = (page_title or "").strip()
        if not w or not p:
            return 0
        core = w.split(" - ")[0].strip()
        if core and len(core) > 3 and core in p:
            return 3
        if w in p or p in w:
            return 2
        tail = w.split(" - ")[-1].strip()
        if tail and len(tail) > 3 and tail in p:
            return 1
        return 0

    def attach(self, window) -> bool:
        from .. import cdp
        self.last_error = None
        title = getattr(window, "title", "") or ""
        best = None                     # (score, port, page)
        for port in self._ports():
            if not cdp.find_port((port,)):
                continue
            try:
                pages = cdp.list_pages(port)
            except Exception:
                continue
            for pg in pages:
                if (pg.get("type") or "") != "page":
                    continue
                url = (pg.get("url") or "")
                if url.startswith("devtools://"):
                    continue
                score = self._match_score(title, pg.get("title") or "")
                if best is None or score > best[0]:
                    best = (score, port, pg)
        if best is None:
            return False
        try:
            self._page = cdp.CDPPage(best[2]["webSocketDebuggerUrl"])
        except Exception as e:
            self.last_error = f"注入连接失败: {e}"
            self._page = None
            return False
        self.port = best[1]
        self.attached = True
        self._bound_root = int(getattr(window, "hwnd", 0) or 0)
        # 连上就先把鼠标监听装上，否则"连上后的第一次悬停"会因为还没开始记录坐标而落空
        try:
            self._page.eval(HOVER_INIT_JS, timeout=5)
            self._dpr = float(self._page.eval(DPR_JS, timeout=5) or 1) or 1.0
        except Exception:
            self._dpr = 1.0
        return True

    def sync_mouse(self) -> None:
        """把系统光标位置同步到页面坐标（悬停用），鼠标不在目标窗口里就把坐标清空。"""
        from .. import winutil
        if not self.attached or self._page is None:
            return
        hwnd = winutil.get_window_hwnd_under_cursor()
        root = winutil.toplevel_hwnd(hwnd) if hwnd else 0
        rect = winutil.get_client_rect(root) if root else None
        if not rect or root != self._bound_root:
            try:
                self._page.eval(_set_pos_js("null", "null"), timeout=4)
            except Exception:
                pass
            return
        px, py = winutil.get_cursor_pos()
        dpr = self._dpr or 1.0
        mx = (px - rect[0]) / dpr
        my = (py - rect[1]) / dpr
        try:
            self._page.eval(_set_pos_js(round(mx, 1), round(my, 1)), timeout=4)
        except Exception as e:
            self.last_error = f"同步鼠标位置失败: {e}"

    # ---------- 读写 ----------
    def read(self) -> List[dict]:
        """读当前可见的英文文本块。"""
        if not self.attached or self._page is None:
            return []
        skip = self.cfg.get("capture", "inject_skip", DEFAULT_SKIP) or DEFAULT_SKIP
        hover = bool(self.cfg.get("capture", "ui_hover", True))
        if hover:
            # 每轮都跑一次初始化（幂等）：界面重载后监听会丢失，这样能自动补上
            try:
                self._page.eval(HOVER_INIT_JS, timeout=5)
            except Exception:
                pass
            self.sync_mouse()
            js = HOVER_READ_JS.replace("__SKIP__", json.dumps(skip))
        else:
            js = READ_JS.replace("__SKIP__", json.dumps(skip))
        try:
            raw = self._page.eval(js, timeout=8)
        except Exception as e:
            self.last_error = f"读取界面失败: {e}"
            return []
        blocks = raw
        if isinstance(raw, str):
            try:
                blocks = json.loads(raw)
            except Exception:
                return []
        # 同一段文字可能挂在好几个元素上（外面一层容器也算），只留一个，
        # 否则同一句话会被重复翻译、也重复消耗额度
        out = []
        seen = set()
        for b in (blocks or []):
            t = b.get("text") or ""
            if t in seen:
                continue
            seen.add(t)
            out.append(b)
        self._pending = out
        return self._pending

    def apply(self, pairs) -> int:
        """把 [(TextItem, 译文)] 写回 DOM。"""
        if not self.attached or self._page is None:
            return 0
        written = 0
        for item, trans in pairs:
            if trans is None:
                continue
            target = None
            for p in self._pending:
                if p.get("text") == item.text:
                    target = p
                    break
            if target is None:
                continue
            js = APPLY_JS.replace("__PAYLOAD__", json.dumps(
                {"id": target["id"], "t": trans, "orig": item.text}, ensure_ascii=False))
            try:
                n = int(self._page.eval(js, timeout=6) or 0)
            except Exception as e:
                self.last_error = f"写入界面失败: {e}"
                continue
            if n < 0:
                # 目标是"里面装着好几个控件"的容器，写进去会把整块界面清空，跳过
                self.last_error = "跳过一块：目标是排版容器，翻它会把里面的控件清空"
                continue
            written += n
        return written

    def restore(self) -> int:
        if not self.attached or self._page is None:
            return 0
        try:
            return int(self._page.eval(RESTORE_JS, timeout=8) or 0)
        except Exception:
            return 0

    def close(self) -> None:
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
        self._page = None
        self.attached = False
