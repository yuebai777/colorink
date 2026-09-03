# Colorink LAB 圆形色盘修复：副选择点促进 + 圆盘边缘无缺口

> **提交给代理工作流**：按任务逐项实现，使用 checkbox 跟踪。实现一次只改一个文件族，先 RED 后 GREEN。

**目标**：修复 LAB / OKLab 圆形色盘的两个用户可感知问题：

1. 点击副选择点：该副点变成主点（大指示点移到它自己的位置），但**整组点的位置仍按初始主点（锚点）计算，不重排**；**拖动该副点（或拖该主点大点）时 A 跟手，锚点由 `f_harmonic_inverse(A_new)` 反解更新，其余点再按新锚点重新生成（整组随 A 联动，几何骨架不变形）**；点击不动鼠标则保持不重排。
2. 圆盘边缘“小缺口/凹凸”：真实原因是边界按 2048 bin 中心方向计算，少数边缘像素的实际方向更严格、越界后被旧 mask 打成透明；固定为几何圆 `rr <= 1` 的 alpha、越界通道钳制显示后圆内不再有透明缺口；同时保留每色相色域边界颜色（方形 LAB 颜色不丢失），不加描边。

**受影响文件**：

- 修改：`ui/lab_visualizer.py`
- 修改：`ui/lab_prewarm.py`
- 修改：`ui/lab_harmony.py`（矩形和谐模式 → 90° 正方形）
- 修改：`tests/test_lab_disc.py`
- 新增：`tools/visual_lab_disc_check.py`（离屏视觉取证）
- 创建：本文档

**约束**：不改 LAB / OKLab 颜色转换、色域边界数学、方形平面路径、同步协议；圆盘 alpha 保持 `rr <= 1` 的完美圆；不新增依赖。

---

## Task 1：副选择点“点击变主点但不重排；拖拽才更新锚点”

### 现状（根因）

`mousePressEvent()` 命中和谐小点后执行 `self.a, self.b = hit`；`_harmony_points_ab()` 又以 `self.a/b` 为基色计算，于是整组小点围绕新主点重排——用户只想要“点谁谁变主点”，不想位置跳。

### 目标行为（多轮确认）

- 点击副点 A：A 原位变大成为主点；颜色应用到前景/背景；**整组点位置不变**，原主点原位变回小点；
- 再点 B：B 变主点；A 回小点；位置仍不变；
- 点回锚点：锚点变回大主点；
- 点击空白处：该位置成为新锚点，整组点按它重排；
- 拖动副点 A：A 跟手，锚点跟着 A 移动，其余点按移动后的锚点重新计算相对位置（整组一起动）；
- 拖动空白处/主点（锚点）：当前位置成为新锚点，整组点实时重排；
- 拖动 L：颜色更新；若当前主点来自副点，保持同一和谐索引；排布仍按当前锚点。

### 实现

1. `__init__` 增加：
   - `self._anchor_ab = (self.a, self.b)` —— 锚点（初始主点），排布只由它计算；
   - `self._picked_harmony_index = 0` —— 当前主点 = 0 锚点 / 1..副点；
   - `self._drag_from_dot` —— 按下是否起始于副点（区分“点击”与“拖拽”）。
2. `_harmony_points_ab()`：基色改用 `self._anchor_ab`，与当前主点解耦。
3. `_draw_disc_overlay()`：小点画“除当前主点外”的所有点（含锚点变小时后）；大点画在当前主点 `(self.a, self.b)`。
4. `_hit_harmony_point()`：除当前主点（大点）外都可点，锚点是小点时也可点回。
5. `mousePressEvent()`：
   - 命中副点/小点：`self.a, self.b = hit`、`_picked_harmony_index = i`、`_picked_ab = hit`、发色；**锚点不动**；进入拖拽态但点击不动鼠标就不会重排；
   - 大点是“副点变来的主点”（`index > 0`）：也按“副点手柄拖拽”处理（`_drag_from_dot=True`），不能当成空白/锚点按下；
   - 空白 / 大点=锚点（`index == 0`）：直接 `handle_mouse(pos)` → 新锚点 + 重排。
6. `handle_mouse()`（圆盘）：
   - 拖副点 A（`_drag_from_dot and index > 0`）：`a, b = 鼠标映射 = A_new`；`_anchor_ab = _anchor_from_harmony_point(A_new, index)`（f_harmonic 的**逆变换**，反解出锚点）；`_picked_harmony_index` 保持 `i`（A 仍在其和谐槽位）；
   - 否则（空白 / 锚点拖拽）：`a, b = 鼠标映射`；`_anchor_ab = (a, b)`；`_picked_harmony_index = 0`。
   - `_harmony_points_ab()` 始终以 `_anchor_ab` 为相对位置来源 → 其余点按反解后的锚点重新生成，**整组随 A 联动，骨架不变形**。
   方形路径走“空白拖拽”逻辑（无和谐点）。
7. 新增 `_anchor_from_harmony_point(a, b, index)`：由 A_new 的 `(hue, C)` 反推 `rho = C / max_c(hue_A)`，再按 `hue_anchor = hue_A - offset_index` 与 `C_anchor = rho * max_c(hue_anchor)` 求出锚点坐标。
8. `set_color` / `set_oklab` / `set_render_mode` / `set_shape` / `set_harmony_mode`：复位锚点到当前主点、`_picked_harmony_index = 0`。
9. `set_lightness()`：先钳制锚点；若 `_picked_harmony_index > 0` 且 disc：取新 L 下同索引点作为当前主点；否则主点 = 锚点。

### 验收

- 点击副点：`(a, b) == 副点`；`_anchor_ab` 不变；所有点坐标不变；`_picked_harmony_index == i`；
- 再点另一副点：同上；
- 点回锚点：锚点变回主点，排布不变；
- 点击空白：锚点 = 点击位置，`points[0]` 随之更新；
- 拖动副点：A 到新位置；锚点 = `_anchor_from_harmony_point(A, index)`（反解，不直接取 A）；`points[index] == A`、`points[0] == 反解锚点`，其余点联动；
- 拖动空白/锚点：锚点更新，`points[0] == 新锚点`；
- L 变化：主点跟随同一索引，锚点不变；
- 相关测试：`test_clicking_harmony_dot_promotes_it_without_reanchoring`、`test_click_anchor_after_selecting_dot_returns_to_anchor`、`test_drag_harmony_dot_reanchors_pattern`、`test_click_blank_reanchors_pattern`、`test_lightness_change_keeps_picked_harmony_index` 全绿。

### 追加变更：矩形和谐模式改为 90° 正方形（Procreate 风格）

- 用户确认：Procreate 的「矩形」和谐模式四个点间隔 90°，连起来是正方形；我们原来 `(0, 60, 180, 240)` 是 60°/120° 矩形。
- 修改 `ui/lab_harmony.py`：`"rectangle": (0.0, 90.0, 180.0, 270.0)`，基色仍占 0° 第一位。
- 只改点位的相对分布；点击/拖拽/反解联动逻辑不变。
- 测试：仍为 4 点；新增断言四点间隔为 90°（正方形）。

### 追加变更：点击点防误触抖动容差

- 用户需求：数位板/画笔/鼠标点色盘上的**点**（副点或主点）时，1~2px 抖动会被误判为拖拽，整组点瞬间飘走；要求只有明确划出阈值才进拖拽。
- 规则：
  - 仅命中点（副点/主点）时启用防抖；空白处仍按下即重排（无容差）；
  - 按下点后移动 ≤ `_POINT_CLICK_TOLERANCE_PX`（5px，用户可接受 4~6px 或 Qt startDragDistance）→ 忽略，视为原地按压；
  - 释放时未超阈值 → 纯点击：只做主/副点切换与选色，坐标零漂移；
  - 超出阈值才 `_drag_armed=True` → 正式拖拽，走前面「反解锚点 + 整组联动」。
- 实现（`ui/lab_visualizer.py`）：新增 `_press_on_point` / `_press_origin` / `_drag_armed`；`_begin_press()` 统一武装；`mouseMoveEvent` 对点按先量距、超阈值才 `handle_mouse`；`end_drag` 清理全部状态；大点（主点）按下也按点处理（index>0 走反解，index=0 走直接锚点）。
- 测试：`test_point_press_jitter_within_tolerance_stays_click`、`test_point_press_beyond_tolerance_becomes_drag`、`test_main_point_press_jitter_does_not_reanchor`。

---

## Task 2：保留方形 LAB 全部颜色 + 圆盘 alpha 恒为完美圆（去掉描边）

### 最终根因（按用户截图确认）

- 圆盘边缘“凹凸”不是外圈颜色深浅造成的“感知”问题，而是**真实的小透明缺口**：
  `render_lab_disc` 按 2048 个 bin 中心方向计算每色相边界 `max_c`；在色域边界变化很快的尖锐角附近，某个像素的**确切方向**比它的 bin 中心更严格，`chroma = rr * max_c` 会略超出该像素的真实边界；
  旧 mask 对这类像素返回 alpha=0 → 圆盘边缘在很小的特定颜色位置出现缺口/锯齿（近白 L 下最明显，已在截图确认）。
- 颜色没有被“压暗丢失”；丢的是 alpha 上的小洞。

### 实现

1. `ui/lab_prewarm.py::render_lab_disc()`：
   - 保留每色相边界：`chroma = np.clip(rr, 0.0, 1.0) * max_c`（`max_c = min(smoothed, raw)`，方形 LAB 颜色不丢）；
   - **alpha 只由几何圆决定**：`mask = rr <= 1.0`，不再把个别越界像素打成透明；
   - `_rgba_bytes` 本身会对越界通道钳制到 0..1，所以极少数边界像素以钳制色显示，视觉上只是边缘一圈极小的色彩微调，不再有透明缺口。

2. `ui/lab_visualizer.py`：
   - **不画描边**（按用户要求移除）；
   - `_max_chroma_for_direction()` 按 hue 返回 `smoothed_boundary_chroma(mode, L, hue)`（渲染器同款边界），取色/和谐点与绘制一致；
   - `_disc_chroma_ceiling()` 返回 `float("inf")`（无统一上限，仅供兼容/测试）。

### 边界

- 极低/极高 L：各色相外圈到自己的色域边界，仍为圆；`_disc_ab_to_screen` 已有 `max_c <= 1e-9` 除零保护；
- lab / oklab 切换、L 拖拽：仍在 `_cached_chroma_profile` 缓存命中；
- 渲染器与交互共用同一个每色相边界函数，不会出现“画的半径”与“取的半径”不一致。

### 验收

- 圆内不允许任何透明像素：新增 `test_disc_no_transparent_notches_inside_circle`（lab/oklab × L=92/95 等近白场景，旧代码会失败）；
- `rr=1.05` 处全部透明、`rr=0.95` 处全部可见（`test_disc_alpha_mask_is_circle`）；
- 每个色相外圈颜色与方形 LAB 平面一致（`test_disc_rim_keeps_each_hues_square_plane_colour`）；
- 截图中圆盘无描边、无边缘缺口；近白 L 下也不再有“特定颜色位置的小咬口”；
- `test_disc_edge_point_is_in_gamut` 以每色相边界通过。

---

## Task 3：测试与验证

### 测试改动（`tests/test_lab_disc.py`）

- `test_clicking_harmony_dot_promotes_it_without_reanchoring`：点副点 → 主点变为该副点、锚点不变、所有点坐标不变；
- `test_harmony_dot_click_promotes_dot_but_keeps_anchor`：互补模式单点验证同样语义；
- `test_click_anchor_after_selecting_dot_returns_to_anchor`：副点选中后锚点变成小点，可点回；
- `test_dragging_promoted_dot_reanchors_pattern`：拖动副点 → `f_harmonic_inverse` 反解锚点，A 仍在其和谐槽位，整组随 A 联动；
- `test_dragging_promoted_dot_from_large_dot_reanchors_pattern`：真实序列——点 A 变主点释放后再按大点 A 拖动，同样反解锚点、整组联动；
- `test_click_blank_reanchors_pattern`：点击空白 → 锚点=点击位置，整组重排；
- `test_drag_blank_area_reanchors_pattern_and_resets_selection`：拖动空白 → 新锚点、整组重排、选中索引回 0；
- `test_lightness_change_keeps_picked_harmony_index`：L 变化时主点保持同一和谐索引、锚点不变；
- 新增 `test_disc_rim_keeps_each_hues_square_plane_colour`（lab/oklab × 多 L：每个色相外圈达到自己的色域边界，方形 LAB 颜色不丢失）；
- 新增 `test_disc_alpha_mask_is_circle`（4 角透明、中心可见、环上无缺口）；
- 新增 `test_disc_no_transparent_notches_inside_circle`（近白 L 回归：几何圆内不允许任何透明缺口像素）。

### 运行

```powershell
python -m pytest tests/test_lab_disc.py tests/test_render_quality.py -q
python -m pytest -q
python -m compileall -q core ui tests
```

### 视觉取证

`QT_QPA_PLATFORM=offscreen` 渲染 LAB 圆盘 L=20/50/80 × lab/oklab 与副点点击前后截图，人工复核：
- 每个色相外圈保留方形 LAB 平面的颜色（不丢失）；圆盘无描边、边缘无透明缺口（近白 L 下尤其要确认）；
- 点击副点后主指示点移到该副点原位，锚点与其余点位置不动、取色/滑块已更新。

---

## 成功标准

1. 点击副选择点：该副点变成主点（大指示点移到此点原位），所有点位置不变；再点另一个同样。
2. 点击主选择点本身：位置不跳变、颜色不变（等价于以当前位置刷新锚点）。
3. 点击副点：不重排；拖动副点（含先点击释放后再拖大点）：A 跟手，锚点由 `f_harmonic_inverse` 反解更新，整组按新锚点联动；拖动空白/锚点：锚点直接设为当前位置，整组重排。
4. 圆盘保留方形 LAB 平面的全部颜色（每个色相外圈达到自己的色域边界）；无描边；几何圆内无透明缺口（近白 L 也不例外），边缘视觉完美圆。
5. 圆盘 alpha 为几何圆 `rr <= 1`；边界极少数越界通道钳制显示而非打成透明；渲染/交互/和谐点共用同一每色相边界。
6. 相关测试全绿，全量无回归，compileall 通过。
