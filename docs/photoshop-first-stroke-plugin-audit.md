# PS 取色后首笔丢压感：插件争用审计与判定协议

> 状态：**取证 + 判定协议**（未在真实 Win10 + 数位板环境复现，见 §6）
> 关联：`docs/photoshop-wintab-pressure-fix-plan.md`（v4.0 方案）、commit `5fc740e`（v1.8.9 首笔修复）
> 触发场景：WinTab（`UseSystemStylus 0`）、驱动关闭 Windows Ink、笔在 Colorink 选色后移回 PS 画布，**第一笔** 丢压感（100% 粗斑 / 折线），第二笔恢复。

---

## 0. 结论摘要

1. "插件冲突"值得查，且本机已找到**可量化证据**：多个第三方 CEP 面板在**用 `setInterval` + `ExtendScript` 定时敲 Photoshop 主线程**，轮询间隔 100 / 150 / 400 / 500 ms 不等。PS 的 WinTab 压感包由同一个主线程取走 —— 这些 interval 就是"首包被吞"的窗口。
2. 但机制有两条，必须分开验证，不能一句"插件冲突"盖过去：
   - **M1 主线程拥塞**（插件/面板）：**概率性**，与打开的面板数量、轮询频率正相关。
   - **M2 前台丢失 → WinTab 上下文 WTEnable(FALSE/TRUE) 重建**：**确定性**，只要 PS 失去过一次前台，下一笔的开头必然没有压感包。插件在这里的角色是"可能自己抢一次前台"（例如带透明覆盖窗口的悬浮面板）。
3. 有一个前提必须先核对：**"Win10" 这个判断很可能是错的**（本机注册表 `ProductName` = `Windows 10 Pro`，但 `CurrentBuild = 26200` = Windows 11 25H2）。若出问题的机器其实 ≥ 22000，那两台机器的差异只剩软件环境（插件 / 绿色版 / 驱动版本），插件怀疑直接升到第一位。见 §2。

---

## 1. 本机（开发机）插件审计

审计对象：`D:\Program Files\Adobe Photoshop 2020\Required\CEP\extensions\`（绿色版把第三方 CEP 塞进 PS 自带目录）

| 面板 | Bundle / 目录 | 定时器 | 每次干什么 |
| :--- | :--- | :--- | :--- |
| ColorinkBridge（我们自己） | `com.colorink.bridge`（用户级 CEP） | `setInterval(poll, 100)` | `STATE_EVERY = 1` → **每 100 ms 一次 state evalScript**（`core/photoshop_script_bridge.py:247`、`:389`） |
| ttsstt（`com.shortcut.zoom`） | `ttsstt/` | `setInterval(pollPhotoshopState, 150)` | 源码注释自述"每 150ms 轮询一次 PS 状态，响应迅速且无性能开销" |
| HueTriPicker | `HueTriPicker/` | `setInterval(syncFromPS, 400)` | 每 400 ms evalScript 同步 PS 状态 |
| Brush Properties（brushbar） | `brushbar/` | `setInterval(pollBrushOptions, 500)` + `setInterval(doEval, 1000)` | 两个定时 evalScript |
| F_Record | `com.f_know.f_record.cep/` | 2 × `setInterval(function(){…})` | 录制/回放相关轮询 |
| Coolorus 2.5.9 | `com.moongorilla.coolorus2/` | 按需（无常驻 PS 轮询） | 写色、读色时才 evalScript |
| Anastasiy Butler（`com.adobe.Butler.backend`） | `Required/www-butler/`（前端 HTML + `manifest.json`） | — | `overlay_type: window`、`use_transparency: true` 的**透明覆盖窗口** |

C 端加载证据：`%TEMP%\cep_cache\` 下存在 `PHXS_20.0.10_com.adobe.Butler.backend`、`PHXS_20.0.10_com.colorink.bridge`、`PHXS_21.2.6_com.colorink.bridge` —— 说明这些扩展**确实被加载运行过**（不是只躺在硬盘上）。

> ⚠️ **装了 ≠ 在跑。** CEP 面板的 JS 只有在该面板被打开（或 manifest 里 `<StartOn>` 事件拉起）时才执行。判定前必须先确认客户机上 `窗口 > 扩展功能` 里**真正开着**哪几个面板。Colorink 自己的桥面板是 `<StartOn>applicationActivate</StartOn>`，必定在跑。

### 为什么这能造成"首笔丢压感"

- PS 的 ExtendScript 引擎跑在**主线程**上；每次 `evalScript` 期间消息泵不抽。
- WinTab 的包在 PS 侧由主线程取（典型是响应 `WT_PACKET` 才 `WTPacketsGet`）。
- 落笔瞬间若撞上某次 `evalScript`，起手几个包（含压力/位置）要么晚到要么被驱动队列丢弃 → 表现为**折线起点 / 满压感粗斑**，随后恢复。
- 面板越多、interval 越密，撞上的概率越高 —— 这正好解释"插件装得多的机器翻车、干净机器没事"，**而与 Win10/Win11 关系不大**。

---

## 2. 先核对前提：那台机器真的是 Windows 10 吗？

本机实测（就是装着 PS 2020 + 上面这堆面板的这台）：

```
ProductName    : Windows 10 Pro      ← 微软在 Win11 上一直没改这个值
DisplayVersion : 25H2
CurrentBuild   : 26200               ← 22000 及以上 = Windows 11
UBR            : 9445
```

**判定命令**（在出问题的那台机器上跑）：

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' |
  Select-Object ProductName, DisplayVersion, CurrentBuild, UBR
```

- `CurrentBuild >= 22000` → 那台其实是 Windows 11。**"只在 Win10 丢"这个前提不成立**，两机差异只剩软件环境（插件 / 绿色版 PS / 驱动版本）→ 插件假设排第一。
- `CurrentBuild` 是 19xxx → 确实是 Win10，M2 的 OS 差异说法保留权重。

---

## 3. 两条机制与判别预测

| | M1 主线程拥塞（插件/面板） | M2 前台丢失 → WinTab 上下文重建 |
| :--- | :--- | :--- |
| 触发 | 任何一次 evalScript 撞上落笔 | PS 失去前台那一次之后的**下一笔** |
| 复现特性 | **概率性**，面板越多越勤越容易 | **确定性**，每次失焦后必现 |
| 与 Colorink 的关系 | Colorink 只是众多轮询者之一 | Colorink 若抢到前台就是元凶；插件悬浮窗同样能抢 |
| 关键预测 | 关掉其它面板后显著好转 | **不用 Colorink**，alt-tab 出去再回来落笔，照样丢 |
| 其它征兆 | 画到一半也可能偶发顿/丢包 | 取色时 PS 标题栏会变灰；失焦后第一笔必丢 |

---

## 4. 判定协议（在出问题的那台机器上，按顺序做，只看"第一笔"）

**T1 · 分离测试（最关键，先做这个）**
1. 完全退出 Colorink 进程（不是最小化）。
2. 在 PS 里 alt-tab 到任意窗口，再切回 PS，落笔。

- 仍然丢 → **与 Colorink 无关**，是 PS + 驱动 + 插件栈 → 做 T2。
- 不丢 → 与"取色往返"这条路径绑定 → 做 T3。

**T2 · 插件二分**
1. `窗口 > 扩展功能` 里把第三方面板**全部关闭**（Coolorus / HueTriPicker / brushbar / ttsstt / F_Record / Butler / Color Wheel），**不要卸载**；重启 PS。
2. 重跑 Colorink 取色 → 落笔。

- 明显好转/消失 → M1 成立，插件轮询是主因。再**一次开一个**（先开 Anastasiy 的 Color Wheel）复现收敛到具体面板。
- 完全没变 → M1 权重下调，转 M2 / 驱动侧（T4）。

**T3 · 焦点观测**
取色时盯 PS 标题栏是否变灰；或 `run_pen_debug.bat`（`COLORINK_DEBUG_PEN=1`）跑一次完整"取色 → 移回 → 落笔"，把日志交回来。日志里 `TabletMove / 拖拽 blank` 的时序能定位取色路径与焦点变化。

**T4 · 驱动侧**
- 控制面板 → 笔和触控：关闭"长按视为右键"（会在接触头几十像素挂起采样）。
- 确认 `PSUserConfig.txt`（本机 PS2020/CC2019 里都是这两行）：
  ```
  WarnRunningScripts 0
  UseSystemStylus 0
  ```
- 记录数位板品牌 + 驱动版本。**Wacom WinTab 通常会置 `VK_LBUTTON`；Huion / XP-Pen 常常不置** —— 这直接决定 Colorink 的"作画中"门控在整笔期间是否生效（见 §5.1）。

---

## 5. Colorink 侧可立即加固的点（读完代码后的具体发现）

### 5.1 "假定作画"窗口只有 1500 ms，很可能在用户落笔前就过期了

- 写色时 `core/memory_sync.py:_note_ps_write` 调 `note_color_applied(1500)`，并把 `drawing.txt` 置 1。
- 桥面板据此**跳过 state evalScript**，条件写在 `core/photoshop_script_bridge.py:381`：`Date.now() - drMtime < 1500`。
- 而真实动作序列是：松笔确认颜色 → 笔移回画布 → 落笔。**1.5 s 经常在落笔之前就用完了**，门控关掉、100 ms 轮询恢复，第一笔正好撞上。
- 建议：把该窗口拉到覆盖整段"取色往返"（例如 3000–4000 ms），或改成"**直到取色后的第一笔真正画完再解除**"（用一个 `first_stroke_pending` 标志，见到 `VK_LBUTTON` 按下→抬起后清除）。

### 5.2 Colorink 自己面板的轮询偏密

`STATE_EVERY = 1`（100 ms，为"即时颜色回读"）：把 PS 当前前景/背景色镜像回 Colorink。降到 250–300 ms 肉眼看不出差别，主线程占用直接砍到 1/3，与其它面板叠加时余量更大。

### 5.3 把"主线程被占"变成可测量的事实

在 state evalScript 上记 round-trip 耗时（发出 → 回调），超过阈值（如 50 ms）就写一行日志。这样客户机上的"PS 主线程拥塞"是可测数据，而不是推理。这是判断 M1 是否成立的直接证据。

---

## 6. MagicPicker（Anastasiy Color Wheel）静态审计

对象：`%APPDATA%\Adobe\CEP\extensions\MagicPickerTrialCC2014`（2026-09-11 13:03 装入本机）

| 事实 | 值 |
| :--- | :--- |
| 类型 | `<Type>Panel</Type>` —— **停靠式面板**，长在 PS 主窗口内部，**不是浮窗/覆盖窗口** |
| 生命周期 | `<AutoVisible>true</AutoVisible>`（随 PS 启动显示）；无 `<StartOn>` 事件 |
| 宿主接口 | `<ScriptPath>./MagicPickerTrial.jsx</ScriptPath>` → 156 KB **JSXBIN**（编译过的 ExtendScript 引擎）被载入 PS 脚本引擎 |
| 面板 JS（`scripts/all.js`，667 KB，混淆） | 与 PS 的交互只有 1 处 `CEP.toCMYK` 派发 + 2 处 `evalScript`；**无 setInterval 轮询 PS**（仅有的两个是 jQuery 动画 tick 和 tooltip 定位）；**无 GlobalShortcut / 无焦点调用 / 无 app.notifiers** |

**结论：弱嫌疑。**

1. 它**没有能力抢 PS 的前台**（Panel 型，不是浮窗）→ 触发不了 M2（失焦 → WinTab 上下文重建）那条"确定性丢压感"的链。这与本机实测"跑这个插件没什么影响"一致。
2. 它**不周期性敲 PS 主线程**（面板侧无轮询）→ M1 的直接证据也不成立。
3. 唯一盲区是那个 JSXBIN 引擎：明文搜不到 `scheduleTask` / `notifiers`（JSXBIN 会编码所有标识符与字符串）。它能藏的、且与本问题相关的只有两件事：`app.scheduleTask` 定时循环、`app.notifiers` 事件钩子。
4. **10 秒可判的观察**（无需工具）：
   - MagicPicker 面板开着时，在 Colorink 里取色 —— MagicPicker 的色轮**会不会自动跟着变**？会 ⇒ 引擎在盯前景色（主线程一定有循环或钩子），值得深挖；不会 ⇒ 纯手动面板，可排除。
   - PS 空闲时任务管理器看 CPU 是否 ~0%。

> 真正"插件类"的元凶通常是另外两类：**轮询型 CEP 面板**（§1 表里那些，只有被打开时才跑）和**挂输入钩子/注入 DLL 的软件**（Lazy Nezumi、Astute Graphics、TourBox 类、录屏批注、绿色版 PS 的破解注入）。问客户两句话就能定方向：`窗口 > 扩展功能` 里同时开着哪几个？有没有装第二类？

### 现场取证工具

`tools/ps_probe_env.jsx`（只读，不改设置）：客户在 PS 里 `文件 > 脚本 > 浏览…` 运行，桌面生成 `colorink_ps_report.txt`，内含 PS 版本/构建、**`app.notifiers` 全量**、`PSUserConfig.txt` 真实内容、四处 CEP 目录的扩展清单（BundleId / Type / StartOn）、UXP 与原生 `.8bf` 插件清单。这能把"他到底装了什么、有没有事件钩子"变成事实。

---

## 7. 为什么偏偏是 Colorink 中招（以及修复：笔尖焦点交还）

### 7.1 结构性原因：只有 Colorink 把笔的输入从 PS 手里拿走了

同一台机器、同一套驱动、同一堆插件，别的外部取色软件第一笔有压感、Colorink 没有 ——
这本身就是一次**在故障机上的对照实验**，它把"系统 / 驱动 / 插件"这条线基本排除，
指向 Colorink 与 PS 的交互方式。差在哪：

| | 别人的工具为什么没事 | Colorink 为什么中招 |
| :--- | :--- | :--- |
| 笔点在哪里 | ① 停靠在 PS **自己的窗口里**（CEP 面板型，如 Coolorus / MagicPicker）：PS 始终是活动的顶层窗口；② 或**根本不需要笔点进它的窗口**（全局热键 + 悬停采样，笔一直待在画布上） | 笔必须点进 Colorink 这个**独立的顶层窗口**：笔的输入目标离开了 PS 的画布 |
| WinTab 上下文 | 全程不切换，压感包一直有主 | PS 一旦失去"激活 / 键盘焦点"，就挂起自己的上下文（`WTEnable(FALSE)`），等落笔时才重新握手 —— 而**握手期间到达的第一包恰好是带压力的那一包** |
| PS 主线程 | 剪贴板 / 按键 / UI 自动化那类根本不碰 PS 的脚本引擎 | ColorinkBridge 每 100 ms 一次 `evalScript`（`STATE_EVERY = 1`），ExtendScript 与 WinTab 取包在同一个主线程上 |

两条机制（M1 主线程拥塞 / M2 上下文被挂起）**都可以在没有插件的情况下成立**。
这也解释了"别的工具没事、插件装了一堆的机器更容易翻车"：插件只是把 M1 的窗口变宽，
把"高概率"变成"必定"。

### 7.2 修复：`core.foreground.StylusFocusGuard`（笔尖焦点交还）

思路：既然别家工具赢在"从不把激活/焦点交出去"，那就**在交互结束、笔还在往画布移动的路上，
主动把激活 + 键盘焦点还回去** —— WinTab 上下文在落笔之前就恢复完毕，第一包就是好的。
不改任何既有实现路径，只在两侧各加一个动作。

- `capture()`：笔尖按下**之前**记住当时的前台窗口（前台是本进程时直接放弃 —— 没什么可还的）。
- `restore()`：笔抬起后把 `BringWindowToTop` + `SetForegroundWindow` + **`SetFocus`** 交给那个窗口。
  - `SetFocus` 不能省：Wintab 的上下文焦点跟着**焦点窗口**走；而且对"本来就是前台"的目标，
    `SetForegroundWindow` 不会产生新的 `WM_ACTIVATEAPP`，只有 `SetFocus` 能把 `WTI_FOCUS` 还给画布。
  - 前台锁：跨进程调 `SetForegroundWindow` 会被系统拒绝，所以先 `AttachThreadInput` 挂上当前前台线程，调完再解挂。
- 安全边界（三条都满足才动手）：① `capture` 时前台不是我们自己；② `restore` 时前台**或**键盘焦点确实落在我们手上（用户没跑去用别的软件）；③ 目标窗口仍存在、可见、未最小化。
- 只挂在**笔**事件上（`ui/window/layout.py` 的 `eventFilter` + `ui/color_picker_overlay.py` 的 `start/stop`），鼠标交互一律不碰 —— 否则用笔点数值框之后就没法打字了。
- 开关：`COLORINK_FOCUS_GUARD=0` 关闭；`COLORINK_FOCUS_GUARD=force` 无条件交还（诊断用：验证"提前恢复上下文"到底有没有用 —— 如果普通模式下它一直是 no-op，说明我们本来就没抢到过激活，问题就只剩 M1）。
- 可观测：`COLORINK_DEBUG_PEN=1`（`run_pen_debug.bat`）时每次笔交互会打印 `[focus] capture ok/skip …`、`[focus] restore skip: …` / `restore -> …`，客户机上"到底有没有采集到、为什么没还"直接可见，不用猜。

测试：`tests/test_focus_guard.py`（采集/交还/安全边界/接线，13 项）。

### 7.3 仍然要客户机回答的两个问题

1. **他现在用的是哪个版本？** 1.8.9 之前没有孤儿 UP 修复、没有 `WS_EX_NOACTIVATE` 的强制加固、
   也没有 `WM_MOUSEACTIVATE` 拦截（这条还在工作区里，未提交、未发布）—— 老版本中招是**必然**的。
   升级到含本轮修复的构建再测，是成本最低的一步。
2. **他的 `noFocusMode` 是不是开着的？** 关掉的话，Colorink 点一下就会真的抢走 PS 的激活。

---

## 8. 类 2 / 类 3 的结构性修复（本轮落地）

### 8.1 类 2-a：`SetCursorPos` 注入的合成鼠标移动（**溯源到一次提交**）

`ui/color_picker_overlay.py::_show_cursor()` 收尾时曾调
`win32api.SetCursorPos(pos+1)` / `SetCursorPos(pos)` "催"前台画图软件重画笔刷光标。
这两次是**注入的合成鼠标移动**，落点是光标所在窗口 —— 取色时正是 **PS 的画布**；
时机正是"取色确认、笔尖开始往回走"。WinTab 下的 Photoshop 靠"最近的输入是笔还是
鼠标"做仲裁，一次合成移动足够让接下来的一笔按鼠标处理：满压感粗斑，第二笔恢复。

证据链：

```
$ git log -S"SetCursorPos" --oneline -- ui/color_picker_overlay.py
b84d26b feat(sai): refresh SAI UI after colour writes; harden no-focus and user-hidden behaviour
  → 2026-08-20 才加进去；_hide_cursor/SetSystemCursor 是 v1.2.0 就有的旧东西
```

同一时期存在过一个 `tests/test_picker_input_guard.py`（**源码已删除，只剩 `.pyc`**），
它的断言原文是：

- `_show_cursor must never call win32api.SetCursorPos (prevents first stroke pressure loss).`
- `SetCursorPos was unexpectedly called!`

也就是说这条结论**此前已经到过**，测试写了却没落地，注入一直留在代码里。

处置：默认不再注入（回退开关 `COLORINK_PICKER_CURSOR_NUDGE=1`），并把那 4 条测试补回来
（`tests/test_picker_input_guard.py`）。

**待客户确认的时间线**：这个毛病是不是 **2026-08-20 之后**的版本才开始出现？若是，基本定案。

### 8.2 类 2-b：钩子欠账可能吃掉"下一笔真实的 UP"

`core/picker_hook.c` 的欠账计数只要是"还欠着一个 UP"就吞**下一个** UP。若那笔欠账永远
没到（驱动丢包 / 按下抬起跨了窗口切换），被吃掉的就会是**下一笔真实笔画自己的 UP** ——
PS 收到"只有按下没有抬起"，一笔状态错乱、下一笔自愈。

处置：取色已结束（`g_active == 0`）时，若来了**新的按下**且欠账已超过 `DEBT_STALE_MS`
（150 ms，用来区别于同一次按压的重复 DOWN），作废那笔欠账，让新一笔的 UP 正常通过。
DLL 已按 TESTING.md C6 的命令重编译（尺寸仍 106496、导出不变、md5 `9cae3f0c…` → `0fecf3a7…`）。

### 8.3 类 2-c：欠账可观测

`COLORINK_DEBUG_PEN=1` 时打印 `[picker] hook kept installed: still owes N button-up(s)`，
以及欠账超时强拆的那条 —— 客户机上一眼能看出欠账有没有泄漏。

### 8.4 类 3：对画图软件无条件重申激活 + 焦点

`StylusFocusGuard.restore()` 原先要求"前台或键盘焦点确实落在我们手上"才动手。但 WinTab
的上下文焦点可能在**驱动层**就被换走了，我们这边看不到（窗口没激活、`GetFocus()` 也是 0）。
现在：目标是画图软件（PS / SAI / CSP / UDM，按 capture 到的 PID 解析 exe 判定）时不做
这个检查 —— 重申是幂等的（本来就是它的话两个调用什么也不会发生）；非画图软件仍然严格。

---

## 9. 本轮没有做到的事（如实记录）

- **没有在真实 Win10 + 数位板 + PS 上复现**：本机是 Windows 11 25H2（build 26200），且**没有安装任何数位板驱动**（无 Wacom / Huion / XP-Pen 目录，`System32\Wintab32.dll` 只是系统自带桩），拿不到压感包。
- 因此本文给出的是**可判定的实验设计 + 本机可量化证据**，不是"已复现 → 已定位"。
- 外部旁证（Adobe 社区长期存在的同类帖，未取到正文）：[Loss of brush pressure on first brush stroke](https://community.adobe.com/t5/photoshop-ecosystem-discussions/loss-of-brush-pressure-on-first-brush-stroke/m-p/11319074)、[[Photoshop CC2020] Broken Pressure on first stroke only, using Wintab](https://community.adobe.com/questions-712/photoshop-cc2020-broken-pressure-on-first-stroke-only-using-wintab-help-1089161)。说明"失焦后第一笔没压感"是 PS 自身的老毛病，Colorink 只是常见的触发者/放大器。
- 另一个未排除的变量：本机 PS 2020 是**绿色版**（`..._CC_2019_20.0.10.28848_Green` / `D:\Program Files\Adobe Photoshop 2020`），破解注入的加载器本身就是输入栈上的一个未知量。干净的零售版 PS 是对照组之一。
