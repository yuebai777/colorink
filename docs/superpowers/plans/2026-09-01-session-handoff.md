# Colorink 会话交接（2026-09-01）

> **给下一个会话**：本会话已很长，此文档是全部上下文的压缩版。工作区是 `D:\Program Files\colorink`（PyQt6 桌面拾色器，Windows）。先读本文件，再在需要细节时读 `docs/superpowers/plans/2026-09-01-window-layout-and-panelization.md`（方案全过程记录）。

> **2026-09-01 续会话更新**：B-4 拖拽重排、B-6 一键复位、**B-5 浮出/停靠回来**（调大小 / 置顶开关 / 双击出入 / 拖回落点 / 右键菜单 / 主题皮肤）、以及设置里的「面板布局」卡均已完成。全量测试 **1254 passed / 0 failed**。

## 0. 当前状态（最后一次全量测试）

- `python -m pytest -q` → **1249 passed / 0 failed**（本轮续会话开始时是 1107，+142）；另有 124 个 `error`，**全部是沙箱临时目录权限（`tmp_path`）问题，与本会话代码无关**（换环境或在外网终端跑会消失）。
- **代码未提交**：本会话从头到尾没有执行过 `git commit`。工作区里 `git status` 有大量改动，其中一部分是**用户自己在别的会话/时间做的**（`ui/border_themes.py`、`ui/chrome_opacity.py`、`ui/theme_contrast.py`、`ui/slider_themes.py`、`tests/test_border_themes.py`、`tests/test_background_opacity.py`、`tests/test_settings_contrast.py` 等），**不要碰、不要回滚**。本会话改动的文件见文末清单。
- 运行环境：系统 Python **3.14**，路径 `C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe`（`.venv` 里没有 python.exe）。所有测试/验证都用它。
- 复现用户配置：把 `%APPDATA%\Colorink` 下的 `hotkey-config.json` / `window-config.json` 拷到隔离目录，`os.environ['APPDATA']=隔离目录` 后再 `import ui.main_window`；`window-config.json` 里把 `dpr` 改成 1.0 避免 offscreen 还原放大（用户显示器 dpr=1.5）。

## 1. 本会话完成的东西（按时间）

| 主题 | 内容 | 状态 |
|---|---|---|
| LAB 明度条布局 | 与色块平面 [gap][plane][gap][bar][gap] 等距编排；条上/下沿对齐色块；对齐用 `LabSlider.track_band()`（**不要用 layout margins**，会把窗口高度策略撑爆） | ✅ |
| 三角把手 | 两端不再裁剪；与 Qt 原生把手行程一致；`update_scale` 加 unpolish/polish 修复样式缓存 | ✅ |
| 前景/背景色块簇 | 与色环/圆盘不重叠（净距 +1.5~+4px）、尺寸位置绑定色轮、角落自适应缩小 | ✅ |
| 窗口尺寸联动 | 调窄后高度跟随；色块簇不再跟窗口底跑；高度策略「增长以适配，从不收缩」 | ✅ |
| 渲染性能 | LAB 平面/圆盘、色环切片全部优化：sRGB 查表（线性光 + `srgb_encode_u8` LUT）、去掉每像素 cos/sin（`cos(atan2)=x/r`）、极坐标网格缓存、色域边界 profile 缓存、float32；**全精度拖动**：≤340px 色轮全精度，LAB 平面 ≤300px 全精度；自适应精度（按实测 ms/像素，预算 7ms） | ✅ |
| 布局重构 | `ui/window_layout.py`（纯数据几何，色环唯一公式出处）；`apply_theme` 拆成 `apply_layout`（几何）/ `apply_style`（样式），缩放只跑 layout；`_settle` 落定钩子 | ✅ |
| B-2 状态解耦 | `ui/color_session.py`（交互计数、槽位/透明命令信号），4 处控件对宿主引用切断 | ✅ |
| B-1 面板模型 | `ui/panels/spec.py`（PanelSpec）/ `registry.py`（10 个面板，picker aspect=1.0，lightness/swatches 为卫星）/ `tree.py`（Leaf/Tabs/Split 可序列化停靠树，含 `resizable`、`spacing`、`margins`、`pages`） | ✅ |
| B-3 面板宿主 | `ui/panels/host.py`（PanelHost：树→QSplitter/QTabWidget/内容定高堆叠；重挂不重建；`column_hint()` 确定性高度；`mount_changed`）；`ui/panels/store.py`（`cfg[panelLayout]` 持久化，版本号+退化回默认）；`ui/window/panels_mixin.py`；**PanelHost 已是滑块列的真实装配者** | ✅ |
| 高度策略 | 读 hint 前先激活嵌套布局；`column_hint()` 替代不可靠的 `sizeHint()`；增长不收缩（手动调整后窗口稳定，仅最小高度跟随内容） | ✅ |
| B-4 部分 | **可拖动分割**（`slidersSplit`：两列 QSplitter，sizes 持久化）；**页签**（`slidersTabs`：每页 ≤2 组，`Tabs.pages` 升级，旧 `items` 兼容）；两者互斥时页签优先 | ✅ |
| **B-4 收尾：拖拽重排** | `ui/panels/rearrange.py`（纯数据：落点几何 + 树手术）、`ui/panels/drag.py`（16px 抓手 / PanelFrame / 落点指示）、宿主 `set_drag_enabled`+`apply_drop`+`rearranged` 信号、存档 **seed** 归属、开关 `panelDrag`（默认关，关着逐像素不变） | ✅ |
| **B-5：浮出 / 停靠回来** | 抓手拖出窗口 → `FloatingPanelWindow`（无边框/置顶/Tool/可无焦点）；关闭按钮或拖回主窗口 → 收回**原位**；`cfg["floatingPanels"]` 落盘 + 重启还原。**浮出不进停靠树**（树记"家"，宿主只是不挂它） | ✅ |
| **B-6 前半：一键复位** | 设置 →"复位面板布局"按钮：`store.clear` 掉存档，**不动布局开关**；走既有设置约定（改 cfg → 落盘 → `settingChanged` → 窗口重装） | ✅ |

## 2. 文档位置

- **主方案（全过程、进度、实测数据）**：`docs/superpowers/plans/2026-09-01-window-layout-and-panelization.md` —— 含 A-0~A-6 实测数据、B-1~B-4 进度、B6 五个待拍板决定。
- **本交接**：`docs/superpowers/plans/2026-09-01-session-handoff.md`。

## 3. B-4「拖拽重排」——已完成（2026-09-01 续会话）

六步全部落地，做法与验证：

1. **面板标题条** → `ui/panels/drag.py` 的 `PanelTitleBar`（16px，抓手点阵 + 标题，只在 `panelDrag` 打开时出现）+ `PanelFrame`（把"面板 + 抓手"打包成一个布局项；`sizeHint` 是 **子控件 hint + 16**，不走 QLayout —— 高度策略在 polish 之前跑，layout hint 在那时说谎，见坑 7）。`PanelFrame` 用 eventFilter 跟随子控件显隐，**隐藏的滑块组不会剩一条孤零零的标题栏**。
2. **拖拽命中** → `PanelTitleBar.mouseMove` 超过 `startDragDistance` 起 `QDrag`，mime 类型 `application/x-colorink-panel`，载荷是面板 id。
3. **落点指示** → 纯函数 `rearrange.zone_at()`（四边各 25% 边带、中间是页签区、角落平局按 左/右/上/下 定序）+ `drop_rect()`（边=一半，中心=整块），宿主用 `DropIndicator` 画出来。
4. **重新挂载** → `PanelHost.apply_drop()` 调 `rearrange.move_panel()` 得到新树 → `set_tree()` → 发 `rearranged(tree)`。
5. **持久化** → `MainWindow._panel_rearranged` 存树 + `save_hotkey_config` + 走**同一条** `refresh_slider_visibility_and_order`（可见性/高度策略只有一条路径）。
6. **三件套** → 树节点（`rearrange` 的手术保持 `Split.resizable` 语义）+ 设置开关（`panelDrag`，默认关，关着逐像素不变）+ 测试（`tests/test_panel_drag.py` **49 项** + `tools/preview_panel_drag.py` 真实窗口 **24 项**）。

**这一轮顺带修掉的既有缺陷**（拖拽把它们变得可达，都有测试守着）：`column_hint()` 把并排两列的高度**加起来**（经典单列 A/B 328==328 逐像素不变，只有算错的两档变小）；`set_drag_enabled()` 在还没挂过树时会挂"默认树"，而默认树里有 picker——**开着 panelDrag 启动会把取色区从主窗口拽走**；`_read()` 读回页签时丢掉 `pages`。

**这一轮真正的设计决定：存档归属（seed）。** 树能被拖动之后，"排布"就有了两个来源——开关推导 vs 用户存档。给存档记一个 seed（`stack`/`split`/`tabs`）：**存档只能覆盖它自己出身的那次推导**。于是拖拽结果活过刷新和重启，而勾选"并排/页签"仍然立刻生效。没有 seed 的老存档不认领（B-4 之前写的存档会回落推导，用户看不出差别）。

顺带补掉两个窟窿：`refresh_slider_visibility_and_order` 以前存的是"推导的列"而不是真正挂上去的树（页签模式会被存成单列）；`mount_changed` 声明了、接了、**从来没 emit 过**（现在挂载的面板集合变化时才发）。

**一键复位（B-6 前半，同轮完成）**：拖拽没有撤销，所以配套做了"复位面板布局"（设置 → 取色区卡片）。只清存档、不动开关；实现是 `store.clear`，窗口侧 `reset_panel_layout()` 与设置侧按钮都调它。

## 3b. B-5「浮出 / 停靠回来」——已完成（同一续会话）

- **触发**：抓手本来就是 `QDrag`，`drag.exec()` 返回 `IgnoreAction` = 松手时没人接 = 窗口外 → `float_requested` → 宿主转发 → `MainWindow.float_panel()`。
- **浮出状态不进停靠树**：树记的是"家"，`PanelHost.set_floating_panels(ids)` 只是不挂这些 id（连 provider 都不问）。收回时重挂 → **自动回到原槽位**。存档另起 `cfg["floatingPanels"] = {id:[x,y,w,h]}`。
- **`FloatingPanelWindow`**（`ui/panels/floating.py`）：无边框 + 置顶 + Tool + 可选 `WS_EX_NOACTIVATE`。无焦点的 Win32 代码抽成共用 `apply_no_activate()`，主窗口改为委托（两处从此同一份实现）；`noFocusMode` 改动会经 `refresh_floating_focus()` 同步给已浮出的窗口。
- **同一条 `PanelTitleBar` 两种模式**：停靠时拖动起 `QDrag`；浮出时拖动移动窗口本身，松手落在宿主矩形内就收回。
- **浮窗能调大小**：无边框自己 hit-test 4px 边（纯函数 `resize_edge_at()` 判八向），`begin_resize/resize_to/end_resize`，限最小尺寸，松手才落盘，悬停变光标。
- **层级开关**：浮窗标题栏图钉按钮，实心=置顶（默认）/空心=可被盖住；状态与几何一起存（记录升级成 `{"rect":[...],"onTop":bool}`，旧裸数组兼容）。
- **双击 = 出去/回来**：双击抓手浮出、双击浮窗标题收回（"拖到窗口外"这手势没人猜得到）。
- **拖回来看得见落点**：拖着浮窗经过滑块列显示落点高亮，松手落在**你放的地方**（四边+中心，与窗口内拖拽同一套规则）。
- **落点只有上下左右**：`zone_at()` 默认把整块面板按四条边分成四个三角区（离哪条边近就落哪边），**没有"掉进它里面变页签"这回事**；页签落点作为 `allow_center=True` 保留（树和手术都支持、有测试），但默认不开。
- **标题条与边框严丝合缝**：照抄主窗自己的规矩 —— 上边距 `= 边宽 if title_bar_inset else 0`（`csp` 主题是 inset，标题条下移一圈；其余三套标题条**就是窗口最上沿**），左右下 `= 边宽`，面板本体零内缩。第一版给了固定 4px 上边距和 body 内缩，于是比主窗多出一圈边框带 —— 那就是用户看到的"缝隙"。拉伸靠边框那一圈；标题条最外 4px 的按压 `event.ignore()` 交还窗口，所以贴边也能拉。
- **撕下来保持原大小**：浮窗尺寸 = **面板在列里的真实尺寸** + chrome，不是 sizeHint。尺寸必须在**离开列之前**量（一旦宿主放手、浮窗收养，它已经被新窗口的默认尺寸改过了）—— 实测 344×75 的滑块组撕出来还是 344×75。
- **间距不再牵一发动全身**：滑块列的 `QVBoxLayout` 以前会把**多余高度平摊到块与块之间**——任何一块高度一变，整列的缝隙全跟着动（实测块间距 99/98 这种不齐的值）。现在栈末尾加一条 `addStretch(1)`，余量全部堆到底部，块间距恒等于 `sliderDiffSpace`（实测开关抓手前后都是整齐的 80 / 96）。开关抓手往返后 `column_hint` 与最小高度**逐像素回到原值**（232 → 280 → 232，648 → 696 → 648）；窗口本身的高度按既有"增长不收缩"策略保留，最小高度已回落，拖一下即可。
- **主窗标题栏加了抓手开关**（☰ 旁边的 ▤，可勾选状态）：这是整个面板系统的入口 —— 不用进设置就能开抓手、于是也就能拖着改顺序（▲▼ 删掉之后留下的缺口由它补上）。按钮与设置页的勾选框双向同步（`TitleBar.sync_panel_grips`，每次重排都对一次）。
- **滑块间距不再被窗口大小改动**：滑块组自己的 `QVBoxLayout` 也会把多余高度平摊到滑条之间 —— 拖大浮窗就等于偷偷改了设置里的"同组滑块间距"。每个组的布局末尾补一条 `addStretch(1)`（幂等，主题 pass 每次检查），余量堆到底部，间距恒等于设置值。
- **抓手与内容之间留白**：`PanelChrome.grip_gap`（`4 × uiScale`）—— 停靠时是 `PanelFrame` 的布局间距，浮出时叠加到 body 上边距。抓手不再压在滑条头上。
- **撕下来的尺寸自校正**：chrome 的加加减减差几个像素很难一次算对，所以 `float_panel` 摆好之后**量一次实际面板尺寸并补差**（`resize(+Δw, +Δh)`）—— 承诺的是"面板尺寸不变"，那就以量到的为准。
- **浮窗里的面板不贴边**：body 用主题给的 `content_margins`（`(4,6,4,6) × uiScale`，和主窗滑块列同一套数），撕下来的窗口尺寸也把这圈内边距算进去 —— 否则面板会比原来窄一圈（实测 344→336 就是这么来的）。
- **浮窗最小尺寸下调到 120×24**：它的职责是"别被拉没了"，不是决定面板多高 —— 28px 高的块撕出来必须还是 28px。
- **设置整合（一级分组）**：侧栏新开一条一级导航「面板」（图标：一块宽的 + 两块并排），下辖三张卡 —— **排布**（抓手 / 并排 / 页签）、**独立窗口**（浮出清单 + 全部收回 + 复位面板布局）、**间距**（同组滑块间距 / 面板之间间距，从「取色器 → 高级」搬过来）。原来那张「面板布局」小卡撤掉。
- **旧的设置整合**：曾经的「面板布局」卡（抓手 / 并排 / 页签 + 浮出清单 + 全部收回 + 复位），从「色环与 LAB」里搬出来 —— 排布跟色轮长什么样是两回事。**「全部收回」是"拖到屏幕外找不着了"的唯一兜底**（右键救不了：你得先找到那个窗口）。
- **滑块顺序的 ▲▼ 按钮删了**：顺序改由拖面板决定，设置里的行只保留显示开关，行序跟随实际顺序。⚠️ 副作用：**不开抓手就没有改顺序的入口**了。
- **右键菜单**：抓手/浮窗标题栏右键 → 浮出/收回、隐藏这一组、复位面板布局（`ui/panels/menu.py` 纯数据 + `run_panel_action` 执行，菜单只是把同一批动作放到手边）。
- **面板的皮跟着主窗走**：主题 pass 把整套值打包成 `PanelChrome`（背景/边框色/边宽/圆角/文字色/**标题条底色**/标题文字色/分隔线/UI 缩放/字号），一次推给 `PanelHost`（所有抓手）和每个浮窗，并缓存在 `_floating_chrome`。**验证比像素**：主窗标题条与浮窗标题条中心点颜色必须相等（black/white/gray 三套主题实测 `#2d2d2d / #b2b2b2 / #787878` 全等）。第一版漏了标题条底色，因为端到端只断言"样式表里有 background-color"——又是"断言账面"（坑 13）。
- **四个顺序坑（都在代码里钉了注释、测试里钉死）**：
  1. 浮出必须**先卸载、再交给浮窗**，否则宿主重挂时 `_detach_mounted` 把控件从浮窗里抢回去；
  2. 浮窗必须**先装面板、再 setGeometry**，给空布局定尺寸不生效（填内容时会被布局改回去）；
  3. 保存槽必须**等窗口彻底摆好再接**，否则还原时"取消置顶"发出的 `geometry_changed` 会把半成品几何写回存档，重启后窗口缩成最小；
  4. **一块面板同一时刻只能有一个 holder**（`PanelHolder.set_panel` 会让上一个 holder 先撒手）。旧 holder 还订阅着显隐的话，它会跟着面板一起"显示"——而它已经没有父控件了，于是屏幕上凭空多出一个空窗口。同理，宿主换树时把**卸下来的框和旧根都 `hide()`**：从 detach 到 deleteLater 之间它们都是顶层控件。

### 下一步可选方向（按依赖排序）

1. **B6 第 2 条**：高度策略 `fit` / `free` 两档（今天靠"手动调过高度就不再自动跟随"顶着；横向分割之后这条会更明显）。
2. **picker 进树 + aspect=1.0 由宿主执行**（B6 第 1 条）：目前取色区不在滑块列的树里，拖不到它；一旦进树，宿主需要"按比例吃高度"的布局项，**并且 `panel_widget('picker')` 会真的把 `self.stack` 交出去**——本轮已经踩过一次（见坑 12）。
3. **布局预设**（B-6 后半：类 PS / 紧凑 / 只要色环）：等 picker 进树之后才有意义。
4. ~~顺序入口的兜底~~ —— 已解决：主窗标题栏 ☰ 旁边的 ▤ 按钮一键开抓手。
5. **浮出清单做成逐行**：现在是一行文字 + 「全部收回」。面板多了之后，逐行「收回」会更好用。

## 4. 关键文件（本会话改动的）

```
ui/window_layout.py          # 纯数据几何（色环唯一公式）
ui/color_session.py          # 共享取色会话
ui/preview_clearance.py      # 色块簇避让（圆环/圆盘组合）
ui/panels/spec.py / registry.py / tree.py / store.py / host.py
ui/panels/rearrange.py       # 拖拽：落点几何 + 树手术（纯数据，不 import Qt）
ui/panels/drag.py            # 抓手 PanelTitleBar / PanelFrame / DropIndicator
ui/panels/floating.py        # 浮窗 FloatingPanelWindow + 共用 apply_no_activate
ui/window/floating_mixin.py  # 浮出/收回/重启还原的编排
ui/window/panels_mixin.py    # panel_widget / provider / 树构建 / arrangement_seed
ui/window/layout.py          # 布局 pass、高度策略（增长不收缩）、settle
ui/window/theme.py           # apply_layout / apply_style 拆分
ui/color_wheel_geometry.py   # get_wheel_geometry 委托 window_layout
ui/color_wheel_rendering.py  # 切片自适应采样
ui/color_wheel_interaction.py# is_active_interaction 走会话
ui/lab_visualizer.py         # 平面几何 / 明度条 track_band / 渲染精度自适应
ui/lab_prewarm.py            # 渲染器（LUT / 网格缓存 / 边界缓存）
ui/slice_prewarm.py          # 切片渲染器同样优化
ui/color_conversions.py      # srgb_encode_u8 + lab_to_linear_array 等
ui/widgets/gradient_slider.py# 三角把手 / 样式缓存 / repolish
ui/color_history.py          # 余数摊缝隙 / 配置早退
ui/main_window.py            # panel_host + mount_changed + session
core/config.py               # slidersSplit / slidersTabs 键注册
ui/settings/appearance_panel.py, settings_sidebar.py  # 两个新勾选框
tools/preview_panel_drag.py  # 真实 MainWindow 上的 15 项端到端拖拽检查
tests/test_window_layout.py / test_render_quality.py / test_panel_model.py / test_panel_host.py / test_panel_arrangement.py / test_panel_provider.py / test_panel_drag.py / test_panel_floating.py / test_panel_reset.py / test_resize_smoothness.py / test_theme_split.py / test_history_grid_layout.py / test_slider_handle_bounds.py / test_lab_lightness_bar.py / test_preview_clearance.py / test_window_size_binding.py
```

## 5. 坑（务必记住）

1. **别在项目/临时目录里用标准库名命名脚本**（如 `inspect.py`）。dataclass 处理会 `import inspect`，导到你的脚本后递归 import `ui.main_window` → 栈溢出 → 段错误 0xC0000005，看起来像产品崩溃。本会话为这个差点误判。
2. **离屏顺序**：稳妥做法是先 import 完所有 `ui.*` 再 `QApplication(...)`（本会话有偶发的非确定性段错误，根因即第 1 条，但顺序仍然建议 keep）。
3. **`isVisible()` 在窗口未真正显示时为 False**；`resizeEvent` 对未显示/未挂载的裸控件不送达——所以 `_render_ab_plane` 自己检测尺寸变化（`_last_full_px`），不依赖事件。
4. **QStackedWidget 非当前页不会布局**：`lab_square` 在色轮页在前时报告构造时几何（100×100）。一切「读别的控件实时几何」的做法都已被换掉（`window_layout` / `_picker_circles` 用布局对象）。
5. **numpy 视图悬空**：`QImage.constBits()` 返回的 buffer 在 QImage 被回收后失效，`np.frombuffer` 不拷贝。比对截图时必须 `.copy()`（本会话一度据此得出错误结论）。
6. **全窗截图比对不可靠**：同代码两次运行的屏幕输出最大差 254（渲染时序），跨进程 diff 无效。可信方法：**逐部件 A/B**（同一进程内两套路径背靠背、每部件独立 grab、数据 .copy()）。
7. **高度策略与布局**：`sliders_container.sizeHint()` 在布局未激活时读 ~16px；`_adjust_content_height` 读 hint 前必须激活嵌套布局；`PanelHost.column_hint()` 提供确定性高度。
8. **配置键清洗**：新增配置键必须同时登记 `core/config.py` 的**默认表** 和 `_BOOL_KEYS`/`_INT_KEYS`（`load_hotkey_config` 只保留已登记键）。
9. **别让 Python 回收活着的 MainWindow**（本轮踩到，和坑 1 同一类误判）：脚本里 `main()` 一返回，局部变量里的窗口就被析构，而热键/同步线程还在跑 → 退出码 `0xC0000005`/`0xC0000409`，**所有检查都打印了 PASS 之后**才炸，看起来像产品崩溃。产品自己从不析构窗口（`close_application()` 直接 `os._exit(0)`）。诊断脚本请把 app/窗口放进模块级 keepalive 列表。
10. **合成的拖放事件只是"借用" QMimeData**：`QDropEvent` 不持有它。事件还在时让 Python 回收 mime 会破坏堆，几秒后炸在无关的地方。测试/脚本里合成拖放事件时把 mime 和事件一起 keepalive（`tests/test_panel_drag.py` 有现成写法）。
11. **离屏读几何要逐层 `layout().activate()`**：`setGeometry` 对未显示控件是 **post** 一个 resize 事件，不跑事件循环永远读不到真实尺寸（子控件会一直报 640×480）。`tests/test_panel_drag.py::_lay_out` 是可复用的写法。
12. **`panel_widget('picker')` 交出去的是主窗口的 `self.stack`**：任何让宿主挂载 picker 的路径（哪怕只是"顺手挂一下默认树"）都会把取色区从主布局里拽走，窗口开起来没有色轮。宿主只该挂它被明确交代的树；`tools/preview_panel_drag.py` 现在在四个时点断言取色区还在主布局里、尺寸 352×352。
13. **断言"看得见的东西"，不是账面**（用户报的那个 bug 就是这么漏过去的）：端到端当时断言的是 `mounted_panels()` 里有它、`floating_windows()` 里没它 —— 全过；而面板其实 `parent() is None`、`isHidden() is True`，**用户再也调不出来**。凡是"回来了/挂上了"的检查，必须断言 `parent()` 与 `isHidden()`。

## 5b. 「复位面板布局」到底复位什么

只丢 `cfg["panelLayout"]`（**拖出来的那棵排布树**），然后按开关重新推导一次。

| 会被丢掉 | 不会动 |
|---|---|
| 拖拽换过的顺序 / 拖出来的左右分割 / 拖成的页签 | 抓手·并排·页签三个开关（那是设置，不是拖出来的） |
| 分割条被拖成的比例 | 每组滑块的显示/隐藏（`showSliders*`） |
| —— | 浮出去的面板（浮出是 `floatingPanels`，另一份记录） |

一句话：**回到"如果你从没拖过，这几个开关会给你的那个布局"**。入口有两个，同一个实现（`store.clear`）：设置 →「取色器 → 色环与 LAB」的按钮，或抓手右键菜单。

## 5g. 完成：LAB 视图可以浮出（B6 第 1 条的一半）

**做法**：把 LAB 视图注册成 `registry.LAB_VIEW`（取色区的卫星，和明度条、前景背景同级 —— 树摆 picker 就算完整），但**不进停靠树**：它住在取色区 `QStackedWidget` 的第二页，浮出/收回由主窗自己接管（`detach_floating_panel` 从 stack 里 `removeWidget`，`attach_floating_panel` 用 `insertWidget(1)` 放回，收放都会把 stack 切回色轮页）。

**入口**：设置 →「面板 → 独立窗口」里的「浮出 LAB 视图」按钮（浮出后变「收回 LAB 视图」）；关闭浮窗的 × 也收回。浮出时色轮/LAB 切换按钮自动禁用（只有一个页面时切无可切），收回恢复。

**两个坑（都是交接文档老坑的实例）**：

1. **QStackedWidget 非当前页报构造尺寸**（坑 4）：LAB 页没浮出时只报 100×30，用它算浮窗大小会得到一个 136×70 的小窗口。解法是 `floating_reference_size()` 钩子 —— 面板自己几何说谎时，主窗用**布局数据**（`window_layout().picker`，352×352）报真值。
2. **自校正逻辑会把 LAB 浮窗撑爆**：它量到面板 100×30（还是那个谎），于是把窗口拉到 620×714。给 `float_panel` 加了 `floating_self_correct()` 钩子，LAB 关掉这个校正 —— 它的参考尺寸本来就精确。

**实测**（隔离配置、真实窗口）：浮出 → 浮窗 368×392、LAB 方格 352×352；收回 → stack 页数 2、pane_lab 父为 QStackedWidget、不隐藏、切换按钮恢复；再切到 LAB 页正常。全量 1270 passed，端到端 80/80。

**透明度修了**：`FloatingPanelWindow` 补上 `WA_TranslucentBackground + WA_NoSystemBackground` —— 之前 chrome 底色带着 opacity 却画在不透明窗口上，半透明对着纯黑合成，于是"透明度调到最低变成黑色"。修后浮窗和主窗一样真正透。

**LAB 抓手（本轮追加）**：`panelDrag` 开着时，LAB 页和滑块块一样被 `PanelFrame` 包起来、顶上有一条抓手，拖动/双击即浮出（`_apply_lab_wrap()` 负责在开关切换时包/拆，经典布局仍是裸页、像素不变）。浮窗尺寸走 `floating_reference_size()`（布局数据的 352×352，不是 stack 的当前几何）。

**本轮已补完 LAB 浮出**：LAB 页现在和其他面板一样，在 panelDrag 开启时包进 PanelFrame，有抓手、可拖出、可双击浮出；浮出后固定到布局数据给出的 352×352，触发 prewarm，lab_square 缓存正常。

**透明度黑块（已修，根因明确）**：浮窗标题栏是**自绘**（`PanelTitleBar.paintEvent`），之前把 `with_opacity()` 产出的 `rgba(...)` 字符串直接喂给 `QColor()` —— `QColor(str)` 不解析 CSS 函数式颜色，解析失败返回无效色（黑）。主窗标题栏用 CSS 样式表（能解析），所以只有浮窗黑。修法：`PanelChrome` 新增 `opacity`（0..1）字段，`bar_bg`/`divider_color` 改为**纯色**传入，自绘时 `QColor(纯色).setAlphaF(opacity)`。实测 100/50/20 三档标题栏不再出现 #000000，随透明度单调变化。

**LAB 抓手/浮出已整体撤回（用户拍板）**：LAB 视图回归普通页 —— 不加抓手、不可浮出、不注册面板，退回纯取色区第二页。`LAB_VIEW` 面板、`_apply_lab_wrap`、`detach/attach/reference/self_correct/dock_target` 等 LAB 专属逻辑全部删除，`tests/test_lab_float.py` 删除。透明度黑块修复保留（那是浮窗标题栏的通用 bug，与 LAB 无关）。**历史留档（将来重做参考）**：抓手挤占纵向空间会让 LAB 方格 352 塞不进 332；正解是抓手叠在 LAB 上方而非挤占，或让 `_prerender_lab` 的尺寸来源改成 pane 实际可用尺寸。

**收尾修掉的三个 bug + 一处清理**：

1. **收回后 LAB 内容缩成 100×13**（stack 非当前页不布局）：`attach_floating_panel` 现在 `QTimer.singleShot(0)` 延迟到重挂完成，再 `setGeometry(stack.rect())` + `_activate_lab_pane` + `_sync_lab_lightness_bar` + `schedule_full_prewarm(0)`。实测收回后 pane/lab_square 均为 352×332，切到 LAB 页不再跳。
2. **浮窗标题栏调透明度变黑**：`PanelTitleBar` 补 `WA_TranslucentBackground + WA_NoSystemBackground` —— 半透明背景之前画在不透明控件上，对着黑底合成。
3. **LAB 拖不回去**：`FloatingPanelsMixin._floating_dropped` 加了 `dock_target_at(panel_id, global_pos)` 钩子 —— LAB 在滑块列没有落点，拖到**取色区方形上**就收回（主窗用 `stack.mapToGlobal` 判定）。
4. **设置里的「浮出 LAB 视图」按钮删了** —— 抓手和浮窗标题栏已足够，不再需要额外入口。

**没做完的一条**：~~浮出的 LAB 内部还停在构造尺寸~~（已修：浮窗 368×392、pane/lab_square 352×352、prewarm cache 已生成）—— 浮窗外壳大小对了（368×392），但 LAB 的内容几何原本由主窗 `apply_layout` 驱动，离开 stack 后没有东西驱动它。已经试过：`update_geometries()`、逐层 `layout().activate()`，都不够，因为 `apply_layout` 喂给 `lab_square` 的矩形来自主窗 stack 的几何，而浮窗里没有这个来源。

**下一步的唯一正解（写清楚留给下个会话）**：浮出后按 `floating_reference_size()` 的 352×352 **直接 `pane.setFixedSize(352, 352)` 并重跑一次 `apply_layout`**，让 `lab_square`/明度条拿到 352 这个真值 —— 具体是给 `FloatingPanelsMixin.float_panel()` 加一个"面板需要主窗几何"的钩子，主窗在 hook 里做 `pane.setFixedSize(352,352)` + `apply_layout()`，然后在 `attach` 时 `setFixedSize(QWIDGETSIZE_MAX)` 恢复由 stack 驱动。关键：给 lab_square 的几何必须来自布局对象，不能读 stack（交接坑 4）。

**没做的半条**：色轮（wheel 页）本身还**不能**浮出 —— 它仍是 stack 的第 0 页，浮出它得把两个页面都考虑进去（浮出 wheel 后 stack 剩 LAB 页，切回逻辑和 LAB 是对称的，可以照抄）。这条留给下一个会话，和"picker 进停靠树"一起做。

## 5f. 已完成 B6 第 2 条：窗口高度的 fit / free 策略

**发现**：`_resolve_content_height()` 这个策略函数**早就写好了、还有 5 个单测**，但真实路径根本没调用它 —— `_adjust_content_height` 里是另一套更粗的"只涨不缩"内联逻辑。测试测的是没人用的函数，于是它一直绿着，而窗口从来不缩。

**接上去之后**（`_adjust_content_height` 改用 resolver）：

| 事件 | 窗高 | 最小高 | manual |
|---|---|---|---|
| 初始 | 708 | 708 | False |
| 全部浮出 | **392** | 392 | False |
| 全部收回 | **708** | 708 | False |
| 用户手动拉高 150 | 858 | 708 | True |
| 内容没变时 | **858** | 708 | True |
| 浮出一块（内容变了） | **608** | 608 | False |

**关键的一处修正**：`mouseReleaseEvent` 原本把 `_last_auto_height` 记成"用户拖完的那个高度"，而 resolver 拿它跟**内容需求**比 —— 两个不同量纲的数永远不会相等，用户的高度实际上一次也保不住。改成记 `_last_required_height`（拖动那一刻内容需要多高），语义才对上："内容需求没变 → 尊重你的高度；内容变了 → 回到跟随内容"。

**闸门**：端到端新增「面板全出去后窗口自己缩回来了」「收回来窗口也自己涨回去了」。

## 5d. 已解决：面板全部浮出后，主窗的空间被"空容器"吃掉

**实测**（全部浮出、窗宽 360）：

| 窗高 | 取色区 stack | 滑块容器 | host | 色轮直径 |
|---|---|---|---|---|
| 708 | 352 | 316 | **300 → 修后 0** | 332 |
| 1000 | 352 | **608** | **592 → 修后 0** | 332 |

- **已修**：`PanelHost` 与它建的 stack 容器改成 `QSizePolicy(Preferred, Maximum)` —— 一个会膨胀的宿主会把窗口的余量全吞掉（空的时候涨到 592px 的虚空）。修后 host 恒为内容高度（空时 0）。
- **已修（第二轮）**：`sliders_container` 也改成 `Maximum`，并且**面板全空时整个容器 `setVisible(False)`**（在 `_panel_mount_apply` 里跟着 `mount_changed` 走）。效果：全部浮出后**最小高度 648 → 432**，窗口终于能缩到贴着取色区。经典布局的数字未变（取色区 `(4,178,352,352)`、色轮 332、最小高 648），端到端 74/74 仍全过。
- **已修（第三轮，尾巴清掉）**：按上一条写的量法做了 —— 缩到最小高后 `grab()` 逐行往上扫，发现底部只有 4px（就是边框），而 `stack` 底到窗口底还差 16px。根因在 `_adjust_content_height`：

  ```python
  hint = host.column_hint() if host.column_hint() > 0 else sliders_container.sizeHint().height()
  ```

  空列时 `column_hint()` 返回 **0（这是正确答案）**，却被当成"没算出来"而回落到容器的 sizeHint（=它自己的边距 16px），于是最小高度替一个不存在的列留了 16+4px。现在 `host` 存在就信它的 0，并且 `hint == 0` 时不再叠加容器边距。

  **效果**：全部浮出后的最小高度 `648 → 432 → **392**`；实测取色区底=385、窗高=392，**余 7px**（边框 4 + 布局 3），窗口真正贴着取色区。端到端加了闸门「最小高度贴着取色区」（`0 < 余量 ≤ 12`）。
- **还要想清楚的**：色轮是**方的**，直径 = `min(宽-8, 高-6)` 级别的公式，360 宽的窗口最多给到 332 —— 所以**纵向空间再多也不会让色轮变大**。面板全部浮出后"下面一大片空"的真正解法是让窗口**能自己缩回去**（B6 第 2 条 `fit` / `free` 两档策略），而不是把空间塞给谁。

## 5e. 浮窗"左上角闪一下" + 标题栏离开顶边（都已修）

- **闪一下**：`PanelHolder.set_panel()` 会 `setVisible(True)`（它要跟随面板显隐），而当时窗口**还没被摆过位置** —— 于是它在屏幕左上角显示一帧再被 `setGeometry` 挪走。修法：**先摆位、再收养、再摆一次**（收养会改尺寸，所以要摆两次）。
- **标题栏不在最上方**：把宿主和滑块容器改成内容定高之后，主布局里**没人再要那份余量**了 —— QVBoxLayout 于是把它**摊到各项之间**，标题栏被推离顶边（实测窗高 800 时 y=25，950 时 y=62）。修法两件一起：`main_layout.addWidget(self.stack, 1)`（余量优先给取色区，直到它被宽度卡住）+ 末尾 `addStretch(0)`（卡住之后剩下的停在底部）。实测三种窗高下标题栏 y 恒为 0、取色区 y 恒为 32、滑块列 y 恒为 388（不再随窗高漂移）。
- 端到端加了闸门「标题栏永远贴着窗口顶边」（连续拉高三次都要 y==0）。

## 5c. 已解决：sai 边框主题下"标题栏左侧有缝隙" —— 分数缩放的半像素

**真相**（在**真实屏幕**上抓到的，离屏永远复现不了）：

```
y=12  左: 303030 303030 303030 303030 949494 303030 …
      右: 303030 303030 303030 303030 303030 303030 …   ← 左右不同
```

物理 x=4 处**只有左边**有一条 `949494` 的浅灰竖线，贯穿整条标题栏。

**根因**：sai 的 `window_border_width` 是 **3**（四套主题里唯一的奇数），用户显示器 **dpr=1.5** → 3 逻辑像素 = **4.5 物理像素**。Qt 画不了半个像素，于是一侧画 4 个、把剩下的半个画成一条半覆盖的浅色线；另一侧取整方向相反，没有这条线。1x / 2x 屏上同一套主题干净得很 —— 所以这个 bug **只在部分机器上出现**。

**修法**：`ui/window_layout.snap_border_width(width, dpr)` —— 边宽已经落在整数物理像素上就原样保留，否则挪到最近的能整除的宽度（1.5x 下 3 → 4，也就是 6 个物理像素），挪不动就放弃保持原值。`apply_layout` 与 `apply_style` 两条路径都用它。**1x / 2x 下是空操作**，所以经典外观零变化（全量 1267 passed 佐证）。

**验证**：`python tools/diag_titlebar_gap.py` 重跑，`widget.grab()` 与真实屏幕截图**每一行左右完全一致**，一个"左右不同"都没有。纯函数单测 `tests/test_border_snap.py` 13 项（含 1.25 / 1.75 等其它分数比例）。

> **方法论教训（值得记住）**：我前面用"从边缘往里扫，找第一个与 x=0 不同的颜色"来量边框宽度，**这个量法对这个 bug 是瞎的** —— 它把那条浅色线当成了"边框到此为止"，于是左右都报 4，看起来完美对称。真正让我看见它的是**把每一行的左右各 10 个像素并排打出来逐个比对**。量不出问题时，先怀疑量法。

## 5c-旧. 排查过程留档（假设与证据）

已排除**窗口边框**：在 y=500 逐像素扫左右两侧，`default`（4px）与 `sai`（3px）左右**完全对称**（`#2d2d2d ×N` 然后 `#1e1e1e`），`main margins` 也是对称的 `(3,0,3,3)`。所以缝隙不在窗口框，而在**滑块行内部**（通道字母那一列 / 行内 spacing / 数值框宽度）——sai 的 slider theme 有自己的 `row_spacing` 与字母列宽。

**已量到的（第二轮）**：

| | 容器 x | 容器宽 | 左内缩 | 右内缩 | 字母列宽 | 行 spacing | 滑条 x..右 | 数值框 |
|---|---|---|---|---|---|---|---|---|
| default | 8 | 344 | 8 | 8 | 12 | 1 | 13..312 | x=317 w=27 |
| sai | 7 | 346 | 7 | 7 | 13 | 3 | 16..310 | x=317 w=29 |

**结论：行本身左右是对称的**（内缩 8/8 与 7/7，差的 1px 就是边框 4 vs 3）。两套主题真正的差别是**字母列宽（12→13）和行内 spacing（1→3）**——sai 的字母与滑条之间多 2px。所以"左侧缝隙"最可能是**字母那一列**看起来偏窄/偏宽造成的错觉，或者在 `GradientSlider` 内部的槽绘制里。

**第三轮：位置搞清楚了，是"标题栏左侧"，不是滑块行。** 重新按标题栏那一行扫（黑/白两套 UI 主题、default/sai 两套边框主题、主窗+浮窗共 8 组）：

| | 标题栏 geo | 标题栏底下一行，左起 8px |
|---|---|---|
| default | (4, 0, 352, 28) | `b2b2b2 ×4` 然后 `ffffff` |
| sai | (3, 0, 354, 36) | `b2b2b2 ×3` 然后 `ffffff` |

**离屏（dpr=1.0）下左右完全对称，量不出缝隙。**

**已排除的假设（都实测过，全部对称）**：

| 变量 | 试过的取值 | 结果 |
|---|---|---|
| UI 主题 | black / white / gray / **eyedropper（用户真实配置：条 #303030、底 #f8f8f8）** | 左右逐像素相同 |
| 边框主题 | default(4px) / sai(3px) | 左右相同，宽度=主题值 |
| 设备像素比 | 1.0 / **1.5（`QT_SCALE_FACTOR`）** | default 6/6 物理，sai 4/4 物理，都对称 |
| 背景不透明度 | 100 / 60 | 标题栏整行单色，左右一致 |
| 窗口 | 主窗 / 浮窗 | 同上 |

**离屏这条路已经穷尽。** 剩下的差异只可能在**真实平台的合成**里（原生 DPI 取整、`WA_TranslucentBackground` 与桌面的混合），`widget.grab()` 复现不出来。

**下一步：`python tools/diag_titlebar_gap.py`** —— 在**真实屏幕**上跑（用用户配置的副本，绝不改原配置），同时用 `widget.grab()` 和 `QScreen.grabWindow()` 抓图，打印标题栏上下左右各 10 个像素并存一张放大图。两种抓法的差异就是答案：如果 grab 对称而屏幕截图不对称，那就是合成层的问题（多半是 DPI 取整），与主题参数无关。

~~最可能的原因（待验证）：分数缩放~~。四套边框主题里**只有 sai 的 `window_border_width` 是奇数 3**（其余是 4）。用户显示器 **dpr=1.5**：3 逻辑像素 = **4.5 物理像素**，无法均分，Qt 只能一边取 4、一边取 5 —— 于是只有 sai、只在真机、只在一侧出现 1px 的差。default 的 4px × 1.5 = 6px 整除，所以不出现。

**下一步（需要真机验证）**：把 `ui/border_themes.py` 里 sai 的 `window_border_width` 临时改成 4 或 2，看缝隙是否消失。若消失，正解是让边宽按 dpr 对齐（`round(bw * dpr) / dpr`）而不是硬改主题值 —— 那是用户按截图调出来的数。

**旧的第二轮记录**：我想逐像素量 `GradientSlider` 内部槽的左右内缩，但滑条是渐变的，"取 x=0 的颜色当背景色"这个扫描法在 sai 下失效（整行同色，扫不到边界）。**下次换个量法**：把滑条 grab 出来后，比较**第一列与最后一列**的像素是否都属于渐变（而不是主题背景色），或者直接读 `ui/slider_themes.py` 里 sai 的 groove/handle 内缩参数与 default 对比。

## 5h. 已完成：浮窗跟随"仅在软件前台显示"（真实 HWND 级修复，用户场景已验证）

`check_foreground_window()` 原来只 show/hide 主窗口，现在末尾补 `set_floating_foreground_visible(...)`（`FloatingPanelsMixin` 方法）：切走画图软件时浮窗跟着主窗一起藏，切回来一起现。

**第一版有真 bug（用户报"没跟着一起隐藏"）**：`isHidden()` 的祖先语义会让带父 Tool 窗口"报告 hidden 但真实 HWND 仍亮着"；当时的 hide 分支还跳过隐藏。**最终修复分三层：**

1. **`PanelHolder` 事件过滤不再盲目镜像祖先显隐**（`ui/panels/drag.py`）：`HideToParent` 到来时面板自己的 `isHidden()` 还是 False，原来会反过来把已藏窗口重新 show 出来——这正是"主窗藏了浮窗又弹回来"的机制。现在 `HideToParent` 直接忽略（holder 随祖先一起藏），`ShowToParent` 才按面板显隐镜像；浮窗加 `_foreground_hidden` 标记，前台整体藏起来后，面板因模块切换自己 show 也不允许把窗口弹回。
2. **`FloatingPanelWindow.force_native_visible()`**（`ui/panels/floating.py`）：`QWidget.hide()` 对"已因祖先而算 hidden"的窗口可能是 no-op，所以用 Win32 `ShowWindow(hwnd, SW_HIDE / SW_SHOWNOACTIVATE)` 直接驱动真实 HWND；Qt 状态与屏幕从此一致。
3. **主窗 `showEvent`/`hideEvent` 同步**（`FloatingPanelsMixin`）：热键/托盘/关闭到托盘的直接 `hide()/show()` 也把浮窗一起藏/现；`check_foreground_window` 改为传 `palette_visible`（用户手动隐藏时不再把浮窗硬拉出来）。

**验证**（真实屏幕，用户显示器 dpr=1.5）：

```powershell
python -u tools/diag_floating_hwnd.py --foreground
# 切前台到 Progman → check_foreground_window 隐藏：main/float IsWindowVisible 都 False
# 切回 Colorink → 两者都恢复 True；Qt isVisible/isHidden 与真实 HWND 一致；6/6 PASS
```

- 单测 `tests/test_floating_foreground.py` **9 项**（含 ShowWindow 调用、面板自身隐藏不被拉起、手动 show/hide 事件同步）。
- 端到端 `tools/preview_panel_drag.py` **83/83**，新增「主窗隐藏后浮窗跟着藏起来」「主窗重新显示后浮窗跟着现出来」两条闸门。

## 6. 验证基线（下次修改前先跑）

```powershell
python -m pytest -q                     # 期望 1273 passed（122 个 error 为沙箱权限，忽略）
python -u tools/diag_titlebar_gap.py       # sai/dpr=1.5 真实屏幕：左右像素一致
python tools/diag_titlebar_gap.py       # 真实屏幕抓图：标题栏左右像素必须逐行一致
python -u tools/diag_floating_hwnd.py [--foreground]  # 主/浮窗真实 HWND 同步藏/现，6/6
# 设置侧栏一级导航：快捷键 / 界面 / 取色器 / 面板 / 滤镜 / 同步 / 关于（面板 = stack.widget(3)）
python -u tools/preview_panel_drag.py   # 期望 83/83 通过、exit 0（含抓手开关、内边距、尺寸保持、最小高度贴合、浮窗跟随主窗藏/现）（重排/复位/浮出/调大小/置顶/双击/右键/无空窗口/皮肤比像素/落点四向/边框严丝合缝）
```

关键交互回归提醒：
- 明度条/色环拖动时全精度、松手不糊；
- 窗口很窄（350 宽）再调宽，色块簇不重叠、色环下方无大片空白；
- 滑块顺序、可见性、模块切换后窗口高度**不跳**；
- `slidersSplit` / `slidersTabs` / `panelDrag` 开关往返 + 重启读回；
- 打开 `panelDrag` 拖一个滑块组到别处 → 松手不闪、顺序变了、重启还在；再关掉 `panelDrag` → 标题条消失、面板一个不少；
- 设置里点"复位面板布局" → 回到开关决定的排布，且三个布局开关**保持不变**；
- 抓手拖到**窗口外**松手（或**双击抓手**）→ 该组变成独立浮窗（置顶、不抢焦点）；点浮窗的 ×、双击浮窗标题、或把它拖回滑块列 → 收回（拖回来时落在你放的位置）；重启后浮窗自己回来，**大小和置顶状态都在**；
- 浮窗拖边能调大小；标题栏图钉按钮切换置顶；浮窗边框/底色跟主窗一套皮；
- 抓手（或浮窗标题栏）**右键** → 浮出/收回、隐藏这一组、复位面板布局；
- 浮出时屏幕上**只多一个窗口**（曾经会甩出 1~2 个空窗口）。