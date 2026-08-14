# 颜色模型与转换管线（Colorink）

本文档描述 Colorink 的颜色转换架构，取代早期分散在多个模块里的转换逻辑。
核心目标：**一个颜色 = 一个不可变的 Color 快照**，所有色彩空间只算一次，
色域映射只做一次，源空间精确往返，色相记忆集中管理。

---

## 1. 文件职责

| 文件 | 职责 |
|------|------|
| ui/color_conversions.py | **数学层**：sRGB gamma、OKLab/OKLCh/CIELAB、色域映射、numpy 向量化变体。纯函数，无副作用。 |
| ui/color_model.py | **模型层**：不可变 Color + 可变 ColorState（色相记忆）。只依赖 color_conversions。 |
| ui/color_wheel.py | 色轮 / 切片视图。渲染缓存 + 拖拽锚点，不再持有权威色。 |
| ui/main_window.py | 编排层：输入 handler -> ColorState.set_from -> _project_color -> update_ui_colors -> 同步。 |
| core/brush_color_spaces.py | **字节兼容层**：CSP/UDM 结构体布局 + u32 缩放 + CMYK GCR。故意不 import UI 数学层，保持与宿主程序逐字节一致。 |
| core/csp_companion_sync.py | sRGB -> HSV-u32 -> CSP TCP 协议。 |

---

## 2. 统一颜色模型（ui/color_model.py）

### Color（不可变）

Color 是一个 frozen dataclass，一次构造算出全部六个空间：

| 字段 | 范围 |
|------|------|
| rgb   | (r, g, b) int 0-255 |
| hsv   | (h 0-360, s 0-100, v 0-100) |
| hls   | (h 0-360, l 0-100, s 0-100) |
| lab   | (L 0-100, a, b) |
| oklab | (L 0-1, a, b) |
| oklch | (L 0-1, C, h 0-360) |

三条设计规则：

1. **构造即色域映射**：Color.from_space 里的 _gamut_map 是唯一的色域映射入口
   （RGB/HSV/HLS 只 clamp；Lab/OKLab/OKLCh 用 CSS Color 4 风格降 chroma，保 L 和 hue）。
   因此任何 Color 都在 sRGB 色域内，调用方无需再 clamp。

2. **源空间精确往返**：用户编辑的那个空间，坐标用「映射后的原值」覆盖，而不是从 RGB
   反推。这从根上消除了旧的 HSV->RGB->OKLCh 往返造成的约 0.2 度色相漂移。

3. **色相记忆**：ColorState 分别记忆 HSV 与 OKLCh 的「消色前色相」，跨空间落到灰色时注入。

### ColorState（可变）

ColorState.set_from(space, values) 构建 Color、更新色相记忆、返回 Color。
无 Qt 信号——调用方用返回的 Color 显式投影，保持路径可测试。

---

## 3. 数据流

输入源（色轮/滑块/LAB页/拾取器/历史/同步回读）
  -> color_state.set_from(space, values)   （只算一次 + 一次色域映射）
  -> Color（六空间快照 + source_space + source_values）
  -> MainWindow._project_color(color, source)
     - 记录 _source_space / _source_values（dict 形式，供同步/历史用）
     - update_ui_colors(color.rgb, source, hsv=color.hsv, oklch=color.oklch, oklab=color.oklab)
       - 色轮 / LAB 页 / 18 个滑块（表驱动投影）
       - 延迟合并的渐变 + L 色域灰显（约 16ms 内）
       - _push_color_to_sync -> CSP/SAI/UDM/PS/companion

关键点：update_ui_colors 接收的是 Color 里**预先算好的** hsv/oklch/oklab 提示，
不再做任何 RGB 反推。source（origin）与 Color.source_space（空间）是正交的两个概念：
前者决定「是否回写同步 / 是否清透明态」，后者决定「以哪个空间写回 CSP 内存」。

---

## 4. OKLCh 转换（正向）

oklch_to_rgb(L, C, h) 管线（ui/color_conversions.py）：

1. 极坐标 -> 笛卡尔：a = C*cos(h)，b = C*sin(h)
2. OKLab -> LMS'（_M2_INV 矩阵）
3. LMS' 立方 -> LMS
4. LMS -> 线性 sRGB（_M1_INV 矩阵）
5. sRGB gamma 编码 -> 0-255

反向 rgb_to_oklch 走 srgb_gamma_decode -> M1 -> cbrt -> M2 -> atan2。

矩阵是 Ottosson 参考值，测试用 coloraide 做独立 oracle 交叉验证。

---

## 5. 色域映射

map_oklch_to_gamut(L, C, h)：固定 L 和 hue，二分搜索把 C 降到 sRGB 边界。
find_max_oklch_c 是 16 次二分搜索；map_lab_to_gamut / map_oklab_to_gamut 同理
（沿 a/b 色相射线降 chroma）。这是 CSS Color 4 风格的「降 chroma」，
不是逐通道 clamp（后者会偏移色相）。

---

## 6. C 滑条：绝对色度

C_oklch 滑条是**绝对色度**，范围 0-321（除以 _C_SCALE = 1000 得到 0-0.321，0.001 分辨率）。
sRGB 内 OKLCh 的绝对最大 C 约 0.3215（仅出现在 L=0.7、h≈328.5 的单一窄点），所以 0.321 已覆盖到最尖端。
拖 L 或 h 使颜色越界时，C 会被色域映射自动钳到边界，超界段在滑条上灰显。

---

## 7. 同步边界

- CSP 内存模式：_resolve_sync_source 读 Color.source_space + Color.source_values
  的 dict 形式，rgb/cmyk/hsv/hls 直写，lab/oklab/oklch 转 float RGB。
- companion 模式：HSV 按比例编码为 u32，写 SetCurrentColor。
- 字节兼容层 core/brush_color_spaces.py 全程不 import UI 层，保持 CSP/UDM 逐字节一致。

---

## 8. 测试

- tests/test_color_conversions.py：anchors（coloraide oracle）、往返、近消色 snap、
  色域映射、numpy/scalar 一致性、HSV/HLS 与 colorsys 交叉验证。
- tests/test_color_model.py：Color 构造、源空间精确往返、色相记忆、恒在色域内。
