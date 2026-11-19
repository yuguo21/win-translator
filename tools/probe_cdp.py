"""探测浏览器是否提供 DevTools 调试端口（网页注入的前提）。

用法：python tools\probe_cdp.py [端口...]
"""
import json
import sys
import urllib.request

DEFAULT_PORTS = [9222, 9223, 9333, 8222]


def probe(port: int) -> bool:
    url = f"http://127.0.0.1:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:
            info = json.loads(r.read().decode("utf-8"))
        print(f"端口 {port}: 命中  {info.get('Browser')}")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1.5) as r:
            targets = json.loads(r.read().decode("utf-8"))
        pages = [t for t in targets if t.get("type") == "page"]
        print(f"  页面标签 {len(pages)} 个：")
        for t in pages[:5]:
            print(f"    - {t.get('title','')[:50]}  {t.get('url','')[:70]}")
        return True
    except Exception as e:
        print(f"端口 {port}: 不通（{type(e).__name__}）")
        return False


def main() -> int:
    ports = [int(p) for p in sys.argv[1:]] or DEFAULT_PORTS
    ok = any(probe(p) for p in ports)
    print("\n可用调试端口:", "有" if ok else "无")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
