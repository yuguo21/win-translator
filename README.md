# 窗口实时翻译（translate_db）

锁定一个窗口，把它里面的英文**实时**译成中文。

- 浏览器：中文直接**写进网页正文**（不贴图，排版、复制、可选中都跟原生一样）；
- 游戏 / 桌面软件：截图识别（OCR）后把中文浮在原文旁边的空白处，或直接写进程序界面；
- 内存扫描模式：不需要画面，直接读目标进程的英文文本，译文**逐条写进日志**。

纯 Python + Win32 API，**零额外下载**（OCR 用 Windows 自带引擎），翻译默认走**免费接口**，不烧额度。

---

## 功能一览

| 模式 | 状态 | 做什么 | 怎么显示 |
|---|---|---|---|
| **网页翻译** | ✅ 推荐 | 连浏览器 DevTools 通道，**鼠标停在哪段就翻哪段**，点链接换页也能继续 | 中文写进网页本身，可还原 |
| **OCR 截图** | ✅ 通用 | 窗口截图 → 2 倍放大 + 灰度增强 → Windows 自带 OCR 识别 | 中文浮在原文旁边（贴图） |
| **Windows UI** | ✅ 适合软件 | 标准控件读 `WM_GETTEXT`；Electron 程序（VS Code 等）走注入读写 DOM | 写进程序界面 / 悬停即翻 |
| **内存扫描** | ⚠️ 实验 | 按选定窗口的进程只读扫描，只挑"人写的文字" | **不贴图，写日志**（界面日志 + 文件） |
| **图形 API Hook** | ⚠️ 需 DLL | 预留 `StartHook(pid)` / `PollText()` 接口 | 需自备 Hook DLL |

---

## 环境要求

- Windows 10 / 11
- Python 3.12（3.10+ 应该也行，未逐一验证）
- 依赖：`mss`、`Pillow`、`winsdk`、`rapidfuzz`（`tkinter`、`sqlite3` 是标准库）

```powershell
pip install mss pillow winsdk rapidfuzz
```

> `winsdk` 提供 Windows 自带的 OCR 与 UI Automation；装不上时 OCR 模式不可用，其他模式不受影响。

---

## 安装与启动

```powershell
git clone https://github.com/yuguo21/win-translator.git
cd win-translator

# 1) 复制一份配置（config.json 里存你的 API key，已被 .gitignore 排除）
copy config.example.json config.json

# 2) 启动
python main.py
```

也可以双击 `启动翻译工具.bat`（自动找本机 Python）。

环境自查（不开界面，看窗口列表、OCR 引擎、翻译配置是否就绪）：

```powershell
python main.py --selftest
```

指定默认模式启动：

```powershell
python main.py --mode ocr
```

---

## 快速上手（4 步）

1. 点「刷新列表」→ 选中目标窗口 →（可选）「鼠标拾取」，3 秒后取光标下的窗口；
2. 选捕获模式；
3. 点「开始翻译」；
4. 译文出现。点「停止」收工（网页 / UI 模式会还原原文）。

> 网页模式不用锁窗口；Windows UI 模式默认"鼠标停哪翻哪"，也不用锁；
> OCR / 内存 / 图形模式**必须先锁定窗口**（它们要按这个窗口的像素或进程工作）。

---

## 各模式怎么用

### 1. 网页翻译（浏览器首选）

1. 点「启动翻译浏览器」——会用独立配置目录起一个带调试端口的 Edge，**不动你现有的浏览器和登录态**；
2. 在这个浏览器里正常浏览英文网站，点「开始翻译」；
3. **把鼠标停在想看的段落上**，那段立刻变成中文；换一段再停；
4. 「停止」会把所有译过的段落还原成原文。

为什么按鼠标翻而不是整页翻：一屏几十个文本节点，批量翻要排队、慢、还耗额度；悬停 = 你想看哪段翻哪段。

细节说明：

- 走 Chrome DevTools Protocol，手写 WebSocket/HTTP，零依赖；
- 文本取自页面 DOM（不是截图），所以中文可选中、可复制、排版不乱；
- 点链接换页、开新标签都能继续跟；
- 长段落按句子切块分别翻译再拼回，不会只翻一半；
- 段落里有链接/加粗时，原文元素是隐藏而非删除，**停止后一字不差还原**；
- 悬停在图片上**不会**隐藏图片、也不会去翻（只翻文字）。

限制：`iframe` 内的内容拿不到；纯图片文字请用 OCR 模式；单段超过 20000 字不翻。

### 2. OCR 截图

锁定窗口 → 选「OCR 截图」→ 开始。适合游戏、模拟器、自绘界面、图片为主的网页。

- 用 Windows 自带 `Windows.Media.Ocr`，无需下载模型；
- 截图会放大 2 倍并做灰度增强，小字号识别率更高；
- 译文以黄色中文浮在原文右侧空白处，悬浮层鼠标可穿透，不影响你操作；
- 最小化 / 关闭按钮等无意义文字会被过滤；
- 装了 `paddleocr` 会自动优先使用（更准但体积大，可选）。

### 3. Windows UI

先试标准控件读取：`EnumChildWindows + WM_GETTEXT` / MSAA，翻译直接写回控件。

**Electron / Chromium 程序（VS Code、Discord 等）** 界面本身是网页，读不到控件文字，必须走注入：

1. **先完全退出该程序**（VS Code 是单实例，再启动只会把参数转给已在运行的实例）；
2. 用带调试端口的方式启动，例如：

   ```powershell
   "<安装路径>\Code.exe" --remote-debugging-port=9400
   ```

   仓库里的 `启动VS Code注入模式.bat` 就是干这件事（会先检查是否还在运行）；
3. 点「开始翻译」。默认**鼠标悬停翻当前那一项**，鼠标移到别的窗口会自动跟随；
4. 想让快捷方式日常就带端口，把快捷方式「目标」改成加上 `--remote-debugging-port=9400` 即可
   （任务栏图标若是旧的要重钉一次）。

端口列表在 `config.json → capture.inject_ports`（默认 `9222, 9223, 9400, 9515`）。
`capture.prefer_inject: false` 可退回纯 UIA/MSAA 读取。

> 只是想要中文界面的话，VS Code 直接装官方简体中文语言包更省事；注入适合"临时看懂某个英文界面、又不想改程序本身"。

### 4. 内存扫描（只写日志）

锁定窗口 → 选「内存扫描」→ 开始。

- 只**读**目标窗口所属进程的内存，不注入、不改游戏、不会翻到别的程序；
- **窗口最小化也能继续扫**（不需要画面）；
- 只挑"人写的文字"：日志、代码、路径、变量名、格式化模板、二进制残片全部丢弃；
- 译文**不贴图**，一条条写进下方日志区，同时落盘到 `logs\translate-YYYYMMDD.log`，
  格式：`[译] 原文  →  译文`；
- 自动跳过不属于程序自己安装目录的 DLL（系统库、输入法、注入组件的英文日志都在那里，
  不跳会把正文淹没）；该行为由 `memory.skip_system_modules` 控制。

想只翻指定原文，可在 `config.json` 里填 `memory.keywords`（填了之后就只找这些）。

### 5. 图形 API Hook（需自带 DLL）

给特殊引擎（D3D9/11、Unity）预留了接口：`StartHook(pid)` / `PollText()`。
在 `config.json` 里配 `gfx.dll_path` 指向你的 Hook DLL；没配时该模式会明确报错，不会假装工作。

---

## 字号与颜色（可实时调）

主界面有「译文字号」（8～48）和「字色」（`#RRGGBB`），改完点「应用」或回车：

- 立刻重画已有译文，不用等下一轮、不用重启；
- 同时写回 `config.json`（`overlay.font_size` / `overlay.text_color`）；
- 默认 11 号（OCR 场景下 14 号容易压住原文）。

---

## 翻译后端：免费优先

`config.json → translator.provider`：

| 值 | 说明 |
|---|---|
| `youdao`（默认） | 有道免费接口，不要 key，1 秒内返回；失败自动回退 MyMemory 免费接口 |
| `ark` | 火山方舟（豆包）API，**按量计费、欠费报错**，需要自己填 `api_key` / `model` / `api_url` |

免费接口对专业名词容易译错（如把 `Cut` 译成"减少"），遇到重要界面词，写进 `term_map.json`
做定译覆盖（术语表优先级最高）。

### 词库与缓存（省额度）

查表顺序：**术语表 → 词库 `tm.json` → SQLite 缓存 → 网络**。

| 内容 | 去向 | 原因 |
|---|---|---|
| 短词条（≤ `tm_short_max`＝16 字，菜单项/按钮/单词） | `tm.json`（永久） | 反复出现，永久复用最划算 |
| 长句（> 16 字） | 只进 SQLite 缓存（默认 30 分钟） | 几乎不复现，进 json 会把文件撑大 |
| 网页模式翻的任何内容 | 只进 SQLite 缓存 | 量大、更新快 |

- `tm.json` 是纯 JSON（`{"英文": "中文"}`），可以手工编辑、合并、搬到别的机器用；
- 匹配不分大小写、忽略多余空白，文件里保留原始大小写；
- 写盘节流 + 原子替换，进程被杀不会写出半个文件；文件损坏会自动备份成 `tm.json.bad-时间戳` 再重建；
- **注意：翻过的会被永久记住，错译也会被记住** —— 所以重要词请用 `term_map.json` 覆盖。

---

## 日志与生成文件

| 文件 / 目录 | 内容 | 是否提交 git |
|---|---|---|
| `config.json` | 你的配置（可能含 API key） | ❌ 已忽略 |
| `tm.json` | 翻译词库 | ❌ 已忽略 |
| `cache.sqlite3` | 翻译缓存 | ❌ 已忽略 |
| `logs\translate-YYYYMMDD.log` | 界面日志 + 内存模式译文 | ❌ 已忽略 |
| `browser_profile\` | 翻译浏览器独立配置（含登录态） | ❌ 已忽略 |
| `backups\` | 词库 / 缓存的备份 | ❌ 已忽略 |

---

## config.json 速查

| 键 | 默认 | 说明 |
|---|---|---|
| `translator.provider` | `youdao` | 翻译后端 |
| `translator.api_key` / `model` / `api_url` | 空 | 仅 `ark` 模式需要 |
| `translator.cache_seconds` | 1800 | 缓存有效期（秒），0 表示不查缓存 |
| `translator.tm_short_max` | 16 | 多长的句子才只进缓存、不进 json |
| `capture.mode` | `ocr` | 默认模式：`web` / `ocr` / `uia` / `memory` / `gfx` |
| `capture.interval_ms` | 700 | 每轮抓取间隔 |
| `capture.max_new_per_round` | 12 | 每轮最多翻多少条 |
| `capture.ocr_scale` | 2.0 | OCR 前的放大倍数 |
| `capture.ui_hover` | true | UI 模式是否"鼠标停哪翻哪" |
| `capture.inject_ports` | 9222, 9223, 9400, 9515 | Electron 注入探测端口 |
| `capture.auto_fallback` | false | 读不到文本时是否自动切 OCR（**默认关，模式由你选**） |
| `memory.round_mb` | 48 | 每轮扫多少 MB |
| `memory.round_seconds` | 2 | 每轮最长几秒（防止拖慢目标程序） |
| `memory.skip_system_modules` | true | 跳过不属于程序自己目录的 DLL |
| `overlay.font_size` / `text_color` | 11 / `#FFE066` | 译文字号与颜色 |

---

## 常见问题

**Q：译文一直不出来？**
先跑 `python main.py --selftest`，确认窗口列表、OCR 引擎、翻译配置都正常；再把日志区的内容看一眼 ——
多数是"没锁定窗口""模式选错""窗口最小化""Electron 没带调试端口"。

**Q：OCR 认不全 / 乱码？**
调大 `capture.ocr_scale`（2.0 → 3.0），或换更大的游戏窗口分辨率；纯图片字太小确实会漏。

**Q：网页模式连不上？**
浏览器必须**带 `--remote-debugging-port` 启动**，已经开着的 Edge 不接受参数。
用「启动翻译浏览器」最省事（独立配置，不动你原来的浏览器）。

**Q：内存模式扫出来的东西跟屏幕对不上？**
内存里没有坐标，它只是"这个进程里出现了这句话"。它读的可能是后台数据、预加载文本，
所以这个模式定位是**辅助查词**，不是"所见即所翻"。要精确对齐画面请用 OCR。

**Q：会不会被游戏反作弊封号？**
内存模式只读内存、不注入，但**带反作弊的联机游戏请勿使用**；单机游戏一般无风险。
图形 Hook 需要注入 DLL，风险更高，自己评估。

**Q：很卡 / 目标程序掉帧？**
调小 `capture.max_new_per_round`、把 `capture.interval_ms` 调大（如 1200），
内存模式把 `memory.round_mb` 降到 16、`round_seconds` 降到 1。

---

## 已知限制

1. 自渲染画面（Unity / DOSBox / 复古 DOS 游戏）读不到控件文字 → 用 OCR 模式；
2. 内存模式拿不到屏幕坐标，只能按文本翻译并写日志；
3. 图形 Hook 必须有注入 DLL，纯 Python 做不到；
4. `iframe` 内容、纯图片文字，网页模式覆盖不到；
5. 翻译质量取决于免费接口，专业术语请用 `term_map.json` 定译。

---

## 目录结构

```
translate_db/
├─ main.py                  入口 / 环境自查
├─ config.example.json      配置模板（复制成 config.json 后填 key）
├─ term_map.json            术语表（原文 → 定译，优先级最高）
├─ model_001.md             项目需求文档
├─ 启动翻译工具.bat          一键启动
├─ 启动VS Code注入模式.bat    带调试端口启动 VS Code
├─ app/
│  ├─ app.py                主界面（tkinter）
│  ├─ controller.py         抓取 → 翻译 → 渲染 调度线程
│  ├─ cdp.py                DevTools 协议客户端（纯标准库）
│  ├─ translator.py         翻译后端 + 词库 / 缓存 / 术语
│  ├─ tm.py                 词库 tm.json 读写
│  ├─ overlay.py            透明置顶穿透悬浮层
│  ├─ msaa.py               MSAA（读可访问树）
│  ├─ winutil.py            Win32 封装（窗口枚举 / DPI / 置顶穿透）
│  ├─ config.py             配置读写
│  └─ engines/              抓取引擎：web / ocr / uia / memory / gfx
└─ tools/                   自测与诊断脚本
```

---

## 许可与免责

仅供学习与个人使用。翻译内容来自第三方免费接口或你自配的模型，准确性与可用性不作保证。
在带反作弊机制的网络游戏中使用任何 Hook / 内存读取类功能，风险自负。
