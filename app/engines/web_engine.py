"""网页翻译模式：把译文**直接写进网页**（替换原文），不使用贴图悬浮层。

抓取方式 = **鼠标悬停**：鼠标停在哪个段落，就翻哪个段落。
（早期版本是整页批量扫描，但页面一屏几十个文本节点，每轮只翻 12 条、间隔 700ms，
  长页面要滚很久才能翻完，用户反馈"很多内容没翻"，所以改成按需翻译。）

实现方式：
- 浏览器必须以调试模式启动（`--remote-debugging-port=9222`），
  本工具的「启动翻译浏览器」按钮会用独立配置目录起一个 Edge，不影响你现有浏览器与登录态；
- 页面侧装一个 mousemove 监听记录坐标，Python 每 250ms 用
  `elementFromPoint` 找到鼠标下最合适的"段落块"，取它的英文原文送翻译，
  再把中文写回这个块（原文挂在文本节点的 `__ttOrig` 属性上，可一键还原）。

且好处：按需翻译、省额度、反应快、不会漏掉视口外的内容。

已知限制：
- iframe 里的内容拿不到（`elementFromPoint` 只返回 iframe 元素本身）；
- 段落里如果是逐词拆开的文本节点，会整段合并成一句再翻（不逐词翻）。
"""
import json
import re
import time
from typing import Dict, List, Optional

from .. import cdp, winutil
from .base import BaseEngine, TextItem

# 长段落按句子切成多块分别翻译：整段几万字丢给模型，输出会被截断，
# 写回时就成了"只有前半截中文", 用户看到的就是内容不全。
_SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+")


def _chunk_text(text: str, limit: int = 700) -> List[str]:
    """把长段落切成若干 <=limit 的块，尽量按句子边界切。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    buf = ""
    for sent in _SENT_SPLIT.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if buf and len(buf) + 1 + len(sent) > limit:
            parts.append(buf)
            buf = sent
        else:
            buf = (buf + " " + sent) if buf else sent
        while len(buf) > limit:            # 单句本身超长：硬切
            parts.append(buf[:limit])
            buf = buf[limit:]
    if buf:
        parts.append(buf)
    return parts


def _title_matches(page_title: str, window_title: str) -> bool:
    """浏览器窗口标题形如「页面标题 - 个人 - Microsoft Edge」，页面标题是「页面标题」。"""
    p = (page_title or "").strip().lower()
    w = (window_title or "").strip().lower()
    if not p or not w:
        return False
    return p in w or w in p


# 页面侧只需要装一次鼠标位置记录；这里写成"每次执行都先确认一遍"，
# 因为点链接打开新页面 / 跳转后页面的 JS 状态会被清空，靠 Python 端记 inited 会失灵。
HOVER_INIT_JS = r"""
(() => {
  const st = window.__tt = window.__tt || {};
  if (!st.inited) {
    st.inited = true;
    st.mx = null; st.my = null;
    st.seq = Date.now() % 1e6;              // 用时间戳起头，避免和旧属性里的 id 重号
    st.blocks = {};                        // id -> DOM 元素（直接存引用，不靠字符串查）
    document.addEventListener('mousemove', function (e) {
      st.mx = e.clientX; st.my = e.clientY;
    }, true);
    document.addEventListener('mouseleave', function () { st.mx = null; }, true);
  }
  return 1;
})()
"""

# 找鼠标下那一段，返回 {id, text}；没合适段落就返回 {}
POLL_JS = r"""
(() => {
  // 页面跳转后 window.__tt 会没了，这里每次先把它补回来（幂等，代价很小）
  const st = window.__tt = window.__tt || {};
  if (!st.inited) {
    st.inited = true;
    st.mx = null; st.my = null;
    st.seq = Date.now() % 1e6;
    st.blocks = {};
    document.addEventListener('mousemove', function (e) {
      st.mx = e.clientX; st.my = e.clientY;
    }, true);
    document.addEventListener('mouseleave', function () { st.mx = null; }, true);
  }
  if (st.mx === null) return "{}";
  const H = ["H1","H2","H3","H4","H5","H6"];
  const PARAS = ["P","LI","TD","TH","DT","DD","BLOCKQUOTE","FIGCAPTION","CAPTION"];
  const BLOCKS = ["SPAN","A","LABEL","DIV","ARTICLE","SECTION","UL","OL","TABLE","FORM"];
  // 非文字元素：鼠标停在这些上面就不翻，也不许它们被写回隐藏
  //（否则父容器被整块替换时，图片/视频会被一起 display:none → "悬停图片，图片消失"）
  const MEDIA = ["IMG","SVG","VIDEO","CANVAS","IFRAME","PICTURE","AUDIO","OBJECT","EMBED"];
  const MAXLEN = 600;                  // 容器兜底时限制长度，避免一下子替换整片区域
  const innerText = (el) => (el.innerText || "").replace(/\s+/g, " ").trim();
  const hasEn = (t) => /[A-Za-z]{2}/.test(t);
  const isDone = (el) => el && el.getAttribute && el.getAttribute('data-tt-done') === '1';
  const doneAncestor = (el) => {
    let c = el;
    while (c) { if (isDone(c)) return true; c = c.parentElement; }
    return false;
  };

  // 容器里的某个段落正好在鼠标下时，优先翻那一段（而不是整个容器）
  const paraUnder = (root, x, y) => {
    if (!root.querySelectorAll) return null;
    const cands = root.querySelectorAll('p,li,h1,h2,h3,h4,h5,h6,td,th,blockquote,figcaption,dd,dt');
    for (const c of cands) {
      const r = c.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom && r.height > 4) return c;
    }
    return null;
  };

  let hit = document.elementFromPoint(st.mx, st.my);
  if (!hit) return "{}";

  // 鼠标停在图片 / 视频 / 画布这类非文字元素上：直接什么都不做。
  // 用户反馈：悬停到图片上，图片消失了 —— 原因是向上找到了父容器、被整块替换。
  {
    let probe = hit;
    while (probe && probe !== document.body && probe !== document.documentElement) {
      const ptag = probe.tagName || "";
      if (MEDIA.indexOf(ptag) >= 0) return "{}";
      probe = probe.parentElement;
    }
  }

  // 容器自己的直接文本节点（不属于任何子元素）。真实页面里极常见，例如
  //   <div>北京时间 3 月 5 日<p>……段落……</p></div>
  // 过去这类裸文本会被静默跳过：既不翻、也不会被覆盖，就是一直留英文。
  // 现在把它单独包一层内联 span（data-tt-loose）当作可翻块，还原时再拆掉这层包装。
  const looseNodes = (root) => {
    const out = [];
    for (const n of (root.childNodes || [])) {
      if (n.nodeType !== 3) continue;
      const t = (n.nodeValue || "").replace(/\s+/g, " ").trim();
      if (t.length < 3 || t.length > MAXLEN || !hasEn(t)) continue;
      const rg = document.createRange();
      rg.selectNodeContents(n);
      const r = rg.getBoundingClientRect();
      if (!r || r.height < 4) continue;          // 纯空白行或不可见
      out.push({ node: n, text: t, r: r });
    }
    return out;
  };
  const looseDown = (root, x, y) => {
    for (const it of looseNodes(root)) {
      if (x >= it.r.left && x <= it.r.right && y >= it.r.top && y <= it.r.bottom) return it;
    }
    return null;
  };
  const wrapLoose = (it) => {
    const node = it.node;
    const s = document.createElement('span');
    s.setAttribute('data-tt-loose', '1');
    node.parentNode.insertBefore(s, node);     // 包住这段裸文本，位置不变
    s.appendChild(node);
    return s;
  };

  // 容器里段落子元素离鼠标最近的那个（容器本身不该被整块替换，
  // 否则卡片里的"标题 + 三段"会被压成一段中文，其它段落视觉上直接消失）
  const nearParaIn = (root, x, y, limit) => {
    if (!root.querySelectorAll) return null;
    const cands = root.querySelectorAll('p,li,h1,h2,h3,h4,h5,h6,td,th,blockquote,figcaption,dd,dt');
    let best = null, dist = 1e9;
    for (const c of cands) {
      if (isDone(c) || !hasEn(innerText(c))) continue;
      const r = c.getBoundingClientRect();
      if (r.height < 4) continue;
      const dx = Math.max(r.left - x, 0, x - r.right);
      const dy = Math.max(r.top - y, 0, y - r.bottom);
      const d = dx * dx + dy * dy;
      if (d < dist) { dist = d; best = c; }
    }
    return (best && dist <= limit * limit) ? best : null;
  };

  // 容器里含图片/视频时，改成找"离鼠标最近、且自己不含媒体"的文字块
  //（典型：图片列表的 div.item 里，图片和标题是兄弟，整块替换会把图和标题一起吃进一段译文）
  const nearTextIn = (root, x, y, limit) => {
    if (!root.querySelectorAll) return null;
    const cands = root.querySelectorAll('a,span,strong,em,b,i,label,div,time,cite,p');
    let best = null, dist = 1e9;
    for (const c of cands) {
      if (isDone(c)) continue;
      if (c.querySelector && c.querySelector('img,svg,video,canvas,iframe,picture,audio,object,embed')) continue;
      const t = innerText(c);
      if (t.length < 3 || t.length > MAXLEN || !hasEn(t)) continue;
      const r = c.getBoundingClientRect();
      if (r.height < 4) continue;
      const dx = Math.max(r.left - x, 0, x - r.right);
      const dy = Math.max(r.top - y, 0, y - r.bottom);
      const d = dx * dx + dy * dy;
      if (d < dist) { dist = d; best = c; }
    }
    return (best && dist <= limit * limit) ? best : null;
  };

  let best = null, cur = hit;
  while (cur && cur !== document.body && cur !== document.documentElement) {
    const tag = cur.tagName;
    if (PARAS.indexOf(tag) >= 0 || H.indexOf(tag) >= 0) { best = cur; break; }
    if (BLOCKS.indexOf(tag) >= 0) {
      // 鼠标正好落在某个段落上：优先翻那一段
      const under = paraUnder(cur, st.mx, st.my);
      if (under) { best = under; break; }
      // 鼠标正压在容器自己的裸文本上：翻这段裸文本，别被里面的段落抢走
      const loose = looseDown(cur, st.mx, st.my);
      if (loose) { best = wrapLoose(loose); break; }
      const hasBlockKids = cur.querySelector('p,li,h1,h2,h3,h4,h5,h6,td,th,blockquote,figcaption,dd,dt');
      if (hasBlockKids) {
        // 容器里包着段落：只翻离鼠标最近的那一段；太远就不翻，绝不整块替换
        const near = nearParaIn(cur, st.mx, st.my, 300);
        if (near) { best = near; }
        if (best) break;
      } else if (!best) {
        const ic = innerText(cur);
        const hasMediaKid = cur.querySelector && cur.querySelector('img,svg,video,canvas,iframe,picture,audio,object,embed');
        if (hasMediaKid) {
          // 容器里有图片/视频：绝不整块替换（否则图片和兄弟标题会被一起吞成一段译文），
          // 退一步只翻"离鼠标最近、且自己不含媒体"的那一块文字
          const nearText = nearTextIn(cur, st.mx, st.my, 300);
          if (nearText) { best = nearText; break; }
        } else if (ic.length <= MAXLEN && hasEn(ic)) { best = cur; break; }
      }
    }
    cur = cur.parentElement;
  }
  if (!best) {
    // 鼠标落在段落之间的空白里：退一步，找页面里离鼠标最近的一段
    let near = null, dist = 1e9;
    for (const c of document.querySelectorAll('p,li,h1,h2,h3,h4,h5,h6,blockquote,figcaption')) {
      if (isDone(c) || !hasEn(innerText(c))) continue;
      const r = c.getBoundingClientRect();
      if (r.height < 4) continue;
      const dx = Math.max(r.left - st.mx, 0, st.mx - r.right);
      const dy = Math.max(r.top - st.my, 0, st.my - r.bottom);
      const d = dx * dx + dy * dy;
      if (d < dist) { dist = d; near = c; }
    }
    if (!near || dist > 40000) return "{}";    // 离得太远（超过 200px）就不管
    best = near;
  }
  if (isDone(best) || doneAncestor(best)) return "{}";         // 这段（或它的容器）已翻过
  if (best.querySelector && best.querySelector('[data-tt-done="1"]')) return "{}";  // 里面已有译文

  const text = innerText(best);
  if (text.length < 3 || !hasEn(text)) return "{}";
  // 超长段落不翻：宁可不动，也不要翻一半就整段替换（那就是"内容显示不全"
  if (text.length > 20000) return JSON.stringify({err: "这一段太长（" + text.length + " 字），暂不处理"});

  let id = best.getAttribute('data-tt-id');
  if (!id) {
    id = 'tt' + (++st.seq) + '_' + Math.random().toString(36).slice(2, 6);
    best.setAttribute('data-tt-id', id);
  }
  st.blocks[id] = best;                    // 存元素引用，写回时直接用，避免 id 重号写错位置
  return JSON.stringify({ id: id, text: text });
})()
"""

# 把译文写回该段。
#
# 旧做法是"译文塞进第一个文本节点、其余文本节点清空"，长段落和嵌套 HTML 会出问题：
#   - 段落里有 <a>/<strong>/<em> 时，这些元素的文字被清空 → 链接和加粗的那截内容消失；
#   - 悬停命中"容器"时整块被压成一段 → 容器里其它段落视觉上没了。
#
# 现在改成：原内容**隐藏而不删除**（文本清空但留 __ttOrig，内联元素 display:none 并记住原值），
# 再在段落开头插一个 <span data-tt-repl> 放完整译文。视觉上内容是完整的，还原也精确。
APPLY_JS_TPL = r"""
(() => {
  const st = window.__tt;
  const p = __PAYLOAD__;
  const el = (st && st.blocks && st.blocks[p.id]) || null;
  if (!el) return 0;

  // 已经替换过（同一段再次写回）：只更新译文，避免层层嵌套
  const old = el.querySelector('[data-tt-repl="1"]');
  if (old) {
    old.textContent = p.t;
    el.setAttribute('data-tt-done', '1');
    return 1;
  }
  if (el.getAttribute('data-tt-done') === '1') return 0;

  // 1) 收集原有文本节点，清空但保留原文（还原用）
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  let n;
  while ((n = walker.nextNode())) {
    if ((n.nodeValue || "").trim()) nodes.push(n);
  }
  if (!nodes.length) return 0;
  for (const nd of nodes) {
    if (nd.__ttOrig === undefined) nd.__ttOrig = nd.nodeValue;
    nd.nodeValue = "";
  }

  // 整块文字全部来自"唯一一个内联子元素"时（最典型就是标题链接 <a>），
  // 把译文写进这个载体内部、并保留它 —— 否则标题链接会被 display:none 变成不可点的空壳，
  // 用户看到的就是"原有内容被覆盖"（新华网图片列表的 .tit 正是这种结构）
  const inlineCarrier = ["A","SPAN","STRONG","EM","B","I","FONT","LABEL"];
  const allKids = Array.from(el.querySelectorAll('*'));
  let ownText = "";
  for (const c of el.childNodes) if (c.nodeType === 3) ownText += (c.nodeValue || "");
  const carrier = (allKids.length === 1 && inlineCarrier.indexOf(allKids[0].tagName) >= 0
                   && ownText.trim() === "") ? allKids[0] : null;

  // 2) 原有内联元素整体隐藏（不删 DOM，链接、图片、加粗都还在，只是不显示）
  //    记的是"计算后的 display"而不是 style.display，这样本来就被 CSS 隐藏的元素还原时不会被翻出来
  //    注意：图片/视频/画布这类媒体元素**永远不隐藏** —— 它们没有文字，
  //    藏起来只会让页面变空（用户报过"悬停图片，图片消失"）
  const MEDIA_TAGS = ["IMG","SVG","VIDEO","CANVAS","IFRAME","PICTURE","AUDIO","OBJECT","EMBED"];
  const kids = el.querySelectorAll('*');
  for (const k of kids) {
    if (!k.getAttribute) continue;
    if (MEDIA_TAGS.indexOf(k.tagName) >= 0) continue;
    if (carrier && (k === carrier || (carrier.contains && carrier.contains(k)))) continue;
    if (k.__ttDisp === undefined) {
      const cd = (window.getComputedStyle(k).display || "");
      // 记原 style.display（可能为空=交给 CSS）；本来就靠 CSS 隐藏的直接记 none，还原时别把它翻出来
      k.__ttDisp = k.style.display || (cd === 'none' ? 'none' : '');
    }
    k.style.display = 'none';
  }

  // 3) 插一个替换块承载完整译文
  const wrap = document.createElement('span');
  wrap.setAttribute('data-tt-repl', '1');
  wrap.textContent = p.t;
  if (carrier) {
    carrier.insertBefore(wrap, carrier.firstChild);   // 译文放进链接内部，链接仍然可以点
  } else {
    el.insertBefore(wrap, el.firstChild);
  }

  el.setAttribute('data-tt-done', '1');
  return 1;
})()
"""

RESTORE_JS = r"""
(() => {
  const els = document.querySelectorAll('[data-tt-done="1"]');
  let done = 0;
  for (const el of els) {
    // 去掉替换块
    const wraps = el.querySelectorAll('[data-tt-repl="1"]');
    for (const w of wraps) {
      try { w.remove(); } catch (e) {}
    }
    // 文本节点写回原文
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    let n;
    while ((n = walker.nextNode())) {
      if (n.__ttOrig !== undefined) {
        n.nodeValue = n.__ttOrig;
        delete n.__ttOrig;
        done++;
      }
    }
    // 被隐藏的元素恢复显示
    const kids = el.querySelectorAll('*');
    for (const k of kids) {
      if (k.__ttDisp === undefined) continue;
      k.style.display = k.__ttDisp;
      delete k.__ttDisp;
    }
    el.removeAttribute('data-tt-done');
  }
  // 裸文本的包装 span（data-tt-loose）要拆掉，把文本节点放回父元素原位，
  // 让 DOM 结构回到和翻译前一样
  for (const s of document.querySelectorAll('[data-tt-loose="1"]')) {
    const parent = s.parentNode;
    if (!parent) continue;
    while (s.firstChild) parent.insertBefore(s.firstChild, s);
    parent.removeChild(s);
  }
  return done;
})()
"""

RESET_JS = r"""
(() => {
  window.__tt = { inited: false, mx: null, my: null, seq: Date.now() % 1e6, blocks: {} };
  return 1;
})()
"""


class WebEngine(BaseEngine):
    mode = "web"
    display_name = "网页翻译（悬停翻当前段落）"
    description = "鼠标停在哪个段落就翻哪段，中文直接写进网页；需用「启动翻译浏览器」开浏览器。"

    renders_in_page = True      # 控制器据此不走悬浮层

    def __init__(self, cfg):
        super().__init__(cfg)
        self._page = None
        self._port = None
        self._ws_url = None
        self._pending: List[dict] = []       # [{'id':.., 'text':..}] 等待写回的段
        self._last_key = None                # 上一次已经翻过的（id + 文本）
        self._last_check = 0.0               # 上次检查"是否换标签"的时间

    def prepare(self, window) -> None:
        super().prepare(window)
        self._port = getattr(self, "_forced_port", None) or cdp.find_port()
        if not self._port:
            self.last_error = (
                "没检测到浏览器调试端口。请点界面上的「启动翻译浏览器」"
                "（会用独立配置目录开一个 Edge，不影响你现在的浏览器）"
            )
            return
        self._connect()

    # ---------- 目标页面选择 ----------
    def _title_hints(self):
        """实时取「用户这会儿在看的浏览器窗口标题」。

        浏览器切标签、点链接跳转后窗口标题会变，所以每次都要重新读，
        不能用 prepare() 时缓存的那份（这也是"点了链接翻译就失效"的直接原因之一）。

        返回 (锁定窗口标题, 前台浏览器窗口标题)：前者是用户明确锁定的，
        后者只是"用户现在大概在看哪个浏览器"的参考，不能当成硬性要求。
        """
        locked = ""
        if self.window and winutil.is_window(self.window.hwnd):
            locked = winutil.get_window_title(self.window.hwnd).strip()
        fg = ""
        fg_hwnd = winutil.get_foreground_hwnd()
        if fg_hwnd and fg_hwnd != (self.window.hwnd if self.window else 0):
            t = winutil.get_window_title(fg_hwnd).strip()
            if t and ("edge" in t.lower() or "chrome" in t.lower()):
                fg = t
        return locked, fg

    def _select_target(self):
        """按窗口标题挑当前应该在翻的标签页。返回 (target, 错误说明)。"""
        try:
            pages = [p for p in (cdp.list_pages(self._port) or [])
                     if p.get("webSocketDebuggerUrl")]
        except Exception as e:
            return None, f"连接浏览器失败: {e}"
        if not pages:
            return None, "没找到可用的网页标签"
        locked, fg = self._title_hints()
        for h in [x for x in (locked, fg) if x]:
            for p in pages:
                if _title_matches(p.get("title") or "", h):
                    return p, None
        # 标题对不上：当前连接还在列表里就继续用它（用户可能在看别的浏览器）
        if self._ws_url:
            for p in pages:
                if p.get("webSocketDebuggerUrl") == self._ws_url:
                    return p, None
        # 只有"用户明确锁定的窗口"对不上时才报错，否则取第一个标签
        if locked:
            return None, (
                f"你锁定的窗口（{locked[:30]}）不对应翻译浏览器里的任何标签页。"
                "请用「启动翻译浏览器」打开的那个浏览器浏览网页"
            )
        return pages[0], None

    def _attach(self, target) -> bool:
        ws_url = target.get("webSocketDebuggerUrl")
        try:
            page = cdp.CDPPage(ws_url)
        except Exception as e:
            self.last_error = f"连接页面失败: {e}"
            return False
        old = self._page
        self._page = page
        self._ws_url = ws_url
        self._pending = []
        self._last_key = None
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        # 新页面的旧状态可能残留，重置一次（不动已经写好的译文）
        try:
            page.eval(RESET_JS, timeout=5)
        except Exception:
            pass
        return True

    def _connect(self) -> bool:
        self._last_check = time.time()
        target, err = self._select_target()
        if target is None:
            self._page = None
            self.last_error = err
            return False
        self.last_error = None
        return self._attach(target)

    def _maybe_follow(self) -> None:
        """每隔 1 秒检查一次：用户是不是切到了另一个标签/新页面，是就跟过去。"""
        now = time.time()
        if now - self._last_check < 1.0:
            return
        self._last_check = now
        if self._port is None:
            return
        target, _err = self._select_target()
        if target is None:
            return          # 暂时对不上就先不动，别把正在用的连接掐了
        if target.get("webSocketDebuggerUrl") == self._ws_url:
            return
        self._attach(target)

    def capture_once(self) -> List[TextItem]:
        if self._page is None:
            if not self._connect():
                return []
        self._maybe_follow()
        try:
            raw = self._page.eval(POLL_JS, timeout=6)
        except Exception as e:
            # 页面跳转 / 标签关闭会让这轮连接失效：丢掉它，下一轮按窗口标题重连
            self.last_error = f"读取页面失败: {e}"
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
            self._ws_url = None
            self._pending = []
            self._last_key = None
            return []

        try:
            row = json.loads(raw or "{}")
        except Exception:
            row = {}
        if row.get("err"):
            self.last_error = row["err"]
            return []
        if not row or not row.get("text"):
            self.last_error = "鼠标还没指到英文段落（把鼠标停在一段英文上）"
            return []
        key = f"{row.get('id')}|{row.get('text')}"
        if key == self._last_key:
            return []
        self._last_key = key
        text = row["text"]
        parts = _chunk_text(text)
        if not parts:
            return []
        self._pending = [{
            "id": row["id"],
            "text": text,
            "parts": parts,
            "tr": {},          # 块序号 -> 译文（用序号而不是文本做键：段落里重复的句子文本一样）
        }]
        self.last_error = None
        # 长段落拆成多块，每块是一个待翻条目，翻完再拼回整段
        return [TextItem(0, 0, 0, 0, c, 1.0) for c in parts]

    def apply(self, pairs) -> int:
        """把 [(TextItem, 译文)] 写回网页对应段落。

        一段长文本会被拆成多块分别翻译，这里等这一段的块都翻完，
        再按原顺序拼成一整段中文写进去 —— 否则只会写回首块，后面就丢了。
        """
        if self._page is None:
            return 0
        for item, trans in pairs:
            if trans is None:
                continue
            for p in self._pending:
                # 同一原文可能对应好几个块（重复的句子），一次全部填上
                for i, chunk in enumerate(p["parts"]):
                    if chunk == item.text and i not in p["tr"]:
                        p["tr"][i] = trans

        written = 0
        changed = False
        for p in list(self._pending):
            if len(p["tr"]) < len(p["parts"]):
                continue               # 还有块没翻完，等下一轮
            full = "".join(p["tr"].get(i) or c for i, c in enumerate(p["parts"]))
            js = APPLY_JS_TPL.replace("__PAYLOAD__", json.dumps(
                {"id": p["id"], "t": full}, ensure_ascii=False))
            try:
                n = int(self._page.eval(js, timeout=6) or 0)
            except Exception as e:
                self.last_error = f"写入页面失败: {e}"
                continue
            written += n
            changed = True
            if n:
                self._pending.remove(p)
                # 译文明显短于原文时提示一下：说明这段可能被模型截断了
                if len(p["text"]) > 200 and len(full) < len(p["text"]) * 0.3:
                    self.last_error = (
                        f"这段译文偏短（原文 {len(p['text'])} 字，译文 {len(full)} 字），"
                        "可能被截断，可以再停一下重翻"
                    )
        if changed and written == 0:
            # 没写成功（元素没了/页面跳转），允许下一轮重试同一段
            self._last_key = None
        return written

    def restore(self) -> int:
        """把页面还原成原文。"""
        if self._page is None:
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
        self._ws_url = None
        super().close()
