# 会话交接：抓手开关后「前景/背景预览簇」与页签条重叠

> 交接日：2026-09-02。仓库：`D:\Program Files\colorink`。当前 HEAD：`8e756c9`（交接文档+探针的提交；v1.7.1 内容，未 push、未发布、`dist/` 未重打包——EXE 是旧版，所有近期修复只在源码里）。2026-09-03 新会话的修复**尚未提交**（见第 8 节）。

## 1. 用户症状（原话）

- 「我打开抓手后还是会重叠」「打开抓手后应该延长窗口长度而不是往上面挤」
- 截图：ColorPreviewBox（前景/背景色块 + 透明棋盘格，左下角模式）整块压在页签条上，盖住标签（如 `OKL… 历史颜色/HSV`）。
- 触发：开启「面板抓手」（`panelDrag`），偶发/常发；用户判断正确解法是**窗口随内容增高**，预览簇保持在轮盘左下的自然位，而不是被向上挤。

## 2. 已做的修复（均为源码，均未打包）

| 提交 | 内容 |
|---|---|
| `57380de` | 浮窗拖回页签头崩溃（tab 索引与 `node.pages` 错位；`_panel_pages` 映射 + `_dock_at` 支持 `MERGE_PAGE`） |
| `a18ae27` | `_place_floating_chrome` 里按 `sliders_container` 真实几何钳制预览簇底边（兜底） |
| `de9a87a` | 钳制下沉到 `_place_preview_box`，覆盖 pass 内所有出口（主题 pass + `update_geometries` 的二次摆放） |
| `94f23bb` | 事件驱动：`eventFilter` 监听 `sliders_container` Resize/Move/Show/LayoutRequest → 重新钳制 |
| `7f3cecd` | 改为**重新摆放**（`_place_floating_chrome`）而非只钳制：窗口长高后预览簇回到贴轮盘自然位（当前 HEAD） |

## 3. 离线（offscreen）已验证的事实（456×700、左下模式、`panelDrag` 关↔开）

```
抓=关：win_h=698 required=698 column_hint=160 preview_y=372 bottom=478 sliders_top=484
抓=开：win_h=720 required=720 column_hint=200 preview_y=372 bottom=478 sliders_top=484
```

- 窗口**确实会**随内容长高（698→720，`column_hint` 160→200，`required` 698→720）。
- 预览簇 y 恒为 372（贴轮盘自然位），底边 478 始终不越 `sliders_top=484`。
- 矩阵（360×700 / 456×777 / 456×621 × uiScale 100/125/150 × 抓手开关 × 5 次切换）：**90 组 0 重叠**。

**结论：当前源码在离线下无法复现用户症状；必须到用户真机上取数据。**

## 4. 关键代码路径

- 摆放：`ui/window/theme.py:_place_floating_chrome(scale)`（读 `sliders_container.sizeHint().height()` 为 `sliders_h`；安装 eventFilter；fit 后钳制）
- 几何：`ui/window/layout.py:update_geometries()` → `apply_layout()`（ThemeMixin）→ 之后**再次** `_place_preview_box(...)`（用旧 `sizeHint`）
- 摆放细节：`layout.py:_place_preview_box` → `_try_preview_fit` → `ColorPreviewBox.resize_and_position`（左下模式 `y = window_h - sliders_h - box - tile - 6*wheel_scale`）→ `_anchor_preview_to_circle`（贴轮盘圆）→ `preview_clearance.avoid_circles`
- 钳制/重摆：`layout.py:_clamp_preview_box`、`eventFilter`（容器 Resize/Move/Show/LayoutRequest → `_place_floating_chrome`）
- 高度策略：`layout.py:_adjust_content_height`、`_required_content_height`、`_resolve_content_height`；`host.py:column_hint/_node_hint/_tabs_hint`（frame 高度已计入：160→200）
- 触发链：`ui/window/picker_actions.py:refresh_slider_visibility_and_order` → `update_geometries` + `_adjust_content_height`；另 `main_window.py:_panel_mount_changed` → `singleShot(0)` `_panel_mount_apply` → `_adjust_content_height`
- 预览框：`ui/color_preview_box.py`（`position_mode` 仅 `top-left`/`bottom-left`，默认 `top-left`）

## 5. 待查项（新会话优先级排序）

1. **真机上窗口高度到底长不长？** 用户说「应该延展而不是往上挤」→ 怀疑在他们机器上启用抓手后**窗口没有长高**（仍为旧高度），预览簇被钳制上移。若真如此，重点查：设置页切「面板抓手」是否触发 `mount_changed → _panel_mount_apply` 的延迟高度调整；以及用户配置是否为 `lockWindowSize=True` 或手动高度覆盖（`_manual_height_override`，`_resolve_content_height` 会在 `required == last_auto_height` 时保留手动高度）。
2. **环境差异**：`devicePixelRatio`（截图 456×777 可能是 125%/150% DPR 的物理像素；逻辑尺寸 = 物理/DPR）、`uiScale`、`position_mode`（截图=bottom-left？）、`slidersTabs`、`showSliders*`。
3. **`sliders_container.sizeHint()` 在用户路径上的滞后**：离线下见到 182 vs 实际 192（抓手关→开的瞬间）；事件驱动重摆理论上兜底，但需在真机验证确实触发（Resize/Move 是否真的到达）。
4. **设置页切换 vs `refresh` 直切**是否走同一条 `mount_changed`（后者可能不触发延迟高度调整）。

## 6. 下一步（建议）

1. 在新会话跑 `python tools/probe_preview_handoff.py`（真机、真实配置、只读不写配置），把生成的 `preview_handoff_report.txt` 作为证据。
2. 若报告显示窗口高度在抓=开后未增长：给设置页切抓手的路径补 `_panel_mount_changed`/`refresh` 的**延迟二次高度调整**（参考 `_panel_mount_apply` 的 singleShot(0) + `_schedule_settle`），并保证在窗口高度增长**之后**才做一次预览簇重摆（当前事件驱动重摆已具备）。
3. 若增长正常但仍重叠：把 `preview_handoff_report.txt` + 截图给新会话，重点核对 `sliders_top`、`preview_bottom`、DPR 换算是否一致。

## 7. 环境收集探针

`tools/probe_preview_handoff.py`（附后）读取用户配置（不写盘），开一个真实窗口，打印：

- DPR / 窗口逻辑与物理尺寸 / `position_mode` / `uiScale`
- 抓=关与抓=开各一次：`win_h`、`minimumHeight`、`_last_required_height`、`column_hint`、`sliders_hint`、`sliders_top`、`tabs` 矩形、`preview` 矩形与底边、是否相交
- 输出：控制台 + 仓库根目录 `preview_handoff_report.txt`

## 8. 2026-09-03 新会话结论（真机已复现根因 + 修复）

执行：`python tools/probe_preview_handoff.py`（真机 = 本仓库所在机器）。

- **真机环境**：DPR 1.5 / 屏幕 2560×1440 / uiScale 100 / `slidersTabs=True` /
  `previewBoxPosition=bottom-left` / `onlyShowInCsp=True` / `noFocusMode=True` /
  保存的 panelLayout = tabs `[ [oklab], [history, hsv] ]`（current=1）。
- **探针修正**（原版在真机上会误判）：测量前必须 `quiesce(win)` 并断开
  `foreground_timer`（`onlyShowInCsp` 会因前台是终端而把窗口藏掉 → 全部
  几何变成 0/残缺、`pending` 卡死）；退出前 `sys.stdout.flush()`（`os._exit`
  会吞控制台输出）。另补充 `manual_override/last_auto/pending/visible/
  container_h/min_hint` 字段与 ON/OFF 截图。
- **根因（真机实测）**：`PanelHost.set_tree` 重建页签树后新根一直处于
  「未 show」状态，QTabWidget 内部（页签栏高度、页内面板）因此量不出来，
  `column_hint()` 在 `_adjust_content_height` 里读 170（真实 248）→
  refresh 直切路径窗口**不长高**（660→670 而非 748/762），预览簇被向上挤/
  钳制（正是用户说的「应该延长窗口长度而不是往上面挤」）。设置保存路径因
  后续 `apply_theme` + 再调一次 `_adjust_content_height` 偶尔被掩盖。
- **修复（本会话，未提交）**：`ui/panels/host.py:set_tree()` 里，当宿主
  已可见时立即 `built.show()`（隐藏时不做——离屏/未显示场景会破坏
  `isVisibleTo` 判定，拖放测试会红）。
- **回归测试**：`tests/test_panel_drag.py::test_a_remount_into_a_visible_host_shows_the_new_root`
  （先红后绿）。
- **修复后真机矩阵**（report 快照，全部 `visible=True`、`pending=False`、
  无重叠）：抓=关 `win_h=708 column_hint=208`；抓=开 `win_h=748
  column_hint=248`（refresh 与 settings 两条路径一致）；预览簇恒定
  `(4,361,79,102)` bottom=463 < `sliders_top=469`，不再被压缩。
- **视觉证据**：`preview_handoff_refresh_on/off.png`、
  `preview_handoff_settings_on/off.png`（ON=684×1122，OFF=684×1062，
  DPR 1.5 → 逻辑高确实 +40；两态页签条与预览簇之间均有可见间隙）。
- **下一步**：请用户在真机跑新打包的 EXE 验证（当前 `dist/` 未重打包）；
  若仍复现，把新截图 + `preview_handoff_report.txt` 发回。
