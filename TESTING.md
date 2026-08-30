# Colorink 测试清单

> 用法：改完东西对着这张表勾。**不是每次都要全测** —— 按下面三档执行。
> 只想按顺序打勾跑手测 → **[CHECKLIST.md](CHECKLIST.md)**（纯手动项，自动化命令已剔除）。
> 基线（2026-08，v1.6.17）：`python -m pytest -q` → **801 passed，约 43 秒**。

## 0. 先决定要测哪一档

| 这次改了什么 | 要做的档位 | 花多久 |
|---|---|---|
| 只改 README / 注释 / release_notes | A1 | 1 分钟 |
| 改了任意 `.py`（哪怕一行） | **A 档全做** + B 档对应区域 | 3~10 分钟 |
| 动了 `*.spec` / `build_pyqt.py` / `icons/` | A + B18 + **C2 打包** | +10 分钟 |
| 要打 tag 发版 | **A + B + C 全套** | 40~60 分钟 |

原则：**自动化测试每次都跑（便宜）；手测只测你碰过的那块（贵）；打包和更新链路只在发版时测（最贵）。**

---

## 1. 环境基线（换机器时确认一次）

- [ ] 测试必须用 **Windows 的 Python** 跑。WSL / Linux 上有 13 个测试文件在采集阶段就崩（`ctypes.WinDLL` 不存在），不是代码坏了。
- [ ] 依赖：`pip install -r requirements.txt -r requirements-dev.txt`
      （`coloraide` 只用于测试，作为 `ui/color_conversions.py` 的独立对照实现）
- [ ] 确认基线绿：`python -m pytest -q` → `801 passed`
- 当前验证过的组合：Python 3.14 / PyQt6 6.7.1 / pytest 9.1.1

---

## 2. A 档 · 每次改完代码都做（约 3 分钟）

- [ ] **A1 全量单测**：`python -m pytest -q`
      → 必须 `801 passed`（加了功能就该 >801；**数字只许涨，不许跌**）
      → 失败时看单个文件：`python -m pytest tests/test_xxx.py -v`
- [ ] **A2 冷启动**：`python main.py` 能出窗口；控制台无 traceback；`stderr.log` 没有新增内容
- [ ] **A3 四个基本动作**（10 秒扫一遍，防低级回归）：
      按 `Ctrl+Alt+Q` 取一次色 → 切一次色空间模块（HSV/HLS/LCH）→ 拖任一滑块 → 前景/背景交换
- [ ] **A4 配置往返**：改一项设置 → 完全退出 → 重开，设置还在（`core/config.py` 原子写没坏）
- [ ] **A5 提交前**：`git status` 干净，`dist/` `build/` `__pycache__/` `stderr.log` 没混进去

---

## 3. B 档 · 改到哪就加测哪

左列 = 你碰过的文件；中列 = 复制粘贴就能跑；右列 = 自动化管不到、必须手动看的点。

| # | 改动区域 | 定向自动化测试 | 必须手测 |
|---|---|---|---|
| B1 | `ui/color_conversions.py` `ui/color_model.py` `ui/oklab_colors.py` | `pytest tests/test_color_conversions.py tests/test_color_model.py tests/test_color_projection.py -q`（76 项） | 六组滑块之间来回切不漂移；OKLCh 色度到 0.321、超 sRGB 段轨道有标记；灰色/白色改色相后仍记得住 |
| B2 | `ui/color_wheel*.py` `ui/slice_prewarm.py` `ui/lab_*.py` `ui/lab_visualizer.py` | `pytest tests/test_ringless_geometry.py tests/test_ringless_interaction.py tests/test_slice_prewarm.py tests/test_picker_components.py -q` | 色环拖拽跟手不跳；`Space`（悬停色环/LAB 区）和 `Ctrl+Alt+L` 都能切 LAB；UI 缩放调到 125% / 150% 不错位 |
| B3 | `ui/ringless_mode.py` `ui/picker_panes.py` `ui/color_preview_box.py` `ui/ringless_settings.py` | `pytest tests/test_ringless_panes.py tests/test_ringless_settings.py tests/test_ringless_preview_geometry.py tests/test_ringless_preview_rendering.py tests/test_ringless_preview_interaction.py tests/test_ringless_visible_geometry.py tests/test_ringless_invalidation.py -q`（107 项） | 「隐藏色环放大切片」开/关各走一遍；控制条左右侧都试；来回切 pane 时 LAB 按钮位置不跳 |
| B4 | `ui/main_window.py` `ui/window/*.py` | `pytest tests/test_window_height.py tests/test_title_bar_toggle.py tests/test_ringless_integration.py tests/test_ringless_lifecycle.py tests/test_sync_mixin.py -q` | `Ctrl+Alt+K` 标题栏显隐；`Ctrl+Alt+Y` 显隐窗口；贴边隐藏后能拉回；拖到第二块屏正常；窗口高度随内容变化 |
| B5 | `ui/color_picker_overlay.py` `core/foreground.py` `core/picker_hook.c/.dll` | `pytest tests/test_picker_zoom.py tests/test_picker_components.py tests/test_foreground_detection.py -q` | **真机必测**：`Ctrl+Alt+Q` 全屏放大镜；预览里不出现 Colorink 自己的窗口；主副屏 + 不同缩放各取一次；画画软件在前台时无焦点取色仍生效 |
| B6 | `core/native_grayscale.py` `native_grayscale/` | `pytest tests/test_native_grayscale.py tests/test_native_grayscale_contract.py -q` | `Ctrl+Alt+D` 连开关 5 次不崩；OKLCh 与 Luma 两种模式；限定单显示器生效；改分辨率/DPI 后覆盖层还对；不可用时**有明确提示**而不是静默 |
| B7 | `core/mag_grayscale.py` `mag_overlay/` | `pytest tests/test_mag_grayscale.py -q` | 全屏灰度生效；退出后颜色完全恢复；和系统颜色滤镜同时开不打架 |
| B8 | `core/csp_brush_link/` `core/memory_sync.py` | `pytest tests/test_csp_brush_link_5_1.py tests/test_csp_copy_locate.py tests/test_csp_legacy_profiles.py tests/test_memory_sync_transparent_readback.py tests/test_memory_sync_ps_echo.py -q` | CSP 里改色 ↔ Colorink 双向同步；**主色槽 / 副色槽必须各自单独测一遍**（历史上出现过「前景色不同步、背景色正常」的单边坏）；**从 CSP 默认色起手**（黑前景 / 白背景，两个都是内存里到处命中的平凡模式）也要能同步；透明色标记透传；**把 CSP 关掉再开，能自动重连**；写入不落地时跑 `python tools/diag_fg_write.py` 定位（打印 mirror-only / located / derived），设置页「复制诊断信息」里的 `copy_locate_state` 是同一个判据；久跑用 `python tools/stability_test.py`、`python tools/e2e_bidirectional.py` |
| B9 | `core/csp_companion_sync.py` | `pytest tests/test_companion_protocol.py tests/test_memory_sync_companion_active_echo.py -q` | CSP「连接智能手机」二维码自动扫描；扫描失败时手动粘贴 URL 也能连；断开后重连；`python tools/verify_companion_bridge.py` |
| B10 | `core/photoshop_color_sync.py` `core/photoshop_instances.py` `core/photoshop_script_bridge.py` `core/photoshop_bridge.jsx` | `pytest tests/test_photoshop_color_sync.py tests/test_photoshop_instances.py tests/test_photoshop_script_bridge.py -q`（49 项） | 注册版 PS（COM 路径）+ 绿色便携版（CEP 脚本桥）各测一次；同时开两个 PS 实例能选对；前景/背景双槽独立读写 |
| B11 | `core/sai2_brush_link.py` `core/udm_brush_link.py` | ⚠️ **无自动化覆盖** | 全靠手测：SAI2 / UDM 里改笔刷色 → Colorink 跟上；软件重启后重连 |
| B12 | `core/global_hotkeys.py` `ui/hotkey_button.py` | ⚠️ **无自动化覆盖** | 键盘键、鼠标侧键、中键、数位板笔键各绑一次；**绑鼠标键后在 CSP 里画一笔，点击不能被吞**；绑一个不支持的键，原绑定不能被清空；重复绑定应被拒绝 |
| B13 | `core/config.py` | `pytest tests/test_config_schema.py tests/test_release_contract.py -q` | 把 `%APPDATA%\Colorink` 改名模拟首启，默认值对（Ctrl+Alt+Q / Ctrl+Alt+Y / HSV / 8×2 历史）；旧配置升级不丢已有值；改设置后立刻任务管理器强杀，重开配置没损坏 |
| B14 | `ui/settings_window.py` `ui/settings/` `ui/settings_sidebar.py` | `pytest tests/test_settings_window.py tests/test_settings_window_crash_regression.py -q`（45 项） | 6 页（快捷键/界面/取色器/滤镜/同步/关于）逐页点开不崩；改过的那几项当场生效；「复制诊断信息」内容完整 |
| B15 | `core/i18n.py` + 任何加了新文案的地方 | `pytest tests/test_i18n.py -q` | 语言切到 English 逐页扫一遍，**新加的文案不能是中文写死或空白**；再切回中文 |
| B16 | `core/updater.py` `ui/settings/update_panel.py` | `pytest tests/test_updater.py tests/test_updater_self_replace.py -q`（31 项） | 平时只跑自动化；真机下载/自替换放到 **C4** 发版时做 |
| B17 | `core/crash_report.py` `core/diagnostics.py` | `pytest tests/test_crash_report.py tests/test_diagnostics.py -q` | 故意制造一次异常，`stderr.log` 有可定位的栈；诊断信息含版本 / 同步状态 / 最近日志 |
| B18 | `Colorink.spec` `Colorink Onefile.spec` `build_pyqt.py` `icons/` | `pytest tests/test_packaging_contract.py tests/test_release_contract.py -q` | **必须真打包一次**（见 C2 + C3）；新增图标/新增二进制资源两个 spec 都要加，测试会拦但打包后要肉眼确认图标真的显示 |
| B19 | 托盘 `ui/window/tray_mixin.py`、开机启动 `core/autostart.py` | ⚠️ **无自动化覆盖** | 托盘单击/双击/右键菜单各一次（历史上右键崩过）；开机启动开→重启电脑确认真的起来→关掉确认注册项清掉 |

---

## 4. C 档 · 发版前（打 tag 之前，逐条不许跳）

### C1 版本三件套（改这三处，必须一致）
- [ ] `core/updater.py` 的 `APP_VERSION`
- [ ] `file_version_info.txt`（`filevers` / `prodvers` / `FileVersion` / `ProductVersion`，都是 `x.y.z.0`）
- [ ] `release_notes.md` 首行是 `## vx.y.z`
- [ ] `python -m pytest tests/test_release_contract.py -q` 通过（这三处不一致会直接红）
- [ ] `README.md` 里的「当前版本」也改了

### C2 打包
- [ ] `python build_pyqt.py` → 两个都 OK（先跑版本一致性自检，漂了直接失败）
- [ ] 产物在：`dist/Onefile/Colorink.exe`、`dist/Onedir/Colorink/`
- [ ] 体积没异常暴涨（对比上一版）

### C3 打包产物冒烟（**源码跑不出来的坑全在这里**）
用 `dist/Onefile/Colorink.exe`，最好先把 `%APPDATA%\Colorink` 改名，模拟新用户首启：
- [ ] 双击能起，无控制台报错，无缺 DLL 弹窗
- [ ] 窗口图标 + 托盘图标都显示（PyInstaller 漏图标是老回归）
- [ ] `Ctrl+Alt+Q` 取色正常
- [ ] `Ctrl+Alt+D` 灰度滤镜正常（Native 后端；打包版才会暴露 `.pyc` 运行时缺失）
- [ ] CSP 同步能连（打包版才会暴露少了 `picker_hook.dll` / 运行时资源）
- [ ] CSP Companion 二维码扫描（`pyzbar` / `Pillow` / `mss` 只在打包版才可能缺）
- [ ] 设置 → 关于 → 检查更新，能连上 GitHub
- [ ] 单实例锁：再双击一次，不会开出第二个窗口
- [ ] 切成 English 扫一眼没乱码
- [ ] 同样步骤用 `dist/Onedir/Colorink/Colorink.exe` 快过一遍

### C4 更新链路（真机，每次发版都要）
- [ ] 装上**上一版** EXE → 检查更新 → 能看到新版本和更新说明
- [ ] 下载（先落 `.part`）→ 校验字节数 + SHA-256 → 原子替换 → 自动重启 → 关于页版本号变成新版
- [ ] 「跳过此版本」点一次，之后不再提示该版本
- [ ] onedir 安装形态下更新，挑到的是 `Colorink-Onedir.zip`，**不是** GitHub 自动生成的源码 zip

### C5 Release 资产
- [ ] 上传名字对：`Colorink.exe`（onefile）+ `Colorink-Onedir.zip`（onedir）
      —— `find_installer_asset()` 按名字挑资产，改名会让老版本更新失败
- [ ] Release 说明与 `release_notes.md` 本版段落一致

### C6 文档
- [ ] `README.md` 功能列表与实际一致（新功能加了、删掉的功能去掉）
- [ ] 动了架构 → `DESIGN.md`；动了色彩管道 → `OKLCH_CONVERSION_PIPELINE.md`

---

## 5. 已知盲区（这些地方自动化管不到，改了必须手测）

| 区域 | 状态 |
|---|---|
| 全局热键注册 / 鼠标键不吞点击 | ❌ 无测试（B12） |
| SAI2 / UDM 同步 | ❌ 无测试（B11） |
| 托盘菜单、开机启动 | ❌ 无测试（B19） |
| Native 灰度真实 GPU / DXGI 捕获路径 | ⚠️ 只测了逻辑契约，真机效果得看 |
| 多显示器 / 混合 DPI 下的取色与覆盖层 | ❌ 无测试（B5 / B6 手测） |
| 打包产物本身 | ❌ 无测试（C3 手工冒烟） |
| PS COM / CSP 进程内存的真实读写 | ⚠️ 单测全是 mock，真机必测（B8 / B10） |

---

## 6. 想以后省事，值得一次性补的（可选）

1. 加 `pytest.ini`：`testpaths = tests`，顺手把 `tools/*_test.py` 排除，避免以后误采集活体脚本
2. 给 `core/global_hotkeys.py`、`core/autostart.py`、`ui/window/tray_mixin.py` 补单测 —— 现在是 0 覆盖，却是崩溃高发区
3. 打包后自动冒烟脚本：启动 EXE 3 秒 → 检查进程存活 + `stderr.log` 为空 + EXE 版本资源等于 `APP_VERSION`，把 C3 的一半自动化掉
4. GitHub Actions 用 `windows-latest` 跑 `pytest`，避免"本地忘了跑"
