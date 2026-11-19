"""翻译层：豆包 API + 本地词库（tm.json）+ 本地 SQLite 缓存 + 术语表。

设计要点：
- 批量：把一轮里多条英文合并成一次请求，省 token、降延迟；
- 词库：翻过的原文→译文永久写进 tm.json，下次（含重启、换窗口、换模式）直接查表，
  不联网也不花额度 —— 这是主要的提速手段；
- 缓存：相同原文在 cache_seconds 内不重复请求（sqlite，短期缓存）；
- 术语：term_map.json 里的固定译名（如 Battlemage→战斗法师）优先命中，不走模型；
- 容错：网络错误/超时/额度不足只记录日志并返回空，不抛异常拖垮主流程。
"""
import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .tm import TranslationMemory

SYSTEM_PROMPT = (
    "你是游戏、网页文本翻译器。输入是英文（可能含 OCR 噪声），输出是简洁流畅的中文。"
    "规则：只输出译文，不加解释、不加引号；保留人名、地名、数字与符号；"
    "不要省略、不要概括、不要合并句子 —— 原文多长就译多长；"
    "若输入是碎片或无意义字符，原样返回空字符串。"
)

BATCH_SUFFIX = (
    "\n输出格式：按输入顺序逐行给出译文，行数必须与输入完全一致，"
    "每行只写译文本身，不要编号、不要空行、不要在译文里换行。"
)

SINGLE_SUFFIX = (
    "\n完整翻译上面这段，只输出一段中文译文；不要分段编号、不要省略任何句子。"
)

# 超过这个长度的单条文本不再走"多行批量"协议：
# 那种协议靠"返回行数"对齐，模型一旦在译文里多换一次行就会串位，
# 结果是长段落只写回半截（用户看到的"内容显示不全"）。
LONG_LINE = 200

# ---------------- 免费翻译端点 ----------------
# 用户要求"翻译用免费、稳定、速度快的方法"：默认走免费端点，豆包 API 只在显式选择 ark 时才用。
# 两个免费端点都不需要 key：
#   youdao   —— aidemo.youdao.com 的公开演示接口，实测 0.3s 返回，中文质量够日常阅读；
#   mymemory —— 匿名版按天限词，作为 youdao 挂掉时的兜底。
# 免费端点是逐条请求（没有批量协议），所以按 800 字切块，避免超长被截/被限。
FREE_CHUNK = 800


def _split_for_free(text: str, limit: int = FREE_CHUNK) -> List[str]:
    """按句子把长文本切成 <=limit 的块（免费端点不适合一次性喂超长文本）。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for sent in re.split(r"(?<=[.!?;:])\s+", text):
        sent = sent.strip()
        if not sent:
            continue
        if buf and len(buf) + 1 + len(sent) > limit:
            parts.append(buf)
            buf = sent
        else:
            buf = (buf + " " + sent) if buf else sent
        while len(buf) > limit:
            parts.append(buf[:limit])
            buf = buf[limit:]
    if buf:
        parts.append(buf)
    return parts


def _http_get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _youdao_once(text: str, timeout: float) -> Optional[str]:
    url = ("https://aidemo.youdao.com/trans?q=" + urllib.parse.quote(text)
           + "&from=en&to=zh-CHS")
    try:
        data = _http_get_json(url, timeout)
    except Exception:
        return None
    if data.get("errorCode") not in (0, "0"):
        return None
    tr = data.get("translation") or []
    out = "".join(str(x) for x in tr).strip()
    return out or None


def _mymemory_once(text: str, timeout: float) -> Optional[str]:
    url = ("https://api.mymemory.translated.net/get?q=" + urllib.parse.quote(text)
           + "&langpair=en|zh-CN")
    try:
        data = _http_get_json(url, timeout)
    except Exception:
        return None
    rd = data.get("responseData") or {}
    out = html.unescape(str(rd.get("translatedText") or "")).strip()
    if not out or out.lower() == text.strip().lower():
        return None
    return out


class Translator:
    # 只有不超过这个长度的词条才进 tm.json（长句只进 sqlite 缓存），可用 config 里的
    # translator.tm_short_max 覆盖。菜单项/按钮/单词都在这以内，长段落不会把词库撑大。
    DEFAULT_SHORT_MAX = 16

    def __init__(self, cfg, work_dir: str):
        self.cfg = cfg
        self.work_dir = work_dir
        self.db_path = os.path.join(work_dir, "cache.sqlite3")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()
        self.term_map = self._load_terms()
        try:
            self.tm_short_max = int(cfg.get("translator", "tm_short_max", self.DEFAULT_SHORT_MAX)
                                    or self.DEFAULT_SHORT_MAX)
        except Exception:
            self.tm_short_max = self.DEFAULT_SHORT_MAX
        self.tm_short_max = max(1, self.tm_short_max)
        # 永久词库：翻过一次的短词条，之后都直接查表
        self.tm = TranslationMemory(os.path.join(work_dir, "tm.json"))
        # 老版本不区分长短，把历史长句挪进 sqlite，json 只留短词条
        try:
            self.pruned = self.prune_memory()
        except Exception:
            self.pruned = 0
        self.stats = {"hits": 0, "tm_hits": 0, "misses": 0, "errors": 0}
        # provider: youdao（默认，免费）/ mymemory（免费兜底）/ ark（豆包，要额度）
        self.provider = str(cfg.get("translator", "provider", "youdao") or "youdao").lower()

    @property
    def provider_label(self) -> str:
        return {
            "youdao": "有道免费接口（不花钱）",
            "mymemory": "MyMemory 免费接口（不花钱）",
            "ark": "豆包 API（消耗额度）",
        }.get(self.provider, self.provider)

    # ---------- 免费端点 ----------
    def _free_one(self, text: str) -> Optional[str]:
        """免费端点翻译一条：按句切块，逐块请求，再拼回。"""
        timeout = float(self.cfg.get("translator", "timeout", 20) or 20)
        pieces: List[str] = []
        for chunk in _split_for_free(text):
            out = None
            if self.provider == "mymemory":
                out = _mymemory_once(chunk, timeout) or _youdao_once(chunk, timeout)
            else:
                out = _youdao_once(chunk, timeout) or _mymemory_once(chunk, timeout)
            if not out:
                return None
            pieces.append(out)
        return "".join(pieces).strip() or None

    # ---------- 存储 ----------
    def _init_db(self) -> None:
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "  key TEXT PRIMARY KEY, src TEXT, dst TEXT, ts REAL)"
            )
            self._db.commit()

    def _load_terms(self) -> Dict[str, str]:
        path = os.path.join(self.work_dir, "term_map.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k).strip().lower(): str(v).strip() for k, v in data.items() if v}
        except Exception:
            pass
        return {}

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()

    def _db_get(self, text: str) -> Optional[str]:
        ttl = float(self.cfg.get("translator", "cache_seconds", 1800) or 0)
        if ttl <= 0:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT dst, ts FROM cache WHERE key=?", (self._key(text),)).fetchone()
        if not row:
            return None
        if time.time() - float(row[1]) > ttl:
            return None
        return row[0]

    def _cache_put(self, src: str, dst: str) -> None:
        """写短期缓存（sqlite）：所有来源、所有长度都写。"""
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cache (key, src, dst, ts) VALUES (?,?,?,?)",
                (self._key(src), src, dst, time.time()))
            self._db.commit()

    def _remember(self, src: str, dst: str, source: str = "ui") -> None:
        """翻成功一条后的记账。

        规则（你定的）：
        - 网页模式：只进 sqlite 缓存，永不写 tm.json（网页句子量大、几乎不复现）；
        - 长句（超过 tm_short_max 个字，默认 16）：只进 sqlite 缓存，不进 tm.json（否则文件越来越大）；
        - 短词条（菜单项、按钮、词）：进 tm.json，永久复用 —— 这才是真正反复出现的东西；
        - 译文和原文一模一样（专有名词、中文回中文）不进 tm.json，免得词库被废条污染。
        """
        self._cache_put(src, dst)
        if source == "web":
            return
        if src.strip().lower() == dst.strip().lower():
            return
        if len(src.strip()) > self.tm_short_max:
            return
        self.tm.put(src, dst)

    def prune_memory(self) -> int:
        """瘦身：把 tm.json 里超过长度阈值的长句挪进 sqlite 缓存再从 json 删掉。

        历史版本不分长短全写进 json，句库会越滚越大；这个方法把长句迁走（数据不丢，
        仍在 sqlite 里可命中），json 只保留短词条。返回迁走的条数。
        """
        long_items = [k for k in self.tm.items() if len(k.strip()) > self.tm_short_max]
        if not long_items:
            return 0
        data = self.tm.items()
        for k in long_items:
            dst = data.get(k)
            if dst:
                self._cache_put(k, dst)
        self.tm.drop_many(long_items)
        return len(long_items)

    def clear_cache(self) -> int:
        """只清 sqlite 短期缓存；tm.json 是永久词库，不动（另有 clear_memory()）。"""
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            self._db.execute("DELETE FROM cache")
            self._db.commit()
        return int(n)

    def clear_memory(self) -> int:
        """清空永久词库（tm.json），下次开始所有句子都会重新联网翻译。"""
        return self.tm.clear()

    # ---------- 术语 ----------
    def _term_hit(self, text: str) -> Optional[str]:
        t = text.strip().lower()
        for k, v in self.term_map.items():
            if t == k or (len(k) >= 4 and k in t):
                if len(t) <= len(k) + 2:
                    return v
        return None

    # ---------- API ----------
    def _call_api(self, lines: List[str], single: bool = False) -> List[str]:
        """single=True：整段一次性翻译（长段落用，不做逐行对齐）。"""
        sec = self.cfg.get("translator")
        url = sec.get("api_url")
        key = sec.get("api_key")
        model = sec.get("model")
        timeout = float(sec.get("timeout", 20) or 20)
        temperature = float(sec.get("temperature", 0.2) or 0.2)
        if not (url and key and model):
            raise RuntimeError("translator.api_key / model 未配置")

        if single:
            user_text = lines[0] + SINGLE_SUFFIX
            max_tokens = min(8000, 300 + len(lines[0]) * 4)      # 中文比英文短，但留足余量
        else:
            user_text = "\n".join(lines) + BATCH_SUFFIX
            max_tokens = min(8000, 200 + sum(len(x) for x in lines) * 3)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        content = data["choices"][0]["message"]["content"].strip()
        finish = (data["choices"][0].get("finish_reason") or "").lower()
        if single:
            # 整段翻译：把模型可能插入的换行合并，保证写回是一整段
            return [" ".join(x.strip() for x in content.splitlines() if x.strip())]
        out = [ln.strip() for ln in content.splitlines() if ln.strip() != ""]
        if finish == "length" or len(out) != len(lines):
            # 行数不齐：宁可把这一批当失败（返回空，让上层重试或降级），
            # 也不要错位拼接 —— 错位会把译文写到别的段落去
            if finish == "length":
                return [""] * len(lines)
            out = out[: len(lines)] + [""] * max(0, len(lines) - len(out))
        return out

    # ---------- 对外 ----------
    def translate_lines(self, lines: List[str],
                        source: str = "ui") -> Dict[str, Optional[str]]:
        """返回 {原文: 译文}；无法翻译的值为 None。

        source 决定"翻完记到哪"：`web` = 网页模式（只进 sqlite，不写 tm.json）；
        其它（uia / ocr / memory / gfx）= 界面模式（短词条进 tm.json）。
        """
        result: Dict[str, Optional[str]] = {}
        pending: List[str] = []
        for raw in lines:
            text = (raw or "").strip()
            if not text:
                continue
            if text in result:
                continue
            term = self._term_hit(text)
            if term:
                result[text] = term
                self.stats["hits"] += 1
                continue
            seen = self.tm.get(text)
            if seen is not None:
                result[text] = seen
                self.stats["hits"] += 1
                self.stats["tm_hits"] += 1
                continue
            cached = self._db_get(text)
            if cached is not None:
                result[text] = cached
                self.stats["hits"] += 1
                self._remember(text, cached, source)   # 老缓存顺手迁进永久词库（仍按长度规则）
            else:
                pending.append(text)
        if not pending:
            return result

        if self.provider in ("youdao", "mymemory", "free"):
            # 免费端点：一条一次请求，不需要批量协议；失败只记日志，返回 None 让上层跳过
            for text in pending:
                try:
                    dst = self._free_one(text)
                except Exception:
                    dst = None
                if dst:
                    result[text] = dst
                    self._remember(text, dst, source)
                    self.stats["misses"] += 1
                else:
                    self.stats["errors"] += 1
                    result[text] = None
            return result

        limit = int(self.cfg.get("translator", "max_batch_lines", 10) or 10)
        # 长段落单独整段翻，短句才批量（批量靠行数对齐，长文本容易串位/截断）
        longs = [t for t in pending if len(t) > LONG_LINE]
        shorts = [t for t in pending if len(t) <= LONG_LINE]

        for text in longs:
            try:
                outs = self._call_api([text], single=True)
            except Exception as e:
                self.stats["errors"] += 1
                raise RuntimeError(f"翻译请求失败: {e}")
            dst = (outs[0] if outs else "").strip()
            if dst:
                result[text] = dst
                self._remember(text, dst, source)
                self.stats["misses"] += 1
            else:
                result[text] = None

        for start in range(0, len(shorts), max(1, limit)):
            chunk = shorts[start: start + max(1, limit)]
            try:
                outs = self._call_api(chunk)
            except Exception as e:
                self.stats["errors"] += 1
                raise RuntimeError(f"翻译请求失败: {e}")
            for src, dst in zip(chunk, outs):
                if dst:
                    result[src] = dst
                    self._remember(src, dst, source)
                    self.stats["misses"] += 1
                else:
                    result[src] = None
        return result
