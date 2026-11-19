"""验证：VS Code（Electron）能不能用 CDP 注入读文本 / 写回。

前置：目标程序必须带 --remote-debugging-port 启动（Electron 启动时才开调试端口，
已经开着的窗口连不进去）。本脚本默认连 9400（tools 里起的独立实例）。

用法：python tools\probe_vscode.py [port]
"""
import json
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9400

BASIC_JS = """
(() => {
  const txt = (document.body.innerText || '').replace(/\\s+/g, ' ');
  return JSON.stringify({
    url: location.href.slice(0, 80),
    body_len: txt.length,
    head: txt.slice(0, 260),
    title: document.title
  });
})()
"""

# 统计"可翻文本块"：含英文、3~600 字、可见，并且不在代码/终端区
BLOCKS_JS = """
(() => {
  const EN = /[A-Za-z]{2}/;
  const SKIP = '.monaco-editor, .monaco-list, .editor, .terminal, .xterm, .output, .debug-console, .chat, .notebook';
  const all = document.querySelectorAll('span, div, a, label, p, li, button');
  let cands = 0, skipped = 0;
  const samples = [];
  for (const el of all) {
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (t.length < 3 || t.length > 600 || !EN.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.height < 4 || r.bottom < 0 || r.top > window.innerHeight) continue;
    if (getComputedStyle(el).display === 'none') continue;
    if (el.closest(SKIP)) { skipped++; continue; }
    // 只留下"最内层"的容器（父元素不是同类候选），避免同一段文字被算很多遍
    cands++;
    if (samples.length < 8) samples.push(t.slice(0, 46));
  }
  return JSON.stringify({cands: cands, skipped_in_code: skipped, samples: samples});
})()
"""

# 试写回：找一个最短的 UI 文本节点，直接替换 nodeValue（不动 DOM 结构）
TRY_JS = """
((newText) => {
  const SKIP = '.monaco-editor, .monaco-list, .editor, .terminal, .xterm, .output, .chat, .notebook';
  const EN = /[A-Za-z]{2}/;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
  let n, best = null;
  while ((n = w.nextNode())) {
    const v = (n.nodeValue || '').trim();
    if (v.length < 3 || v.length > 40 || !EN.test(v)) continue;
    const p = n.parentElement;
    if (!p || p.closest(SKIP)) continue;
    const r = p.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    best = n; break;
  }
  if (!best) return "no-target";
  const old = best.nodeValue;
  best.nodeValue = newText;
  return JSON.stringify({old: old.trim(), parent: best.parentElement.tagName + '.' +
    String(best.parentElement.className || '').slice(0, 40)});
})(%s)
"""


def main() -> int:
    pages = cdp.list_pages(PORT)
    print("targets:", [(p.get("type"), (p.get("title") or "")[:40]) for p in pages])
    if not pages:
        print("没有可用的调试目标（确认程序是用 --remote-debugging-port 启动的）")
        return 2
    page = None
    for p in pages:
        if p.get("type") == "page":
            page = p
            break
    page = page or pages[0]
    pg = cdp.CDPPage(page["webSocketDebuggerUrl"])

    raw = pg.eval(BASIC_JS, timeout=10)
    d = raw if isinstance(raw, dict) else json.loads(raw)
    print("\n可读性:", json.dumps(d, ensure_ascii=False)[:400])

    raw = pg.eval(BLOCKS_JS, timeout=10)
    b = raw if isinstance(raw, dict) else json.loads(raw)
    print("\n候选文本块:", b["cands"], "| 代码区被排除:", b["skipped_in_code"])
    print("样例:", b["samples"])

    print("\n试写回（把一个短 UI 文本改成中文，不动 DOM 结构）:")
    raw = pg.eval(TRY_JS % json.dumps("【中文测试】"), timeout=10)
    print("  ", raw)

    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
