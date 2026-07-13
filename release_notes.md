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
