"""配置读写：config.json 与默认值合并，支持热更新保存。"""
import copy
import json
import os
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "translator": {
        "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "api_key": "",
        "model": "",
        "temperature": 0.2,
        "timeout": 20,
        "cache_seconds": 1800,
        "max_batch_lines": 10,
    },
    "capture": {
        "mode": "ocr",
        "interval_ms": 700,
        "min_text_len": 3,
        "max_new_per_round": 12,
        "ocr_scale": 2.0,
        "ocr_min_confidence": 0.55,
    },
    "overlay": {
        "font_family": "Microsoft YaHei",
        "font_size": 11,
        "text_color": "#FFE066",
        "box_color": "#101010",
        "transparent_key": "#010203",
        "auto_place": True,
        "follow_window": True,
        "cover_original": False,   # 网页翻译模式会自动开启"覆盖原文"
    },
    "log_level": "INFO",
}


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: str):
        self.path = path
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = _merge(DEFAULT_CONFIG, json.load(f))
            except Exception:
                self.data = copy.deepcopy(DEFAULT_CONFIG)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, section: str, key: str = None, default=None):
        sec = self.data.get(section, {})
        if key is None:
            return sec
        return sec.get(key, default)

    def set(self, section: str, key: str, value, save: bool = True) -> None:
        """改配置。save=False 只改内存 —— 测试脚本必须用它，否则会把用户的 config.json 改坏。"""
        self.data.setdefault(section, {})[key] = value
        if save:
            self.save()
