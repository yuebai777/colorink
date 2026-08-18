"""Tiny source-as-key translation layer for Colorink.

The Chinese UI string is itself the translation key; ``tr()`` returns the
English equivalent when the active language is English, and otherwise returns
the source text unchanged. This means untranslated strings never blank out —
they simply fall back to Chinese — and there is only one catalog to maintain
(the ``_EN`` dict).

Avoids Qt's QTranslator (which needs compiled .qm files) in favor of a plain
dict, but keeps the same live-switch pattern: call :func:`set_language` and the
registered listeners re-apply their text.
"""

import locale

LANG_ZH = "zh"
LANG_EN = "en"
DEFAULT_LANGUAGE = LANG_ZH

_current_language: str | None = None
_listeners: list = []

# English catalog: Chinese source text → English translation.
_EN: dict[str, str] = {
    # ── Navigation / sections ──
    "快捷键": "Hotkeys",
    "界面": "Interface",
    "取色器": "Picker",
    "软件": "Software",
    "关于": "About",
    # ── Cards ──
    "全局热键": "Global hotkeys",
    "外观": "Appearance",
    "灰度滤镜": "Grayscale filter",
    "行为": "Behavior",
    "滑块显示与顺序": "Sliders & order",
    "颜色历史": "Color history",
    "色环与 LAB": "Wheel & LAB",
    "高级": "Advanced",
    "同步与版本": "Sync & version",
    "复制诊断信息": "Copy diagnostics",
    "把版本、同步状态与最近日志复制到剪贴板，"
    "用于排查同步 / 崩溃问题": "Copy version, sync state and recent logs to "
    "the clipboard for troubleshooting sync / crash issues",
    "已复制 ✓": "Copied ✓",
    "配置管理": "Config management",
    # ── Title-bar / tray ──
    "跟随鼠标": "Follow mouse",
    "无焦点选色模式": "No-focus pick mode",
    "打开设置": "Open settings",
    "显示/隐藏": "Show / hide",
    "显示标题栏": "Show title bar",
    "退出": "Quit",
    "设置": "Settings",
    # ── Hotkeys ──
    "全局取色": "Pick color",
    "隐藏界面": "Hide window",
    "随鼠标移动": "Follow mouse",
    "启用": "Enable",
    "黑白滤镜": "Grayscale filter",
    "LAB切换(色轮)": "Toggle LAB (wheel)",
    "LAB切换(全局)": "Toggle LAB (global)",
    "标题栏显隐": "Toggle title bar",
    # ── Appearance ──
    "背景主题": "Background theme",
    "背景 自动（匹配CSP）": "Auto (match CSP)",
    "背景 取色": "Eyedropper",
    "背景 灰": "Gray",
    "背景 白": "White",
    "背景 黑": "Black",
    "滑条样式": "Slider style",
    "字体大小": "Font size",
    "界面缩放": "UI scale",
    "框色": "Frame color",
    "底色": "Canvas color",
    "滤镜目标屏幕": "Target screen",
    "黑白模式": "Grayscale mode",
    "OKLCh (感知均匀)": "OKLCh (perceptual)",
    "Luma (BT.709 标准)": "Luma (BT.709)",
    "渲染后端 (高级)": "Render backend (advanced)",
    "OKLCh (GPU兼容)": "OKLCh (GPU-compatible)",
    "系统 Luma (Mag)": "System Luma (Mag)",
    "任务栏图标": "Taskbar icon",
    "固定窗口大小": "Lock window size",
    "锁定窗口位置": "Lock window position",
    "开机自启动": "Start on login",
    "仅在画图软件前台时显示": "Show only in drawing app foreground",
    # ── Picker ──
    "取色放大倍率": "Picker zoom",
    "前背景色位置": "Preview position",
    "左上角": "Top-left",
    "左下角": "Bottom-left",
    "色彩空间模块": "Color space module",
    "显示模块切换按钮": "Show module switch button",
    "显示LAB切换按钮": "Show LAB toggle button",
    "RGB 滑条": "RGB sliders",
    "HSV 滑条": "HSV sliders",
    "HLS 滑条": "HLS sliders",
    "LAB 滑条": "LAB sliders",
    "OKLab 滑条": "OKLab sliders",
    "OKLCh 滑条": "OKLCh sliders",
    "上移": "Move up",
    "下移": "Move down",
    "在滑块顺序中上移": "Move up in slider order",
    "在滑块顺序中下移": "Move down in slider order",
    "显示颜色历史": "Show color history",
    "历史列数": "Columns",
    "历史行数": "Rows",
    "LAB图模式": "LAB view mode",
    "LAB 色彩空间": "LAB color space",
    "OKLab 色彩空间": "OKLab color space",
    "显示 LAB 亮度滑条": "Show LAB lightness slider",
    "水平翻转色环": "Flip wheel horizontally",
    "滚轮单次步长": "Scroll step",
    "同空间滑条间距": "Same-space spacing",
    "不同空间间距": "Different-space spacing",
    # ── Software ──
    "同步软件": "Sync software",
    "CSP 智能手机 (R)": "CSP Smartphone (R)",
    "未连接": "Not connected",
    "重新连接": "Reconnect",
    "断开": "Disconnect",
    "连接智能手机": "Connect smartphone",
    "CSP 版本": "CSP version",
    "SAI2 版本": "SAI2 version",
    "UDM 版本": "UDM version",
    "PS 版本": "PS version",
    "重新检测": "Re-detect",
    "重启 Photoshop": "Restart Photoshop",
    "未设定": "Not set",
    "设定": "Set",
    "同步": "Sync",
    "未绑定": "Unbound",
    # ── CSP version options ──
    "auto（自动检测）": "auto (detect)",
    "CSP 4.x（仅主色）": "CSP 4.x (main only)",
    "CSP 5.0（仅主色）": "CSP 5.0 (main only)",
    "CSP 5.1（支持前景/背景/透明）": "CSP 5.1 (fg/bg/transparent)",
    # ── About / update ──
    "当前版本": "Current version",
    "检查更新": "Check for updates",
    "关于作者": "About the author",
    "导出配置": "Export settings",
    "导入配置": "Import settings",
    "恢复默认": "Reset to defaults",
    "启动时自动检查更新": "Check for updates on startup",
    "检查中...": "Checking...",
    "已是最新版本": "Up to date",
    "发现新版本": "New version available",
    "下载到本地": "Download locally",
    "下载更新 ({flavor})": "Download update ({flavor})",
    "下载 {flavor} 版（切换）": "Download {flavor} version (switch)",
    "前往下载": "Open download page",
    "稍后": "Later",
    "下载中": "Downloading",
    "下载失败": "Download failed",
    "下载完成": "Download complete",
    "打开所在文件夹": "Open folder",
    "立即运行": "Run now",
    "关闭": "Close",
    "Colorink 更新": "Colorink update",
    "发现新版本 {latest}，点击查看下载": "New version {latest} available — click to view",
    # ── Companion / sync status ──
    "● 已连接": "● Connected",
    "○ 已保存 — 等待 CSP...": "○ Saved — waiting for CSP...",
    "○ 未设置": "○ Not set",
    "手机": "Phone",
    "{name} 已连接": "{name} connected",
    "{name} 未连接": "{name} not connected",
    "当前同步：{name} {version}": "Syncing: {name} {version}",
    # ── Theme status ──
    "自动匹配画图软件主题": "Auto: match drawing app theme",
    "自动匹配：{dark}主题": "Auto: {dark} theme",
    "取色主题：从屏幕两个位置取色": "Eyedropper: pick from two screen points",
    "固定主题：{name}": "Fixed theme: {name}",
    "深色": "Dark",
    "浅色": "Light",
    "黑": "Black",
    "白": "White",
    "灰": "Gray",
    # ── CSP ability / tooltips ──
    "CSP 5.1 内存模式支持前景/背景色与透明状态同步。": "CSP 5.1 memory mode supports foreground/background and transparent sync.",
    "CSP 5.0 内存模式仅支持主色同步；前景/背景色与透明状态同步需要 CSP 5.1。": "CSP 5.0 memory mode only syncs the main color; foreground/background and transparent sync need CSP 5.1.",
    "CSP 4.x 内存模式仅支持主色同步；前景/背景色与透明状态同步需要 CSP 5.1。": "CSP 4.x memory mode only syncs the main color; foreground/background and transparent sync need CSP 5.1.",
    "自动检测 CSP 版本：检测为 5.1 时支持前景/背景色与透明状态同步，5.0 及以下仅主色同步。": "Auto-detect CSP version: 5.1 supports foreground/background and transparent sync; 5.0 and below only sync the main color.",
    "自动检测 CSP 主版本；检测为 5.1 时支持前景/背景色与透明同步，5.0 及以下仅主色同步。": "Auto-detect the CSP major version; 5.1 supports foreground/background and transparent sync, 5.0 and below only sync the main color.",
    "CSP 5.1 内存模式支持前景/背景色与透明状态同步（推荐）。": "CSP 5.1 memory mode supports foreground/background and transparent sync (recommended).",
    "前景/背景色与透明状态同步（内存模式）仅 CSP 5.1 支持；自动检测失败时才需要手动指定版本": "Foreground/background and transparent sync (memory mode) requires CSP 5.1; specify the version manually only if auto-detection fails",
    "2024 年后的 SAI2 版本地址偏移不同，自动检测失败时可手动指定": "SAI2 builds after 2024 use different address offsets; specify manually if auto-detection fails",
    # ── Misc tooltips ──
    "把当前设置保存为 JSON 文件": "Save current settings as a JSON file",
    "从 JSON 文件恢复设置": "Restore settings from a JSON file",
    "恢复全部设置为出厂默认值": "Reset all settings to defaults",
    "隐藏后顶部边框与四周一致；可通过快捷键或托盘菜单恢复": "Hides the top border to match the other edges; restore via hotkey or tray menu",
    "开启后不能拖动窗口": "The window cannot be dragged while enabled",
    "开机后自动以管理员权限启动（免 UAC 弹窗）": "Start with admin rights on login (no UAC prompt)",
    "画图软件不在前台时自动隐藏悬浮面板": "Auto-hide the floating panel when the drawing app is not in the foreground",
    "开启后不会抢占画图软件的键盘焦点，适合边画边选色": "Does not steal keyboard focus from the drawing app — ideal for picking while drawing",
    "在色环区域显示色彩空间模块切换按钮": "Show the color space module switch button on the wheel",
    "在色轮/LAB区域显示色轮与LAB之间的切换按钮": "Show the wheel/LAB toggle button in the wheel/LAB area",
    "选择黑白滤镜作用在哪个屏幕，默认作用于全部屏幕": "Choose which screen the grayscale filter applies to (default: all screens)",
    "OKLCh 更接近人眼感知；Luma 是标准亮度转换": "OKLCh is closer to human perception; Luma is the standard luminance conversion",
    "绘画软件标题栏/边框的深色": "Dark color of the drawing app title bar/border",
    "绘画软件画布区域的浅色": "Light color of the drawing app canvas area",
    "从已设定的取色点立即同步颜色": "Sync colors immediately from the configured pick point",
    # ── Dialogs ──
    "配置已导出到：\n{path}": "Settings exported to:\n{path}",
    "导出失败：{e}": "Export failed: {e}",
    "配置文件格式不正确": "Invalid settings file format",
    "读取失败：{e}": "Read failed: {e}",
    "配置已导入并生效。": "Settings imported and applied.",
    "确定要恢复所有设置为默认值吗？": "Reset all settings to defaults?",
    "设置已恢复为默认值。": "Settings reset to defaults.",
    "JSON 文件 (*.json)": "JSON file (*.json)",
    "程序 (*.exe)": "Program (*.exe)",
    "更新包 (*.exe *.zip)": "Update package (*.exe *.zip)",
    "已下载到:\n{path}": "Downloaded to:\n{path}",
    "这是 onefile 版。请先退出当前 Colorink，再运行该文件以切换。": "This is the onefile version. Quit Colorink first, then run this file to switch.",
    "这是 onedir 版。请先退出当前 Colorink，解压 zip 后运行其中的 Colorink.exe 以切换。": "This is the onedir version. Quit Colorink first, extract the zip, then run Colorink.exe inside it to switch.",
    "保存安装包": "Save installer",
    "未检测到运行中的 Photoshop 进程": "No running Photoshop process detected",
    "将关闭并重新启动 Photoshop：\n{path}\n\n未保存的更改可能会丢失，是否继续？": "Photoshop will be closed and restarted:\n{path}\n\nUnsaved changes may be lost. Continue?",
    "启动失败：{e}": "Failed to start: {e}",
    "绿色版 Photoshop": "Portable Photoshop",
    "检测到绿色版（便携版）Photoshop：它未注册 COM 自动化接口，无法直接同步颜色。\n\nColorink 已自动部署同步脚本（脚本桥），重启 Photoshop 后即可同步前景 / 背景色。\n是否现在重启 Photoshop？": "Detected a portable Photoshop: it does not register a COM automation interface, so colors cannot sync directly.\n\nColorink deployed a script bridge automatically; restart Photoshop to sync foreground/background colors.\nRestart Photoshop now?",
    "已连接（脚本桥），但 Photoshop 内运行的仍是旧版同步面板：拖动颜色可能跳动。请重启 Photoshop 一次后点击右侧按钮。": "Connected (script bridge), but Photoshop is still running an old sync panel; colors may jump while dragging. Restart Photoshop once, then click the button.",
    "绿色版 Photoshop 已连接（脚本桥）：前景 / 背景色双槽同步已启用。": "Portable Photoshop connected (script bridge): foreground/background dual-slot sync enabled.",
    "检测到绿色版 Photoshop：已自动部署同步脚本，重启 Photoshop（绿色版）后生效；之后在 PS 中有操作时颜色即会同步。": "Portable Photoshop detected: the sync script was deployed automatically and takes effect after restarting Photoshop; colors will sync on subsequent actions in PS.",
    "切换界面语言": "Switch the UI language",
    "更新内容:": "What's new:",
    "可一键下载安装包，或前往 GitHub 页面。": "Download the installer directly, or open the GitHub page.",
    # ── Crash prompt ──
    "检测到 Colorink 上次运行发生异常。": "Colorink did not close cleanly last time.",
    "已保存错误日志，可复制或打开查看：": "An error log was saved — you can copy or open it:",
    "复制错误信息": "Copy error details",
    "打开日志文件": "Open log file",
    # ── Slider themes / hotkey capture / preview context menu ──
    "默认": "Default",
    "类 CSP": "CSP-like",
    "类 SAI": "SAI-like",
    "类 PS": "PS-like",
    "未绑定": "Unbound",
    "请按键盘或鼠标键...": "Press a key or mouse button...",
    "请按键盘...": "Press a key...",
    "复制 RGB": "Copy RGB",
    "复制 HEX": "Copy HEX",
    "复制 HSL": "Copy HSL",
    "复制 OKLCh": "Copy OKLCh",
    "复制 LAB": "Copy LAB",
    "更新并重启": "Update & restart",
    "系统 Luma (Mag) 作用于全部屏幕": "System Luma (Mag) applies to all screens",
    "点击后窗口隐藏3秒，移鼠标到目标位置": "Hides the window for 3 seconds, then move the mouse to the target",
    # Long tooltips
    "鼠标悬停在色轮或LAB区域时，按此键/鼠标键切换色轮/LAB视图；支持键盘、鼠标按键或数位板笔按键（建议侧键/中键，左键会与色轮操作冲突）；无需聚焦本窗口，无焦点选色模式下也可用": "While hovering the wheel/LAB area, press this key/mouse button to switch the wheel/LAB view. Supports keyboard, mouse buttons and pen buttons (side/middle button recommended — left click conflicts with the wheel). No focus required; also works in no-focus pick mode.",
    "任意位置全局切换色轮/LAB视图，无需聚焦本窗口；支持键盘或鼠标按键（鼠标按键作为全局快捷键时不拦截点击，画画软件仍会收到）": "Toggle the wheel/LAB view globally from anywhere, no focus required. Supports keyboard or mouse buttons (mouse hotkeys are not suppressed, so the drawing app still receives the click).",
    "显示或隐藏标题栏（设置/最小化/关闭按钮那一栏）；隐藏后顶部边框与四周一致": "Show or hide the title bar (settings/minimize/close buttons). When hidden the top border matches the other edges.",
    "OKLCh (GPU兼容)：感知均匀的全屏黑白，覆盖 ColorInk；系统 Luma (Mag)：延迟最低、仅作用于全部屏幕的备用模式；需要按屏目标时请在 Native 后端选择 Luma。": "OKLCh (GPU-compatible): perceptual full-screen grayscale covering Colorink. System Luma (Mag): lowest latency fallback that only applies to all screens. Use the Native backend's Luma mode for per-screen targeting.",
    # ── Update errors (translated at the UI boundary via {detail}) ──
    "跳过此版本": "Skip this version",
    "GitHub 返回 HTTP {detail}，请稍后重试": "GitHub returned HTTP {detail}; try again later",
    "网络异常：{detail}": "Network error: {detail}",
    "获取更新失败：{detail}": "Update check failed: {detail}",
    "下载失败：{detail}": "Download failed: {detail}",
    "下载不完整：{detail}": "Incomplete download: {detail}",
    "保存失败：{detail}": "Save failed: {detail}",
    "校验失败：下载文件与发布校验和不一致": "Checksum mismatch: the downloaded file does not match the published checksum",
    "GitHub API 限流 (403)。未认证请求每小时仅 60 次，可稍后重试或设置 COLORINK_GITHUB_TOKEN 提升配额。": "GitHub API rate limit (403). Unauthenticated requests are capped at 60/hour; retry later or set COLORINK_GITHUB_TOKEN.",
    "未在 GitHub 上找到发布信息 (404)": "No release found on GitHub (404)",
    "GitHub 响应解析失败": "Failed to parse the GitHub response",
    "未在响应中找到版本号": "No version tag found in the response",
}


def _detect_language() -> str:
    """Detect a catalog language from the system locale (auto mode).

    Uses ``locale.getlocale()`` after an explicit ``setlocale`` so we do not
    depend on the deprecated ``getdefaultlocale()``.
    """
    try:
        locale.setlocale(locale.LC_ALL, "")
        lang, _enc = locale.getlocale()
    except Exception:
        lang = None
    if lang and lang.lower().startswith("en"):
        return LANG_EN
    return LANG_ZH


def resolve_language(config_value: str | None) -> str:
    """Map a config ``language`` value to a catalog code.

    Accepts ``"zh"`` / ``"en"`` directly; anything else (including ``"auto"``
    or ``None``) falls back to system-locale detection.
    """
    if config_value in (LANG_ZH, LANG_EN):
        return config_value
    return _detect_language()


def set_language(lang: str) -> None:
    """Set the active language and notify listeners on change."""
    global _current_language
    lang = lang if lang in (LANG_ZH, LANG_EN) else DEFAULT_LANGUAGE
    if _current_language == lang:
        return
    _current_language = lang
    for callback in list(_listeners):
        try:
            callback()
        except Exception:
            pass


def get_language() -> str:
    """Return the active language code, resolving auto on first use."""
    global _current_language
    if _current_language is None:
        _current_language = _detect_language()
    return _current_language


def add_language_listener(callback) -> None:
    """Register a zero-arg callback invoked after the language changes."""
    if callback not in _listeners:
        _listeners.append(callback)


def tr(text: str, **kwargs) -> str:
    """Translate *text* into the active language.

    Chinese source text is the key; when the active language is English and a
    translation exists, it is returned, otherwise the source text is returned
    unchanged. ``{name}`` placeholders are formatted from ``kwargs`` (missing
    keys are left untouched rather than raising).
    """
    if get_language() == LANG_EN:
        result = _EN.get(text, text)
    else:
        result = text
    if kwargs:
        try:
            return result.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return result
    return result
