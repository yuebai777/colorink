## v1.5.2

OKLCh 切片宽幅化与窗口紧凑适配发布。

### 新增

- **OKLCh 切片按色相自适应宽度**：每个色相的色域都独立缩放并填满切片框宽度，彩色区域不再受最宽色相限制，始终与切片框同宽；Ringless 模式下切片框扩展到整行可用宽度，取色区域更大、更容易精准取色
- **设置窗口紧凑化**：设置窗口由 560px 收窄至 460px，左侧导航栏由 112px 收窄至 96px，整体更紧凑；导航图标改为 72px 高清画布绘制，HiDPI 下线条更锐利

### 修复

- **色轮下弧裁剪**：窗口较矮较宽（手动缩放、低屏幕空间）时色轮下半弧会被裁出可视区，现在色轮尺寸按窗格高度钳制，超出时缩小而不是裁剪
- **OKLCh 色域轮廓丢失**：色相变化后预渲染切片落地时，色域边界描边会消失，现在预渲染图像上方仍会绘制色域轮廓
- **OKLCh 指示器越界**：切换色相后存储的亮度 / 彩度可能超出新色相的最大彩度，指示点会飘到彩色区域之外，现在会钳制在色域边界内
- **预渲染与控件不一致**：后台预渲染使用 21 点彩度扫描而控件使用 201 点，导致缓存图与轮廓描边、指示器比例错位，统一为 201 点扫描并按切片框宽度缩放，两者完全一致

### 变更

- **版本号**：应用内更新检查版本与 Windows 文件版本统一到 `1.5.2` / `1.5.2.0`

---

## v1.5.1

设置面板导航重构与稳定性修复发布。

### 新增

- **设置面板左侧导航栏**：设置界面由标签页改为 CSP 环境设置风格的左侧导航栏 + 内容页布局；导航图标由 QPainter 按当前主题绘制并支持 HiDPI 高清渲染
- **取色器设置整理**：取色放大倍率与前背景色位置设置移入取色器分区，色轮分区仅保留可视化相关选项；「高级」改为扁平分区展示

### 修复

- **配置目录回退**：系统环境变量 `APPDATA` 未设置时回退到用户主目录，避免配置文件读写失败
- **CSP 内存同步**：`pymem` 不可用或模块未找到时给出明确错误，不再静默失败
- **Photoshop 同步**：连接流程重构并补充进程存活检查，避免对已退出的 PS 发起 COM 调用
- **Rust 灰度滤镜**：子进程 stdin 不可用时安全跳过，不再抛出异常
- **主窗口空值防护**：修复多显示器、系统样式缺失等边界场景下的潜在崩溃（取色定位、拖拽缩放、托盘图标、灰度后端切换）

### 变更

- **代码现代化**：类型标注全面迁移至内建泛型（`dict` / `tuple`）与 `X | None` 语法，统一导入排序
- **版本号**：应用内更新检查版本与 Windows 文件版本统一到 `1.5.1` / `1.5.1.0`

---

## v1.5.0

无环取色（Ringless）模式与设置窗口重构发布。

### 新增

- **Ringless 模式**：可隐藏色环并放大颜色切片，主界面切换为矩形前景 / 背景色块与顶部控制栏布局；控制栏支持前景 / 背景槽、模块与模式按钮，按钮位置按共享基线对齐，切换面板时不再跳动
- **颜色预览框**：独立的前景 / 背景双色块组件，统一绘制与命中测试几何，支持右键菜单快速复制 RGB / HEX 值
- **独立设置窗口**：设置界面改为带标题栏的独立无边框窗口，可拖动、跟随主窗口贴靠并自动限位；设置侧栏支持分区滚动与更细粒度的模块控制
- **切片与 LAB 预渲染**：颜色切片和 LAB 可视化改为后台线程预渲染，切换模块时即时显示缓存结果，减少交互卡顿
- **热键按钮组件**：设置中的热键录入改为专用按钮组件，交互状态更清晰

### 变更

- **色轮与颜色转换重构**：色轮绘制、OKLab / OKLCh 转换管道全面重构，消除 RGB 往返漂移，提升绘制一致性
- **图标资源**：新增复选框、下拉箭头等主题图标资源
- **版本号**：应用内更新检查版本与 Windows 文件版本统一到 `1.5.0` / `1.5.0.0`

---

## v1.4.0

围绕 CSP Companion、色空间模块和打包发布一致性的一次功能发布。

### 新增

- **CSP Companion Mode**：新增基于 CLIP STUDIO PAINT「连接智能手机」协议的 TCP 同步后端，可在不依赖内存扫描和版本偏移的情况下读写 CSP 颜色；支持保存连接会话、重新连接、断开连接，以及二维码扫描失败时手动粘贴连接 URL
- **色空间模块切换**：新增 HSV / HLS / LCH 三个色空间模块，每个模块预设对应色轮和滑块组合；模块切换按钮可显示在主界面，也可在设置中关闭
- **OKLab / OKLCh 交互增强**：改进 OKLab、OKLCh 滑块、色域范围和亮度拖动逻辑，减少 RGB 往返导致的色相/饱和度漂移
- **内容驱动窗口高度**：窗口高度按当前可见内容自动收缩或扩展，减少首次打开和切换模块后的空白区域
- **发布契约测试**：新增版本一致性、首次启动默认值和配置合并测试，防止应用版本、Windows 文件版本和发布说明脱节

### 修复

- **首次启动默认值**：默认热键统一为 `F11` / `Ctrl+R` / `Ctrl+H` / `Ctrl+G`，与 README 和设置界面一致；默认仅展示紧凑的 HSV 模块与颜色历史，历史色板为 `8 × 2`
- **Brush Link 精度**：CSP / UDM 写入 HSV 时保留低饱和度和黑色场景下的色相、饱和度状态，降低同步到绘画软件后的颜色跳变
- **滑块模块联动**：设置面板现在按当前色空间模块过滤滑块行，避免显示不属于当前模块的滑块组
- **打包崩溃排查**：新增全局异常日志输出到 `stderr.log`，便于定位打包后未捕获异常

### 变更

- **版本统一**：应用内更新检查版本与 Windows 文件版本统一到 `1.4.0` / `1.4.0.0`
- **打包说明**：README 补充 Onedir / Onefile 输出说明，以及 CSP Companion 二维码扫描依赖

---

## v1.3.0

Rust D3D11 灰度滤镜后端和打包瘦身发布。

### 新增

- **Rust D3D11 灰度滤镜**：新增 Rust D3D11 后端并集成到设置界面，可作为灰度滤镜渲染后端选择

### 变更

- **构建配置**：移除废弃 D3D11 Overlay 和调试 spec，更新 PyInstaller 配置并移除 OpenCV 运行依赖，减小打包体积
- **版本号**：Windows 文件版本升级到 `1.3.0.0`

---

## v1.2.2

修复 Photoshop 颜色同步卡死和取色历史丢失。

### 修复

- **Photoshop 颜色同步**：调整 COM ProgID 查询顺序，优先使用版本无关的 `Photoshop.Application` 而非残留的 `Photoshop.Application.140`（该废弃条目每次重连会阻塞 ~30 秒），同步延迟从数十秒降至毫秒级
- **取色历史**：外部软件（CSP/SAI/UDM/PS）颜色同步变化时，现在正确录入取色历史，不再丢失外部取色的记录
- **滑块跟手**：拖动滑块/色轮时，滑块轨道渐变色和 L 色域遮罩改用延迟渲染，手柄始终紧跟光标不再卡顿

---

## v1.2.1

设置面板新增「关于」一栏：检查更新 / 关于作者。

### 新增
- **检查更新**：后台线程查询 GitHub releases，对比 `APP_VERSION` 与最新 tag；发现新版本时弹出公告并附带跳转下载按钮，已是最新则提示当前版本
- **关于作者**：打开作者 Bilibili 主页
- 新增 `core/updater.py`（仅依赖标准库 `urllib` / `json`，不影响打包体积）
- `file_version_info.txt` 同步升级到 `1.2.1.0`

---

## v1.2.0

新增全局取色功能，并修复 OKLCh 的 L 值条。

### 新增

- **全局取色**：在任意位置通过热键（默认 `F11`）触发全屏取色放大镜
  - 截图思路改造为「先全屏静态截图，再从图中取色」，确保预览里再也不会出现小窗口自己、其它悬浮窗或叠加层的边缘
  - 高分屏 / 多显示器 / 不同 DPR 自动适配
  - 视觉上隐藏系统光标，仅保留细十字辅助点跟随鼠标
  - 自定义十字辅助点在所有屏幕（含高 DPI 主屏）下均完整显示

### 修复

- **OKLCh 的 L 值条**：滑块联动不再错位 / 跳变

---

## v1.1.1

Fix: grayscale overlay (OpenGL / Ctrl+G) broken after PyInstaller packaging.

### Root Cause
dxcam dynamically imports OpenCV via `importlib.import_module("cv2")` which PyInstaller's static analysis cannot detect. This causes `ModuleNotFoundError: No module named 'cv2'` at runtime, crashing the frame processing thread.

Additional issues: `GrayscaleOverlay` missing `is_healthy` attribute (AttributeError), dxcam C extension (`_numpy_kernels.pyd`) not bundled, and overlay C++ EXEs not included.

### Changes
- **ui/grayscale_overlay.py**: Added `is_healthy` property + QTimer-based delayed health check (2.5s after overlay creation)
- **core/dcomp_grayscale.py**: Fixed DComp EXE path lookup for PyInstaller-frozen environments (`sys._MEIPASS`)
- **PyInstaller specs**: Added hidden imports (`cv2`, all dxcam submodules, `comtypes`), explicitly bundled dxcam C extension `.pyd` and `dcomp_overlay.exe`/`sc_overlay.exe`
- **build_pyqt.py**: Now builds both onedir (folder) and onefile (single EXE) outputs simultaneously
