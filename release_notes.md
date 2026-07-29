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
