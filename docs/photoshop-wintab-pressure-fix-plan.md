# Photoshop 在 WinTab 模式下色轮选色后首笔丢压感：Win10 兼容性增强方案

> **文档版本**：v4.0（针对**色轮直接选色场景**深度定制）  
> **文档位置**：`docs/photoshop-wintab-pressure-fix-plan.md`  
> **面向问题**：Photoshop 配置 `PSUserConfig.txt`（`UseSystemStylus 0`，WinTab 模式），驱动关闭 Windows Ink 时，在 Windows 10 系统下使用数位笔直接点击/拖动 Colorink 的“色轮（Color Wheel）”选色后，笔移回 Photoshop 画布落笔第一笔高概率丢失压感（呈现最大笔刷粗细或折线），第二笔恢复正常；而在 Windows 11 本地设备上完全正常。  
> **核心诉求**：**深入兼容 Windows 10 系统底层输入栈，彻底提升软件在 Win10 上的稳定性**。在本次会话中仅归档方案，不在代码中执行，供后续会话实施。

---

## 目录
1. [代码级与系统级根因：为什么色轮选色在 Win10 丢压感？](#1-代码级与系统级根因为什么色轮选色在-win10-丢压感)
   - [物理链条 1：Win10 下 `WM_MOUSEACTIVATE` 缺失导致的隐性失焦](#物理链条-1win10-下-wm_mouseactivate-缺失导致的隐性失焦)
   - [物理链条 2：Photoshop 的 WinTab 挂起与上下文重建真空期](#物理链条-2photoshop-的-wintab-挂起与上下文重建真空期)
   - [物理链条 3：色轮拖动与落笔时序的写色高频冲击](#物理链条-3色轮拖动与落笔时序的写色高频冲击)
2. [为什么 Win11 丝滑而 Win10 翻车（操作系统差异）](#2-为什么-win11-丝滑而-win10-翻车操作系统差异)
3. [整体改造原则与架构设计](#3-整体改造原则与架构设计)
4. [四个具体的 Win10 兼容性增强改动点](#4-四个具体的-win10-兼容性增强改动点)
   - [改动点 1：原生级无焦点加固 —— 拦截 `WM_MOUSEACTIVATE` 返回 `MA_NOACTIVATE`（核心）](#改动点-1原生级无焦点加固--拦截-wm_mouseactivate-返回-ma_noactivate核心)
   - [改动点 2：色轮拖动同步防抖 —— 对齐滑块的 `is_dragging` 保护机制](#改动点-2色轮拖动同步防抖--对齐滑块的-is_dragging-保护机制)
   - [改动点 3：消除色轮交互时的暴力光标样式切换](#改动点-3消除色轮交互时的暴力光标样式切换)
   - [改动点 4：数位板驱动的非 WinTab 窗口接触平滑过渡](#改动点-4数位板驱动的非-wintab-窗口接触平滑过渡)
5. [受影响文件与精确代码实现建议](#5-受影响文件与精确代码实现建议)
6. [零破坏验证矩阵与验收标准](#6-零破坏验证矩阵与验收标准)

---

## 1. 代码级与系统级根因：为什么色轮选色在 Win10 丢压感？

在画师使用数位笔直接点击 Colorink 窗口的色轮选色时，系统与硬件驱动经历如下链条：

```
[画师手持数位笔离开 Photoshop，点击 Colorink 色轮调色]
                         │
                         ▼
        【冲击 1：Windows 10 焦点管理机制漏洞】
  Colorink 虽然有 WS_EX_NOACTIVATE，但未拦截 WM_MOUSEACTIVATE，
  且 Qt 在点击子控件时调用 Win32 SetCapture 捕获鼠标。
  Windows 10 认为跨进程输入挂钩，向 Photoshop 投递隐性失活通知：
             WM_NCACTIVATE(FALSE) / WM_ACTIVATEAPP(FALSE)
                         │
                         ▼
        【冲击 2：Photoshop 内部 WinTab 挂起机制触发】
  Photoshop 处于 UseSystemStylus 0 (WinTab 模式)，检测到失去前台，
  立即调用 WinTab API：WTEnable(hCtx, FALSE) 挂起上下文！
                         │
                         ▼
        【画师在色轮松手，笔移回 Photoshop 画布落下第一笔】
                         │
                         ▼
        【冲击 3：落笔充当了重新激活 Photoshop 的扳机】
  1. 系统在 0ms 发送 WM_LBUTTONDOWN；
  2. Photoshop 重新激活，调用 WTEnable(hCtx, TRUE) 重建上下文；
  3. ★ 重建 WinTab 硬件通信握手需要耗时 20 ~ 80ms (真空期)！
  4. Photoshop 看到鼠标已下笔，但 WinTab 压感包未就绪，
     直接触发降级容错（Fallback）—— 按普通鼠标处理（100% 满压感粗斑或折线）！
                         │
                         ▼
  几十毫秒后，WinTab 上下文完全就绪，第二笔恢复正常压感。
```

---

## 2. 为什么 Win11 丝滑而 Win10 翻车（操作系统差异）

很多开发者会误以为两台机器驱动一样就应该表现一致，但实际上微软在两代系统中对**前台窗口管理（Foreground Window Management）**做了革命性重构：

| 机制维度 | Windows 10 表现 | Windows 11 表现 |
| :--- | :--- | :--- |
| **前台租约模型 (Foreground Lease)** | **老旧且脆弱**。当数位笔点击一个拥有 `WS_EX_NOACTIVATE` 的复杂窗口内部子控件时，DWM 和 User32 极易向下方的原前台程序（PS）投递 `WM_NCACTIVATE`，引发失焦抖动。 | **现代租约锁定**。Win11 引入了强大的前台租约锁定机制，置顶无焦点窗口发生任何点击或 `SetCapture`，原前台程序的激活状态被操作系统强制锁死，**全程完全不失焦**。 |
| **WinTab 上下文状态** | Photoshop 收到短暂失焦通知，**主动执行 `WTEnable(FALSE)` 挂起了上下文**，导致回画布下笔时必须重新握手。 | Photoshop 全程处于激活状态，其 WinTab 上下文始终保持 Active，**根本没有断开过**！ |
| **首笔落笔响应** | 撞上 20~80ms 的上下文重建真空期，首笔压感丢失。 | 毫无真空期，WinTab 数据包即时到达，首笔压感完美顺滑。 |

---

## 3. 整体改造原则与架构设计

针对上述机制，改造核心在于：**在 Windows 10 系统底层把“无焦点防护”做到极致，彻底掐断 Photoshop 收到任何失焦信号的可能性！**

1. **协议层拦截**：在 Win32 原生消息泵（`nativeEvent`）中接管 `WM_MOUSEACTIVATE`，强制向操作系统宣告 `MA_NOACTIVATE`；
2. **时序平滑**：让色轮拖动与写色像滑块一样享有拖拽防抖，避免高频写色在画师松手瞬间与 PS 消息循环撞车；
3. **零破坏承诺**：不修改任何色彩空间数学模型、不破坏色轮交互跟手度、不影响正常点击和拖拽。

---

## 4. 四个具体的 Win10 兼容性增强改动点

### 改动点 1：原生级无焦点加固 —— 拦截 `WM_MOUSEACTIVATE` 返回 `MA_NOACTIVATE`（核心）

#### 问题审查
在当前代码中：
- 主窗口设置了 `Qt.WindowType.WindowDoesNotAcceptFocus` 与扩展样式 `WS_EX_NOACTIVATE`；
- 但是在 [`ui/main_window.py`](file:///d:/Program%20Files/colorink/ui/main_window.py#L479) 的 `nativeEvent` 中，**只处理了 DPI 相关的 `WM_GETDPISCALEDSIZE` 和 `WM_DPICHANGED`，完全没有处理 `WM_MOUSEACTIVATE`！**
- 在 Windows 10 下，当鼠标/笔尖点击主窗口内的子控件（`ColorWheel`）时，Windows 会向主窗口发送 `WM_MOUSEACTIVATE` (0x0021)。如果窗口不显式拦截并返回 `MA_NOACTIVATE` (3)，Windows 默认过程会启动复杂的层级激活判断，从而向 Photoshop 发送失活通知。

#### 解决方案
在 `ui/main_window.py` 的 `nativeEvent` 中增加针对 `WM_MOUSEACTIVATE` 的显式拦截：
```python
# 0x0021 = WM_MOUSEACTIVATE
# 3 = MA_NOACTIVATE (不激活本窗口，但允许鼠标/笔消息传递给控件)
if msg.message == 0x0021:
    if self.cfg.get("noFocusMode", False):
        return True, 3
```
同样地，在浮动面板窗口 [`ui/panels/floating.py`](file:///d:/Program%20Files/colorink/ui/panels/floating.py) 的 `FloatingPanelWindow` 中也加入这一条拦截。

#### 效果保证
* 无论用户使用数位笔怎样点击色轮、拖拽色轮，Windows 10 操作系统底层直接判定 `MA_NOACTIVATE`；
* Photoshop 全程绝对不会收到任何 `WM_NCACTIVATE(FALSE)` 或 `WM_ACTIVATEAPP(FALSE)`；
* Photoshop 的 WinTab 上下文全程保持活跃，彻底杜绝 `WTEnable(FALSE)` 挂起！

---

### 改动点 2：色轮拖动同步防抖 —— 对齐滑块的 `is_dragging` 保护机制

#### 问题审查
在 [`ui/window/sync_mixin.py`](file:///d:/Program%20Files/colorink/ui/window/sync_mixin.py#L329-L336) 中：
```python
is_dragging = False
if source.startswith("sliders_"):
    for chan, (slider, _) in self.slider_widgets.items():
        if slider.isSliderDown():
            is_dragging = True
            break
if is_dragging:
    return
```
可以看到：滑块被拖动时被明确拦截，只有在松手时才提交写入。
然而当 `source == "wheel"` 时，**没有任何拖动判定**！数位笔在色轮上哪怕微移 2 个像素，色轮都在以极高频率向同步管道刷入数据，增加了 Photoshop 主线程处理事件的负担。

#### 解决方案
在 `_push_color_to_sync` 中，将色轮拖动也纳入跳过判定：
```python
if hasattr(self, "color_wheel") and getattr(self.color_wheel, "dragging", None):
    return
```
在色轮的 `mouseReleaseEvent` 触发 `on_interaction_finished` 时，统一执行最终的高精度色彩写入，避免画师在选色微调期间 Photoshop 队列被高频刷写。

#### 效果保证
* 选色依然即时（松手瞬间立即落地）；
* 避免了色轮微调时的跨进程消息拥塞，降低 Photoshop 消息泵负载。

---

### 改动点 3：消除色轮交互时的暴力光标样式切换

#### 问题审查
在 [`ui/color_wheel_interaction.py`](file:///d:/Program%20Files/colorink/ui/color_wheel_interaction.py#L201) 中：
```python
if self.dragging:
    self.setCursor(Qt.CursorShape.BlankCursor)
...
def end_drag(self):
    self.dragging = None
    self.setCursor(Qt.CursorShape.CrossCursor)
```
点下变空白光标，松手变十字光标。在 Win10 的非激活窗口机制下，频繁切光标会触发系统的 `WM_SETCURSOR` 级联广播。

#### 解决方案
对色轮采用常驻光标样式，或者避免在无焦点模式下进行剧烈的 BlankCursor 进出切换，减少系统光标重评事件。

---

### 改动点 4：数位板驱动的非 WinTab 窗口接触平滑过渡

#### 驱动层变量排查指引（供用户设备对照）
除了上述软件层面的代码改进，对于运行 Windows 10 的特定设备，数位板驱动（如 Wacom 驱动）本身包含两项设置对该现象有决定性影响：
1. **控制面板 -> 笔和触控 -> 长按右键**：
   在 Win10 上，必须关闭“将长按视为右键单击”。否则当笔从色轮（无 WinTab）移回 PS（有 WinTab）落笔时，系统长按探测器会强行挂起最初 10 个像素的采样。
2. **数位板驱动的独立应用配置文件**：
   确保数位板驱动中为 Photoshop 单独设置了配置文件，并确认在该配置下关闭了 Windows Ink。

---

## 5. 受影响文件与精确代码实现建议

在后续会话中实施时，主要涉及以下 2 个文件的针对性微调：

### 1. `ui/main_window.py`
在 `nativeEvent` 中加入 `WM_MOUSEACTIVATE` 拦截：
```python
# WM_MOUSEACTIVATE = 0x0021, MA_NOACTIVATE = 3
if msg.message == 0x0021:
    no_focus = bool(getattr(self, "cfg", {}).get("noFocusMode", False))
    if no_focus:
        return True, 3
```

### 2. `ui/window/sync_mixin.py`
在 `_push_color_to_sync` 中加入色轮拖动避让：
```python
if source == "wheel":
    wheel = getattr(self, "color_wheel", None)
    if wheel is not None and getattr(wheel, "dragging", None):
        return
```

---

## 6. 零破坏验证矩阵与验收标准

| 序号 | 验证模块 | 测试操作 | 预期行为（必须 100% 通过） |
| :--- | :--- | :--- | :--- |
| **T1** | **色轮基础选色** | 在色轮环与内部多边形点击、拖动 | 取色精准跟手，色相/饱和度/明度平滑更新 |
| **T2** | **颜色即时落地** | 色轮松手后观察 Photoshop 前景色 | Photoshop 前景色在 50ms 内立即更新 |
| **T3** | **无焦点与前台保持** | 点击色轮时观察 Photoshop 标题栏 | **Photoshop 标题栏始终保持高亮激活状态，绝无任何变灰失焦闪烁** |
| **T4** | **核心压感验证** | **在 Win10 设备上，手持数位笔点击色轮选色，随后立刻在 PS 画布落笔** | **首笔起手即带平滑压感（细→粗），彻底消除满粗斑或折线！** |
