# OKLCh 颜色转换管线

## 文件位置

| 文件 | 职责 |
|------|------|
| `ui/oklab_colors.py` | OKLab / OKLCh ↔ sRGB 核心数学 |
| `ui/color_wheel.py` | HSV ↔ RGB, 色域裁剪 (find_max_oklch_c), UI 交互 |
| `ui/main_window.py` | 滑块 → 转换 → 同步调度的编排层 |
| `core/csp_companion_sync.py` | sRGB → HSV-u32 → CSP TCP 协议 |

---

## 1. 颜色空间链

```
OKLCh (用户交互)
   │  L∈[0,1], C∈[0,~0.4], h∈[0,360)
   │
   ▼ 【ui/oklab_colors.py:131-140】 oklch_to_rgb
OKLab (中间桥梁)
   │  L∈[0,1], a∈[-0.4,0.4], b∈[-0.4,0.4]
   │
   ▼ 【ui/oklab_colors.py:77-113】 oklab_to_rgb
sRGB (所有空间互换的中枢)
   │  R,G,B ∈ [0,255]
   │
   ▼ 【ui/color_wheel.py:10-13】 rgb_to_hsv (colorsys)
HSV (传给 CSP)
   │  H∈[0,360], S∈[0,100], V∈[0,100]
   │
   ▼ 【core/csp_companion_sync.py】 set_color
CSP uint32
```

---

## 2. oklch_to_rgb — 正向转换

**文件:** `ui/oklab_colors.py:131-140`

```
输入: L (0-1), C (0-0.4), h (0-360°)
输出: R, G, B 浮点数 (0-255，可能溢出)

步骤:
  h_rad = h × π / 180
  a = C × cos(h_rad)          ← OKLCh → OKLab (极坐标→笛卡尔)
  b = C × sin(h_rad)          ← C=0 时 a=b=0，色相信息在此处丢失
  ↓
  调用 oklab_to_rgb(L, a, b)
```

## 3. oklab_to_rgb — OKLab → sRGB

**文件:** `ui/oklab_colors.py:77-113`

```
输入: L (0-1), a (-0.4~0.4), b (-0.4~0.4)
输出: R, G, B (0-255 浮点)
```

### 第一步：OKLab → LMS'（M2 逆矩阵）

```
┌       ┐     ┌                                     ┐ ┌   ┐
│ l'    │     │ 1.0     0.3963…     0.2158…         │ │ L │
│ m'    │  =  │ 1.0    -0.1055…    -0.0638…         │ │ a │
│ s'    │     │ 1.0    -0.0894…    -1.2914…         │ │ b │
└       ┘     └                                     ┘ └   ┘
```

第 13-16 行的 `_M2_INV` 矩阵。

### 第二步：LMS' → LMS（立方还原）

```
L = (l')³
M = (m')³
S = (s')³
```

第 94-96 行。这里的立方根/立方是 OKLab 非线性编码的核心——它让 OKLab 在感知上更均匀。

### 第三步：LMS → 线性 sRGB（M1 逆矩阵）

```
┌        ┐     ┌                                       ┐ ┌   ┐
│ R_lin  │     │  4.0767…   -3.3077…    0.2309…        │ │ L │
│ G_lin  │  =  │ -1.2684…    2.6097…   -0.3413…        │ │ M │
│ B_lin  │     │ -0.0041…   -0.7034…    1.7076…        │ │ S │
└        ┘     └                                       ┘ └   ┘
```

第 19-23 行的 `_M1_INV` 矩阵。

### 第四步：sRGB gamma 编码（线性→gamma）

```python
def _srgb_gamma_encode(c):            # c ∈ [0, 1]
    if c <= 0.0031308:
        return 12.92 × c              # 暗部线性段
    else:
        return 1.055 × c^(1/2.4) - 0.055  # 幂函数段
```

第 37-38 行。

### 第五步：缩放到 0-255

```
R = gamma_encode(R_lin) × 255.0
G = gamma_encode(G_lin) × 255.0
B = gamma_encode(B_lin) × 255.0
```

---

## 4. rgb_to_oklch — 反向转换

**文件:** `ui/oklab_colors.py:116-128`

```
输入: R, G, B (0-255)
输出: L (0-1), C (0-0.4), h (0-360)

步骤:
  1. sRGB gamma decode: γ⁻¹(R/255, G/255, B/255)  → 线性 RGB
  2. M1 矩阵: 线性 RGB → LMS
  3. 立方根: LMS → LMS' (L' = ∛L, 等等)
  4. M2 矩阵: LMS' → OKLab (L, a, b)
  5. 极坐标:
       C = √(a² + b²)
       h = atan2(b, a) × 180/π
```

### 第一步：sRGB gamma decode（gamma→线性）

```python
def _srgb_gamma_decode(c):            # c ∈ [0, 1]
    if c <= 0.04045:
        return c / 12.92               # 暗部线性段
    else:
        return ((c + 0.055) / 1.055) ^ 2.4  # 幂函数段
```

第 31-33 行。

### 第二步：线性 RGB → LMS（M1 矩阵）

```
┌    ┐     ┌                                    ┐ ┌       ┐
│ L  │     │ 0.4122…   0.5363…   0.0514…        │ │ R_lin │
│ M  │  =  │ 0.2119…   0.6807…   0.1074…        │ │ G_lin │
│ S  │     │ 0.0883…   0.2817…   0.6300…        │ │ B_lin │
└    ┘     └                                    ┘ └       ┘
```

第 5-9 行的 `_M1` 矩阵。

### 第三步：LMS → LMS'（立方根）

```
l' = ∛L          (保留符号: copysign(|L|^⅓, L))
m' = ∛M
s' = ∛S
```

第 60-62 行。

### 第四步：LMS' → OKLab（M2 前向系数）

```
L  = 0.2104… × l' + 0.7936… × m' - 0.0040… × s'
a  = 1.9780… × l' - 2.4286… × m' + 0.4506… × s'
b  = 0.0259… × l' + 0.7828… × m' - 0.8087… × s'
```

第 25-28 行的 `_M2_L/A/B` 系数，第 65-67 行计算。

### 第五步：OKLab → OKLCh（笛卡尔→极坐标）

```python
C = sqrt(a*a + b*b)
h = degrees(atan2(b, a))       # 0-360
```

### 第六步：防噪处理

```python
# 第 69-72 行：近消色 RGB → 强制 a=b=0
if abs(r-g) < 0.5 and abs(g-b) < 0.5 and abs(b-r) < 0.5:
    a = 0.0
    b = 0.0
```

---

## 5. rgb_to_hsv / hsv_to_rgb — sRGB ↔ HSV

**文件:** `ui/color_wheel.py:10-17`

直接使用 Python 标准库 `colorsys`:

```python
# 输入: R,G,B ∈ [0,255], 输出: H∈[0,360], S∈[0,100], V∈[0,100]
def rgb_to_hsv(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
    return h*360, s*100, v*100

# 逆变换
def hsv_to_rgb(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h/360, s/100, v/100)
    return int(r*255), int(g*255), int(b*255)
```

colorsys 的 HSV 实现：
```
V = max(R, G, B)
S = (V - min(R,G,B)) / V    (V>0 时，否则 S=0)

当 S = 0:
  H = 0                     ← 关键：消色时 H 总是 0
当 V = R:
  H = 60 × (G-B)/(max-min) mod 6
当 V = G:
  H = 60 × (B-R)/(max-min) + 2
当 V = B:
  H = 60 × (R-G)/(max-min) + 4
```

---

## 6. 色域裁剪：find_max_oklch_c

**文件:** `ui/color_wheel.py:74-97`

```
find_max_oklch_c(L, h) → max_C

功能: 在给定的 L 和 h 下，找到刚好不超出 sRGB 色域的最大色度
算法: 二分查找
  lo=0, hi=0.6
  循环 16 次:
    mid = (lo+hi)/2
    oklch_to_rgb(L, mid, h) → R, G, B
    如果 RGB 都在 [0, 255] → lo=mid (还可以更大)
    否则 → hi=mid (超出了)
  返回 lo
```

这是用 OKLCh 数学往 sRGB 色域方向走，检查 RGB 是否还在 [0, 255] 范围内来逼近边界的二分搜索。

---

## 7. 色域吸附：_snap_to_boundary_oklch

**文件:** `ui/color_wheel.py:1240-1262`

```
_snap_to_boundary_oklch(L, C_raw, scale, px, py, cx, cy, hy, min_x, oklch_h) → best_L

功能: 鼠标点在色域外时，找到色域边界上最近的 (L, C) 对
算法: 两轮搜索
  粗搜索: L 从 0 到 1 取 26 个采样点，每个点算边界 C，
          转换为屏幕坐标，找离鼠标最近的点
  精搜索: 在最佳 L ± 0.04 范围内取 21 个采样点细化
```

---

## 8. CSP 同步：HSV → u32 → TCP

**文件:** `core/csp_companion_sync.py:782-888`

```
输入: R, G, B (8-bit), hsv_u32 元组 (可选)
输出: CSP TCP 消息 SetCurrentColor

流程:
  1. 如果有 hsv_u32 → 直接用
     没有 → rgb_to_hsv_u32(R, G, B)  估算

  2. 防丢策略:
     if S < 0.005:  H = _last_hue_u32     ← 消色回退
     if V < 0.005:  S = _last_sat_u32     ← 纯黑回退

  3. 如果 V ≈ 0 且有显式 HSV:
     先发 V=5% 定位色轮，再发 V=0   ← V=0 flash 技巧
     否则直接发 SetCurrentColor

  4. CSP 协议字段 (uint32):
     HSVColorH, HSVColorS, HSVColorV,
     each = value_in_range / max_range × 0xFFFFFFFF
```

---

## 9. UI 数据流 — OKLCh 模式完整路径

### 9.1 用户拖色片（handle_oklch_slice_drag）

```
鼠标位置 → L, C 参数              ← oklch_h 锁住不动
         → oklch_to_rgb(L, C, oklch_h)              [oklab_colors.py]
         → 色域裁剪 (find_max_oklch_c)              [color_wheel.py]
         → 色域吸附 (_snap_to_boundary_oklch)        [color_wheel.py]
         → rgb_to_hsv(R, G, B)                      [color_wheel.py:15-17]
              if S < 0.5:                           ← C≈0 消色保护
                oklch_to_rgb(L, 0.005, oklch_h)     ← ε=0.005 推算色相方向
                → rgb_to_hsv → 取 H
         → 更新 self.h, self.s, self.v
         → colorChanged.emit(R, G, B)               [信号发射]
                ↓
         on_wheel_color_changed(r, g, b)            [main_window.py]
         → 读取 self.color_wheel.h → hsv 元组
         → update_ui_colors(r, g, b, hsv=(h,s,v))   [main_window.py]
                ↓
         → hsv_u32 = (h/360×U32, s/100×U32, v/100×U32)
         → sync_thread.write_color(..., hsv_u32)     [companion TCP write]
```

### 9.2 用户拖色环（handle_hue_drag OKLCh 模式）

```
鼠标角度 → oklch_h                                   ← 翻转检查
         → 从当前 L, C 状态取 L_ring, C_ring
         → oklch_to_rgb(L_ring, C_ring, oklch_h)    ← C=0.4 始终有色彩
         → rgb_to_hsv → self.h (S>0.5 → 更新 _last_hue)
         → colorChanged.emit → 同 9.1
```

### 9.3 用户拖 L/C 滑块（on_oklch_slider_changed）

```
滑块值 → L = slider/100, C = slider/_C_SCALE
       → oklch_to_rgb(L, display_C, h_cur)
            display_C = min(C, find_max_oklch_c(L, h_cur))
       → rgb_to_hsv
            if S < 1.0:                              ← C≈0 消色保护
              oklch_to_rgb(L, 0.005, h_cur)          ← ε=0.005
              → rgb_to_hsv → 取 H
       → update_ui_colors(..., hsv=(h,s,v))
            → sync_thread.write_color → CSP
```

---

## 10. 关键数值摘要

| 参数 | 范围 | 含义 |
|------|------|------|
| OKLCh L | 0 – 1 | 明度（感知均匀） |
| OKLCh C | 0 – ~0.4 | 色度 (chroma)，0=灰 |
| OKLCh h | 0 – 360° | 色相 (hue angle) |
| OKLab a | ~-0.4 – +0.4 | 绿←→红轴 |
| OKLab b | ~-0.4 – +0.4 | 蓝←→黄轴 |
| sRGB | 0 – 255 | 标准显示色 |
| HSV H | 0 – 360° | CSP 使用的色相 |
| HSV S | 0 – 100 | CSP 使用的饱和度 |
| HSV V | 0 – 100 | CSP 使用的明度 |
| CSP u32 | 0 – 0xFFFFFFFF | 比例编码 |

## 11. ε 值说明

`ε = 0.005` (约 0.5% 的 typical max chroma):

```
oklch_to_rgb(L, 0,     h) → R=G=B          ← 色相丢失
oklch_to_rgb(L, 0.005, h) → R≈G≈B (差~0.5) ← 色相可算

视觉影响: 通道差 < 0.5/255，肉眼完全不可分辨
数学精度: atan2 在通道差=0.5 时稳定，色相误差 < 0.1°
```

选择原因: 不需要单独的"OKLCh→HSV 色相"映射。复用现有的 oklch_to_rgb 管线作为数值偏导 — 取 C 方向上的差分，让 atan2 自动算出 HSV 色相。
