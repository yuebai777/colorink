"""Hover-tooltip catalog for the settings sidebar.

This module centralizes the Chinese source strings for settings controls
that previously had no tooltip, so the main widget wire-up stays readable and
every tooltip goes through ``i18n.tr`` (English catalog lives in core/i18n.py).

``apply_settings_tooltips(sidebar)`` is called at the end of
``SettingsSidebar.init_ui`` — the same place that rebuilds the UI on
language change — so tooltips are re-applied on every retranslate.
"""

from core import i18n

_SETTINGS_TOOLTIPS: dict[str, str] = {
    # ── Hotkeys page ────────────────────────────────────────────────────
    "btn_pick": "设置全局取色快捷键；按快捷键后在屏幕任意位置取色",
    "btn_hide": "设置显示/隐藏悬浮面板的快捷键",
    "btn_follow": "设置「跟随鼠标」开关快捷键（需配合右侧启用勾选）",
    "cb_follow_mouse": "启用后按跟随鼠标快捷键时，窗口会移动到鼠标位置",
    "btn_grayscale": "设置开关全屏灰度滤镜的快捷键",
    # ── Interface page ──────────────────────────────────────────────────
    "combo_theme": "选择界面主题：自动匹配绘画软件、屏幕取色或固定灰/白/黑",
    "combo_slider_style": "选择色彩滑块的外观样式",
    "combo_border_style": "选择窗口边框、分组框与数值框的外观样式；「跟随滑块样式」会自动匹配当前滑块主题",
    "btn_font_dec": "减小界面字体大小",
    "btn_font_inc": "增大界面字体大小",
    "zoom_slider": "拖动调整整个窗口的缩放比例（松开后生效）",
    "opacity_slider": "拖动调整窗口背景与边框的不透明度（0% = 完全透明，"
                      "100% = 完全不透明），拖动时实时预览；0% 时窗口仍可拖动、"
                      "点击不会穿透到画布；滑块、色轮与色块始终保持不透明",
    "cb_taskbar_icon": "在任务栏显示 Colorink 图标；关闭后只保留托盘图标",
    "cb_lock_size": "锁定后窗口不能手动缩放",
    # ── Picker page ─────────────────────────────────────────────────────
    "btn_zoom_dec": "减小取色放大镜倍率",
    "btn_zoom_inc": "增大取色放大镜倍率",
    "combo_pos": "选择前景/背景色块在窗口中的显示位置",
    "combo_module": "切换色轮使用的色彩空间模块（HSV / HLS / RGB / LCH）",
    "cb_history": "显示/隐藏颜色历史区域",
    "combo_history_cols": "颜色历史网格的列数",
    "combo_history_rows": "颜色历史网格的行数",
    "combo_viz_mode": "切换 LAB 视图的色彩空间：LAB 或 OKLab（更接近人眼感知）",
    "combo_lab_shape": "LAB 色彩平面的显示形状：方形或圆形",
    "combo_lab_harmony": "选择 LAB 视图的调和模式（互补 / 近似 / 三等分等）",
    "cb_show_lab_lightness": "显示/隐藏 LAB 区域的亮度滑块",
    "cb_flip_wheel": "水平镜像色环（颜色排列左右翻转）",
    "btn_scroll_dec": "减小滑块滚轮步长（每次滚动改变的值）",
    "btn_scroll_inc": "增大滑块滚轮步长（每次滚动改变的值）",
    "btn_same_dec": "减小同一色彩空间内滑块之间的间距",
    "btn_same_inc": "增大同一色彩空间内滑块之间的间距",
    "btn_diff_dec": "减小不同色彩空间滑块之间的间距",
    "btn_diff_inc": "增大不同色彩空间滑块之间的间距",
    # ── Sync page ────────────────────────────────────────────────────────
    "combo_software": "选择颜色同步到哪个绘画软件（支持自动识别前台软件 / CSP / SAI2 / UDM / Photoshop / 手机 Companion）",
    "combo_udm": "选择 UDM 版本；自动检测失败时可手动指定",
    "combo_ps": "选择 Photoshop 版本；自动检测失败时可手动指定",
    "btn_companion_reconnect": "重新连接手机 Companion 模式；未保存会话时按钮会改为「连接智能手机」",
    "btn_companion_disconnect": "断开当前手机 Companion 连接",
    "btn_ps_bridge_recheck": "重新检测 Photoshop 实例和同步桥状态",
    "btn_ps_bridge_restart": "确认并重启 Photoshop，让已部署的同步桥生效",
    # ── About page ───────────────────────────────────────────────────────
    "btn_check_update": "检查 GitHub 上是否有新版本",
    "btn_about_author": "查看作者信息和项目主页",
    "cb_check_updates": "启动后自动在后台检查更新（结果通过托盘通知显示）",
}

# Slider-show checkboxes are built in a loop and stored in ``slider_rows``,
# so they are not reachable by a single attribute name in the catalog above.
SLIDER_SHOW_TIPS: dict[str, str] = {
    "RGB": "显示/隐藏 RGB 滑块",
    "HSV": "显示/隐藏 HSV 滑块",
    "HSL": "显示/隐藏 HLS 滑块",
    "LAB": "显示/隐藏 LAB 滑块",
    "OKLab": "显示/隐藏 OKLab 滑块",
    "OKLCh": "显示/隐藏 OKLCh 滑块",
}


def apply_settings_tooltips(sidebar) -> None:
    """Apply the static tooltip catalog to the settings sidebar.

    Only touches attributes listed in ``_SETTINGS_TOOLTIPS``; existing
    (often dynamic) tooltips such as combo item tips or status labels are
    left untouched.
    """
    for attr, tip in _SETTINGS_TOOLTIPS.items():
        widget = getattr(sidebar, attr, None)
        if widget is None:
            continue
        try:
            widget.setToolTip(i18n.tr(tip))
        except Exception:
            pass
