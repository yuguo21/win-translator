"""翻译记忆库（tm.json）：翻过的原文 → 译文永久存下来，下次直接查表，不再走网络。

设计要点：
- 只存"文本"，不存界面位置 —— 同一句英文出现在网页、VS Code 菜单还是 OCR 截图里，都命中同一条；
- 匹配不分大小写、忽略多余空白，但文件里保留原文的原始大小写，方便手工查看/编辑；
- 加载一次进内存（启动时读盘，之后全在内存里查，没有磁盘开销）；
- 写盘节流（默认最多每 2 秒落盘一次）+ 原子替换（先写 .tmp 再 replace），进程被杀也不会写出半个文件；
- 文件损坏时备份成 tm.bad-时间戳.json 再重建，不静默丢数据。
"""
import atexit
import json
import os
import re
import shutil
import threading
import time
from typing import Dict, Optional

_WS = re.compile(r"[ \t\r\n\u00a0]+")


def norm_key(text: str) -> str:
    """查表用的键：压缩空白 + 转小写。"""
    return _WS.sub(" ", (text or "").strip()).lower()


class TranslationMemory:
    def __init__(self, path: str, save_delay: float = 2.0):
        self.path = path
        self.save_delay = float(save_delay)
        self._lock = threading.Lock()
        self._data: Dict[str, str] = {}      # 规范化键 -> 译文
        self._src: Dict[str, str] = {}       # 规范化键 -> 原文写法（落盘时用）
        self._loaded = False
        self._dirty = False
        self._last_save = 0.0
        self.hits = 0
        atexit.register(self.flush)

    # ---------- 读 ----------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("顶层不是对象")
        except Exception:
            bak = self.path + time.strftime(".bad-%Y%m%d-%H%M%S")
            try:
                shutil.copy2(self.path, bak)
            except Exception:
                pass
            self._data, self._src = {}, {}
            return
        for k, v in data.items():
            text = str(k or "").strip()
            dst = str(v or "").strip()
            if not text or not dst:
                continue
            self._data[norm_key(text)] = dst
            self._src[norm_key(text)] = _WS.sub(" ", text).strip()

    def get(self, text: str) -> Optional[str]:
        with self._lock:
            self._load()
            dst = self._data.get(norm_key(text))
        if dst:
            self.hits += 1
        return dst

    # ---------- 写 ----------
    def put(self, src: str, dst: str) -> None:
        src, dst = (src or "").strip(), (dst or "").strip()
        if not src or not dst:
            return
        k = norm_key(src)
        with self._lock:
            self._load()
            if self._data.get(k) == dst:
                return
            self._data[k] = dst
            self._src.setdefault(k, _WS.sub(" ", src).strip())
            self._dirty = True
            due = (time.time() - self._last_save) >= self.save_delay
        if due:
            self.save()

    def save(self) -> bool:
        """落盘（原子替换）。返回是否真的写了。"""
        with self._lock:
            if not self._dirty:
                return False
            snapshot = {self._src[k]: v for k, v in self._data.items()}
            self._dirty = False
            self._last_save = time.time()
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=0)
            os.replace(tmp, self.path)
            return True
        except Exception:
            with self._lock:
                self._dirty = True
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False

    def flush(self) -> None:
        try:
            self.save()
        except Exception:
            pass

    def clear(self) -> int:
        with self._lock:
            self._load()
            n = len(self._data)
            self._data, self._src = {}, {}
            self._dirty = True
        self.save()
        return n

    def items(self) -> Dict[str, str]:
        """当前全部条目（原文写法 -> 译文）的副本，供迁移/瘦身使用。"""
        with self._lock:
            self._load()
            return {self._src[k]: v for k, v in self._data.items()}

    def drop_many(self, texts) -> int:
        """批量删掉若干条（按原文或规范键都行），返回删掉的条数。"""
        with self._lock:
            self._load()
            n = 0
            for t in texts:
                k = norm_key(t)
                if self._data.pop(k, None) is not None:
                    self._src.pop(k, None)
                    n += 1
            if n:
                self._dirty = True
        if n:
            self.save()
        return n

    def __len__(self) -> int:
        with self._lock:
            self._load()
            return len(self._data)
