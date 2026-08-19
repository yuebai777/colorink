# Colorink

基于 PyQt6 的 Windows 桌面取色器 / 调色工具，为绘画与设计工作流设计：全局取色、多色空间精确调色、绘画软件颜色同步，以及系统级灰度滤镜。

当前版本：**v1.6.9**

## 下载

最新版下载页：**https://github.com/yuebai777/colorink/releases/latest**

下载 `Colorink.exe`（onefile 单文件版），双击即可运行，无需安装、无需解压；另提供 `Colorink-Onedir.zip` 便携目录版。

> 如果 Windows 提示“Windows 已保护你的电脑”，点击“更多信息”→“仍要运行”。

### 环境要求

- Windows 10 或更高版本（64 位）
- 不需要安装 Python，也不需要安装其它依赖（发布版已内置）

## 功能特性

- **全局取色** — 任意位置按 `F11` 打开全屏取色放大镜，先截图再取色，预览中不会出现窗口自身；高分屏 / 多显示器 / 不同 DPR 自动适配
- **色空间模块** — HSV / HLS / LCH 三个模块，一键切换对应色轮与滑块组合
- **多色域滑块** — RGB、HSV、HSL、CIELAB、OKLab、OKLCh 全部可调节；OKLCh 使用真实色度坐标（0~0.321），滑块轨道标出超出 sRGB 边界的部分
- **统一色彩模型** — 六种色空间的取色值收敛为单一颜色快照，源坐标精确往返，消除 RGB 往返导致的色相漂移；无彩色时按 HSV / OKLCh 分别记忆色相并回填
- **前景 / 背景双色槽** — 一键交换、复制、对比；支持**透明色**标记（CSP 同步透传）
- **取色历史** — 持久化色板，行列与色块大小可调；外部软件同步的颜色也会录入
- **灰度滤镜** — 双后端：
  - **Native**：DXGI 桌面复制 + OpenGL 覆盖层，支持 OKLCh 感知灰度与 BT.709 Luma，可只作用于指定屏幕，启动时后台预热，切换接近即时
  - **Mag**：Windows 放大镜 API 系统级 Luma（BT.709），零捕获、零覆盖窗口，与系统颜色滤镜同路径
- **全局热键** — 所有快捷键可绑定键盘、鼠标侧键 / 中键甚至数位板笔按键；全局鼠标热键不拦截点击，绘画软件仍能收到
- **LAB 视图** — 鼠标悬停色轮 / LAB 区域时按 `Space` 快速切换，或任意位置按 `Ctrl+L`；色轮与 LAB 之间可显示浮动切换按钮
- **绘画软件同步** —
  - **CSP Companion**：CLIP STUDIO PAINT「连接智能手机」TCP 协议，二维码自动扫描（失败可手动粘贴 URL），无需内存扫描
  - **CSP 内存同步**：支持 CSP 5.0 / 5.1（5.1 自动检测新全局颜色槽布局），主 / 副色槽独立双向同步，透明标志透传
  - **Photoshop 桥接**：自动检测所有 PS 实例（注册版走 COM / 绿色便携版走隐藏 CEP 扩展桥），COM 不可用自动回退脚本桥，独立读写前景 / 背景双色槽
  - **SAI2 / UDM**：笔刷颜色内存同步
- **更新检查** — 从 GitHub Releases 拉取更新，下载先落 `.part` 临时文件、按字节数 + SHA-256 校验后原子替换并自重启；支持跳过指定版本
- **界面语言** — 设置中可在「自动 / 中文 / English」间切换
- **无边框置顶窗口** — 极简悬浮，不用时贴边隐藏；`Ctrl+Shift+T` 切换标题栏显隐
- **高度可定制** — 滑块顺序 / 显隐、色空间模块、主题、UI 缩放；Ringless 模式可隐藏色环、放大颜色切片
- **稳健性** — DPI 自适应、单实例锁、原子配置写入、崩溃报告（`stderr.log` 定位）；设置页可一键「复制诊断信息」（版本 / 同步状态 / 最近日志）便于反馈问题

## 截图

![Colorink 截图](screenshots/screenshot.png)

## 从源码运行（给开发者 / 想改代码的人）

需要先安装 Python 3.10+。

```bash
git clone https://github.com/yuebai777/colorink.git
cd colorink
pip install -r requirements.txt
python main.py
```

或双击 `run.bat`（以 `pythonw` 无控制台窗口启动）。

## 快捷键（可在设置中修改）

| 功能 | 默认 |
|------|------|
| 取前景色 | `F11` |
| 切换跟随鼠标 | `Ctrl + R` |
| 显示 / 隐藏窗口 | `Ctrl + H` |
| 显示 / 隐藏标题栏 | `Ctrl + Shift + T` |
| 切换灰度滤镜 | `Ctrl + G` |
| 切换 LAB 视图（鼠标悬停色轮 / LAB 区域） | `Space`（也可设为鼠标侧键 / 中键） |
| 切换 LAB 视图（全局） | `Ctrl + L` |

所有快捷键均可绑定鼠标按键（侧键 / 中键等）或数位板笔按键。全局快捷键绑定鼠标按键时，点击不会被拦截，画画软件仍会收到该点击。

## 灰度滤镜说明

- **Native（默认）**：OKLCh 感知灰度更符合人眼明度感知；Luma 模式为标准 BT.709 权重。可指定只对某一台显示器生效。依赖 dxcam + OpenGL，GPU 需支持 OpenGL 3.3+
- **Mag**：系统级灰度，全屏生效，仅提供 Luma 模式，兼容性最好（不支持 OpenGL 的环境可用）

所选后端无法运行时不再静默切换，而是明确提示原因，可在设置中手动更换。

## 打包（独立 EXE）

```bash
pip install pyinstaller
python build_pyqt.py
```

打包输出在 `dist/` 目录：

- `dist/Onedir/Colorink/`：目录版，便于排查运行依赖
- `dist/Onefile/Colorink.exe`：单文件版，适合作为 GitHub Release 下载资产

打包前会校验 `APP_VERSION`、Windows 文件版本与 `release_notes.md` 三者一致，版本漂移直接失败。CSP Companion 的二维码自动扫描依赖 `pyzbar`、`Pillow`、`mss`；源码安装会随 `requirements.txt` 安装，发行包已内置。二维码不可用时仍可手动粘贴连接 URL。

## 开发

- 灰度原生运行时（`native_grayscale/runtime/grayscale_overlay.pyc`）随仓库分发，`python native_grayscale/build_runtime.py` 可复现编译；原生灰度不可用时自动回退 Mag 后端
- `tests/` 含发布契约测试（版本一致性、首次启动默认值、配置合并等），`python -m pytest` 运行
- 改动后要跑哪些测试、发版前要过哪些关，见 [TESTING.md](TESTING.md)
- `tools/` 含同步诊断与端到端稳定性脚本（内存探针、写路径对比等），便于排查 CSP / PS 同步问题
- 架构与设计说明见 [DESIGN.md](DESIGN.md)，色彩转换管道见 [OKLCH_CONVERSION_PIPELINE.md](OKLCH_CONVERSION_PIPELINE.md)

## License

MIT — 详见 [LICENSE](LICENSE)。
