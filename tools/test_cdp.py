"""验证 CDP：连上调试端口，读出页面文本节点，并试写一条译文进页面。"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import cdp

PORT = 9222

SCAN_JS = r"""
(() => {
  if (!window.__tt) {
    window.__tt = { nodes: [], seq: 0 };
  }
  const st = window.__tt;
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
  let n;
  while ((n = walker.nextNode())) {
    const t = n.nodeValue;
    if (!t || t.trim().length < 3) continue;
    const txt = t.trim();
    if (!/[A-Za-z]{2}/.test(txt)) continue;
    if (/[\u4e00-\u9fff]/.test(txt)) continue;
    if (st.nodes.includes(n)) continue;
    const parent = n.parentElement;
    if (!parent) continue;
    if (["SCRIPT","STYLE","NOSCRIPT","TITLE","HEAD"].includes(parent.tagName)) continue;
    st.nodes.push(n);
    out.push({ i: st.nodes.length - 1, text: txt, tag: parent.tagName });
  }
  return JSON.stringify(out.slice(0, 300));
})()
"""


def main() -> int:
    port = cdp.find_port() or PORT
    print("端口:", port)
    pages = cdp.list_pages(port)
    for p in pages:
        print("  页面:", p.get("title", "")[:50], p.get("url", "")[:60])
    if not pages:
        return 1
    pg = cdp.connect(port, pages[0].get("title", ""))
    print("已连接:", bool(pg))
    raw = pg.eval(SCAN_JS)
    import json
    items = json.loads(raw or "[]")
    print(f"页面英文文本节点 {len(items)} 个：")
    for it in items[:10]:
        print(f"   [{it['i']}] <{it['tag']}> {it['text'][:70]}")

    # 试着把第一条替换成中文
    if items:
        test = pg.eval(f"""
        (() => {{
          const n = window.__tt.nodes[{items[0]['i']}];
          if (!n) return 'no node';
          window.__tt.first_backup = n.nodeValue;
          n.nodeValue = '【注入测试】' + n.nodeValue;
          return n.nodeValue;
        }})()
        """)
        print("注入结果:", test)
        restored = pg.eval("""
        (() => {
          const n = window.__tt.nodes[0];
          if (n && window.__tt.first_backup) n.nodeValue = window.__tt.first_backup;
          return n ? n.nodeValue : 'none';
        })()
        """)
        print("还原结果:", restored)
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
