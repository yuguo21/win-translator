"""内存扫描引擎：按"选定的那个窗口"所属进程，扫描里面的英文文本，翻译后贴悬浮层。

和"内存 Hook（注入改写内存）"不是一件事：这里只**读**，不动游戏内存，不注入 DLL，
所以不会把游戏改坏，也不会翻到别的程序 —— 一切以你选定的那个窗口的进程为界。

工作方式：
1. `prepare(window)` 拿窗口的 pid，OpenProcess 打开这个进程（窗口最小化也能读）；
2. 每轮从上次停下的地址继续扫一块可读内存（轮转推进，单轮几十毫秒，不会卡住游戏）；
3. 从字节流里提取 ASCII / UTF-16LE 的连续可打印串，用启发式规则筛出"像英文短语的东西"；
4. 只输出**新出现**的（已经翻译过的记在 `_known` 里，不会反复冒出来）；
5. 没有坐标可给，所以按"列表"排在窗口左上角，一条一行。

配置（config.json，都可省略）：
  memory.min_len        默认 3，太短的不算
  memory.max_len        默认 200，太长的（多半是别的数据）不算
  memory.round_mb       每轮最多扫多少 MB，默认 48
  memory.keywords       若配了关键词表，就只找这些（老用法保留）
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import time
from typing import List, Optional, Set

from .. import winutil
from .base import BaseEngine, TextItem

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT = 0x1000
# 可读的页保护属性（低 8 位）
PAGE_READABLE = frozenset((0x02, 0x04, 0x08, 0x20, 0x40, 0x80))
MAX_REGION = 64 * 1024 * 1024      # 单个内存区超过 64MB 就跳过（多半是资源/纹理缓冲）
MIN_REGION = 8 * 1024              # 小于 8KB 的碎片区跳过（文本通常在大块里，扫它们只会拖时间）
MAX_CHUNK = 4 * 1024 * 1024        # 单次 ReadProcessMemory 最多 4MB
PAGE_STEP = 0x1000

_ASCII_RUN = re.compile(rb"[ -~]{4,}")
_U16_RUN = re.compile(rb"(?:[ -~]\x00){4,}")
_SAME_CHAR = re.compile(r"(.)\1{5,}")
_WORD_SPLIT = re.compile(r"[^A-Za-z]+")
# 日志/代码/路径/格式化模板的典型特征，命中即判定"不是给人看的文字"
_TECH_PAT = re.compile(
    r"::|->|\b0x[0-9A-Fa-f]{2,}|\bpid\b|=\s*|\bgle\b|\bstd::\b|"
    r"%[-+ 0-9.]*[sdulxXc]|\\x[0-9A-Fa-f]{2}|\b[A-Za-z]+_[A-Za-z_]+\b|"
    r"\b(result|error|failed|fail|failed|sandbox|trace|debug)\s*:|"
    r"\b[A-Za-z]+\s*\([A-Za-z]*\)\s*=",
    re.I)
# 程序日志/库代码常见的"技术词"。一句话里命中两个以上，就当日志丢掉。
# 单个出现不丢（多人游戏里 server、选项里 memory 都算正常词）。
_TECH_WORDS = frozenset("""
server client socket buffer cache descriptor protocol commit transaction
snapshot materialize serialize deserialize timeout retry thread mutex
process handle pointer allocation heap stack callback handler listener
queue packet stream encode decode parse invalid exception trace debug
payload header api http https url json xml utf binary byte bytes hash
encrypt decrypt token session request response payload config cursor
mutex semaphore iterator index offset flags param params args
""".split())
# 常见英文功能词：句子/短语里至少出现一个，才认为是"人写的句子"，
# 否则多半是符号名、变量名、DLL 导出名之类（内存里这类串非常多）
_FUNC_WORDS = frozenset("""
the a an and or of to in on at for with without your you our my his her their
is are was were be been being has have had do does did not no yes can could
will would shall should may might must if then than this that these those
from by as it its they them we us he she who what when where why how
all any some more most new open close save load exit back next start
""".split())


def _rank(s: str):
    """给候选打分：完整句子 > 短语 > 单词。返回 None 表示不够像人话。"""
    words = [w.lower() for w in _WORD_SPLIT.split(s) if w]
    if not words:
        return None
    has_space = " " in s
    func = sum(1 for w in words if w in _FUNC_WORDS)
    if has_space and func == 0 and not (s.isupper() or s.istitle()):
        return None
    if has_space and len(words) >= 3:
        return (0, -min(len(s), 120))
    if has_space:
        return (1, -len(s))
    return (2, -len(s))


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", ctypes.c_char * 256),
        ("szExePath", ctypes.c_char * 260),
    ]


def _foreign_module_ranges(pid: int) -> List[tuple]:
    """枚举目标进程加载的模块，返回"不属于这个程序自己目录"的模块所占的 [起, 止) 区间。

    为什么整块跳过：进程里被塞进来的 DLL 非常多 —— Windows 系统库、输入法
    （豆包的 tsf-oime-core.dll / doubaoime-rpc-new.dll）、各种注入的沙箱与安全组件，
    它们里面全是英文日志和报错模板（"Clipboard recovery pending"、"strict
    materialization settle ..."），和被翻译的程序一点关系都没有，扫进来只会把真正的
    界面/剧情文本淹没。只留主程序 exe 所在目录（含其子目录）的模块 + 私有堆。
    """
    TH32CS_SNAPMODULE = 0x00000008
    TH32CS_SNAPMODULE32 = 0x00000010
    windir = (os.environ.get("SystemRoot") or r"C:\Windows").lower().rstrip("\\") + os.sep
    out: List[tuple] = []
    home = ""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if not snap:
        return out
    try:
        me = MODULEENTRY32()
        me.dwSize = ctypes.sizeof(me)
        ok = kernel32.Module32First(snap, ctypes.byref(me))
        first = True
        while ok:
            path = (me.szExePath or b"").decode("mbcs", "ignore")
            low = path.lower()
            base = int(me.modBaseAddr or 0)
            size = int(me.modBaseSize or 0)
            if first and path:
                # 第一个模块就是主程序 exe，它的目录算"自己家"
                home = os.path.dirname(path).lower().rstrip("\\") + os.sep
                first = False
            elif base > 0 and size > 0:
                in_home = bool(home) and low.startswith(home)
                if not in_home or low.startswith(windir):
                    out.append((base, base + size))
            me = MODULEENTRY32()
            me.dwSize = ctypes.sizeof(me)
            ok = kernel32.Module32Next(snap, ctypes.byref(me))
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(snap)
    return out


def _looks_like_text(s: str, min_len: int, max_len: int) -> bool:
    """判断一串字符像不像**人写的文字**（而不是日志、代码、路径、变量名、二进制残片）。

    原则：内存里绝大部分可打印串都不是给人看的（格式化日志、snake_case 符号、
    报错模板、注册表路径、纹理数据），这些一律不翻 —— 只留真正的界面/剧情文本。
    """
    s = s.strip()
    if len(s) < max(3, min_len) or len(s) > max_len:
        return False
    if s.startswith("#") or s.startswith("//") or s.startswith("/*"):
        return False                                  # 代码注释
    if any(ord(c) < 32 or ord(c) > 126 for c in s):
        return False

    # --- 一眼就是"技术串"的，直接丢 ---
    if _TECH_PAT.search(s):
        return False
    if any(ch in s for ch in "={}|_%$\\"):        # 键值对、格式化、标识符、路径
        return False
    # 只留"正常文字"会出现的字符（字母数字 + 常见标点）；@ < > [ ] 之类一律当残片
    if any(not (c.isalnum() or c in " .,!?'-:;()\"&") for c in s):
        return False
    if sum(ch.isdigit() for ch in s) > 2:         # 日志/版本号/内存地址里数字扎堆
        return False
    if s.count("(") + s.count(")") > 2:           # 层层括号的函数式/区域名
        return False

    letters = sum(1 for c in s if c.isascii() and c.isalpha())
    if letters < 3:
        return False
    visible = sum(1 for c in s if not c.isspace()) or 1
    if letters / visible < 0.7:                   # 剩下的符号还是太多
        return False
    if _SAME_CHAR.search(s):                      # AAAAAA 之类填充
        return False
    if s.count("/") + s.count("\\") > 1:          # 文件路径
        return False
    if re.search(r"::|->|\[|\]|\b0x\b", s):
        return False

    words = [w for w in _WORD_SPLIT.split(s) if w]
    if not words or max(len(w) for w in words) < 3:
        return False
    # 日志味太重：技术词命中两个以上
    tech = sum(1 for w in words if w.lower() in _TECH_WORDS)
    if tech >= 2:
        return False
    # 句子里的"驼峰函数名"（DrainServerDirtyState 这种）也算日志特征
    camel = sum(1 for w in words if re.search(r"[a-z][A-Z]", w))
    if camel >= 1:
        return False
    # 词形像英文单词（有元音）的比例要够高，否则是随机/缩写堆
    wordlike = sum(1 for w in words if len(w) >= 2 and any(c in "aeiouy" for c in w.lower()))
    if wordlike * 3 < len(words) * 2:
        return False
    # 元音密度：正常英文文本元音大约占 35%，低于 20% 基本是随机串
    vowels = sum(1 for c in s.lower() if c in "aeiou")
    if vowels * 5 < letters:
        return False
    # 全大写（标题/缩写）要求元音更足，否则 QTUDEUEUTEDTETTT 这类缩写堆会漏进来
    if s.isupper() and vowels * 10 < letters * 3:
        return False
    # 三个以上相同字母连排（UUUU、QTQ…堆）：正常英文几乎不会出现
    if re.search(r"([A-Za-z])\1{2,}", s):
        return False

    has_space = " " in s
    if not has_space:
        # 单个词：只接受正常大小写，且长度像"界面上的一个词"（超长没空格的几乎都是标识符）
        if len(s) > (14 if s.isupper() else 12):
            return False
        if "." in s:                              # Microsoft.Notepad 这类标识
            return False
        if not (s.islower() or s.istitle() or s.isupper()):
            return False
        if s.isupper() and len(s) > 4:
            # 全大写长串多半是缩写堆（QTDEUEUTEDTETTT），要有足够元音才认（CAMPAIGN 这种标题）
            if vowels * 10 < letters * 3:
                return False
            # 全大写单词超过 10 个字母几乎必然是编码/序列串（RESETEUEVEWEXEYEZE）
            return len(s) <= 10
        return True

    if len(words) < 2:
        return False
    upper = sum(1 for c in s if c.isupper())
    if upper and upper * 3 > letters:             # 大写太密集（含缩写/常量大写串）
        if not (s.isupper() and len(s) <= 30):    # 全大写的短标题可以
            return False
    # 首字母要是大写，或者整句全小写（正常句子）
    first_alpha = next((c for c in s if c.isalpha()), "")
    if first_alpha and not (first_alpha.isupper() or s.islower()):
        return False
    return True


class MemoryEngine(BaseEngine):
    mode = "memory"
    display_name = "内存扫描（按选定窗口的进程）"
    description = ("只读扫描选定窗口所属进程的内存，找出英文文本后翻译，悬浮层显示；"
                   "不改游戏内存、不注入 DLL，窗口最小化也能读。")
    needs_screen = False        # 读内存不需要窗口画面，最小化照样能扫
    renders_in_log = True       # 译文不贴图，直接写日志（用户要求）

    def __init__(self, cfg):
        super().__init__(cfg)
        self._handle = None
        self._pid = 0
        self._cursor = 0
        self._known: Set[str] = set()
        self._needles: List[str] = []
        self._sys_ranges: List[tuple] = []
        self.last_stats = {}

    # ---------- 生命周期 ----------
    def prepare(self, window) -> None:
        if window is None:
            raise RuntimeError("内存扫描模式必须先选定一个窗口（用它的进程干活）")
        super().prepare(window)
        pid = int(getattr(window, "pid", 0) or 0)
        if pid <= 0:
            raise RuntimeError("选定的窗口没有可用的进程 ID，换一个窗口再试")
        self.close()
        self.window = window
        self._pid = pid
        self._cursor = 0
        self._known = set()
        self._needles = self._load_needles()
        self._handle = kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self._handle:
            self._handle = kernel32.OpenProcess(
                PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not self._handle:
            code = ctypes.get_last_error()
            raise RuntimeError(
                "打不开这个窗口的进程（PID=%d，错误码 %d）。"
                "内存扫描需要和目标程序同等或更高权限，试试以管理员身份运行本工具" % (pid, code))
        self._sys_ranges = _foreign_module_ranges(pid) if self._skip_system() else []

    def close(self) -> None:
        if self._handle:
            try:
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
        self._handle = None
        self._pid = 0
        super().close()

    def _load_needles(self) -> List[str]:
        raw = self.cfg.get("memory", "keywords", []) or []
        if isinstance(raw, dict):
            raw = list(raw.keys())
        return [str(x).strip() for x in raw if str(x).strip()]

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            v = int(self.cfg.get("memory", key, default) or default)
        except Exception:
            v = default
        return max(1, v)

    def _skip_system(self) -> bool:
        try:
            v = self.cfg.get("memory", "skip_system_modules", None)
        except Exception:
            v = None
        if v is None:
            return True          # 没配置时默认跳过系统 DLL（否则噪声会淹没真正的文本）
        return bool(v)

    def _in_system_module(self, base: int) -> bool:
        for lo, hi in self._sys_ranges:
            if lo <= base < hi:
                return True
        return False

    # ---------- 扫描 ----------
    def _read_region(self, base: int, size: int) -> Optional[bytes]:
        buf = (ctypes.c_char * size)()
        read = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            self._handle, ctypes.c_void_p(base), buf, ctypes.c_size_t(size), ctypes.byref(read))
        if not ok:
            return None
        return bytes(buf[: read.value])

    def _scan_round(self, budget: int, seconds: float = 1.5) -> List[str]:
        """从游标位置往后扫（最多 budget 字节 / seconds 秒），返回新发现的可翻文本。"""
        if not self._handle:
            return []
        min_len = self._cfg_int("min_len", 3)
        max_len = self._cfg_int("max_len", 200)
        found: List[str] = []
        total = 0
        regions = 0
        skipped = 0
        cands = 0
        t0 = time.time()
        deadline = t0 + max(0.3, seconds)
        addr = self._cursor
        mbi = MEMORY_BASIC_INFORMATION()
        size_mbi = ctypes.sizeof(mbi)
        while total < budget and time.time() < deadline:
            if not kernel32.VirtualQueryEx(self._handle, ctypes.c_void_p(addr),
                                           ctypes.byref(mbi), size_mbi):
                self._cursor = 0          # 走到头了，下一轮从头开始（重新覆盖一遍）
                break
            base = int(mbi.BaseAddress or 0)
            region = int(mbi.RegionSize or 0)
            if base <= 0 or region <= 0:
                addr += PAGE_STEP
                self._cursor = addr
                continue
            usable = (mbi.State == MEM_COMMIT
                      and (mbi.Protect & 0xFF) in PAGE_READABLE
                      and MIN_REGION <= region <= MAX_REGION)
            if usable and self._sys_ranges and self._in_system_module(base):
                usable = False        # 系统 DLL 的字符串跟游戏无关，整块跳过
                skipped += 1
            if usable:
                regions += 1
                off = 0
                while off < region and total < budget and time.time() < deadline:
                    n = min(MAX_CHUNK, region - off, budget - total)
                    blob = self._read_region(base + off, n)
                    off += n
                    total += n
                    if not blob:
                        continue
                    for cand in self._extract(blob, min_len, max_len):
                        cands += 1
                        if cand in self._known:
                            continue
                        self._known.add(cand)
                        if len(found) < 2000:      # 收集上限，但扫描游标照常推进，不会卡在原地
                            found.append(cand)
            addr = base + region
            self._cursor = addr
            if addr > 0x7FFFFFFFFFFF:
                addr = 0
                self._cursor = 0
                break
        self.last_stats = {
            "round_seconds": round(time.time() - t0, 2),
            "regions": regions,
            "skipped_sys": skipped,
            "mb": round(total / (1024 * 1024.0), 1),
            "candidates": cands,
            "new": len(found),
            "cursor": hex(self._cursor),
            "known": len(self._known),
        }
        return found

    def _extract(self, blob: bytes, min_len: int, max_len: int) -> List[str]:
        """从一个内存块里提取像英文短语的字符串（ASCII 与 UTF-16LE 都看）。"""
        out: List[str] = []
        if self._needles:
            # 老用法：只认关键词表里的原文
            for n in self._needles:
                if n in self._known:
                    continue
                if n.encode("utf-8", "ignore") in blob or n.encode("utf-16-le", "ignore") in blob:
                    out.append(n)
            return out
        for m in _ASCII_RUN.finditer(blob):
            s = m.group().decode("ascii", "ignore")
            if _looks_like_text(s, min_len, max_len):
                out.append(s.strip())
        for m in _U16_RUN.finditer(blob):
            s = m.group().decode("utf-16-le", "ignore")
            if _looks_like_text(s, min_len, max_len):
                out.append(s.strip())
        return out

    # ---------- 每轮 ----------
    def capture_once(self) -> List[TextItem]:
        if not self.window or not winutil.is_window(self.window.hwnd):
            return self.fail("选定的窗口已关闭，请重新选定")
        if not self._handle:
            return self.fail("还没连上这个窗口的进程（先在列表里选定窗口再点开始）")
        # 进程退出就明确报错，别去翻别的东西
        code = wt.DWORD(0)
        if kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)) and code.value != 259:
            return self.fail("这个窗口的进程已经退出了（错误码 %d）" % code.value)

        budget = self._cfg_int("round_mb", 48) * 1024 * 1024
        seconds = float(self._cfg_int("round_seconds", 2) or 2)
        hits = self._scan_round(budget, seconds)
        if not hits:
            self.last_error = None
            return []

        rect = self.client_rect()
        if not rect:
            return []
        cw = max(200, rect[2] - rect[0])
        ch = max(120, rect[3] - rect[1])
        max_new = int(self.cfg.get("capture", "max_new_per_round", 12) or 12)
        # 择优：先放完整句子，再放短语，最后才是单词（按钮/菜单词）
        ranked = []
        for s in hits:
            r = _rank(s)
            if r is not None:
                ranked.append((r, s))
        ranked.sort(key=lambda kv: (kv[0], kv[1].lower()))
        hits = [s for _, s in ranked] or []
        items: List[TextItem] = []
        line_h = 20
        for i, text in enumerate(hits[:max_new]):
            y = 10 + i * line_h
            if y + line_h > ch:
                break
            items.append(TextItem(x=10, y=y, w=max(180, cw // 2), h=line_h - 4,
                                  text=text, conf=1.0))
        self.last_error = None
        return items
