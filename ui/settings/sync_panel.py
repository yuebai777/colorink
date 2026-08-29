"""Software-sync status and version controls for the settings sidebar.

Extracted from ``ui.settings_sidebar``: CSP/SAI/UDM/PS/companion version rows,
connection-status labels, diagnostics copy and the green-Photoshop bridge UI.
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import i18n
from ui.settings.settings_helpers import (
    _CSP_VERSION_ITEMS,
    _CSP_VERSION_TIPS,
    _SAI_REFRESH_ITEMS,
    _SAI_REFRESH_TIPS,
    NonScrollComboBox,
)


class SyncPanelMixin:

    def _on_csp_version_changed(self, _text: str) -> None:
        """CSP 版本选择变化：保存配置并刷新能力提示。"""
        self.save_settings()
        self._refresh_csp_version_hint()

    def _refresh_csp_version_hint(self):
        """按所选 CSP 版本显示同步能力说明（5.1 内存同步已移除）。"""
        if not hasattr(self, "lbl_csp_hint"):
            return
        val = self.combo_csp.currentData() or "auto"
        if val == "csp5.x":
            text = i18n.tr("CSP 5.0 内存模式仅支持主色同步；CSP 5.1 不再内存同步，请改用手机（Companion）模式。")
        elif val == "csp4.x":
            text = i18n.tr("CSP 4.x 内存模式仅支持主色同步；CSP 5.1 不再内存同步，请改用手机（Companion）模式。")
        else:
            text = i18n.tr("自动检测 CSP 版本：内存模式仅 4.x/5.0 主色同步；检测为 5.1 时不再内存同步，请改用手机（Companion）模式。")
        self.lbl_csp_hint.setText(text)

    def update_version_visibility(self):
        selected = self.combo_software.currentData() or "csp"
        self.row_csp_widget.setVisible(selected == "csp")
        self.row_csp_hint_widget.setVisible(selected == "csp")
        self.row_sai_widget.setVisible(selected == "sai")
        self.row_sai_refresh_widget.setVisible(selected == "sai")
        self.row_udm_widget.setVisible(selected == "udm")
        self.row_ps_widget.setVisible(selected == "ps")
        self.row_companion_widget.setVisible(selected == "companion")
        self._refresh_csp_mode_tip(selected)
        if selected == "companion":
            self._refresh_companion_status()
        if selected == "csp":
            self._refresh_csp_version_hint()
        self._refresh_sync_status()
        self._refresh_ps_bridge_status()

    def _refresh_csp_mode_tip(self, selected):
        """标注 CSP 各连接模式的问题与推荐（内存同步 vs 手机 Companion）。"""
        if not hasattr(self, "lbl_csp_mode_tip"):
            return
        if selected in ("csp", "companion"):
            self.lbl_csp_mode_tip.setText(i18n.tr(
                "CSP 内存同步仅 4.x/5.0 主色，依赖内存扫描、易随 CSP 更新失效；"
                "CSP 5.1 已移除内存同步。手机（Companion）模式连接稳定、"
                "支持前景/背景与透明，推荐使用。"))
            self.row_csp_mode_tip_widget.show()
        else:
            self.row_csp_mode_tip_widget.hide()

    def _refresh_ps_instances(self):
        """Populate the Photoshop version combo with detected running instances:
        registered installs (COM) + green/portable editions (script bridge)."""
        try:
            from core.photoshop_instances import detect_instances
            instances = detect_instances()
        except Exception:
            instances = []
        labels = ["auto"] + [inst.label for inst in instances]
        current = self.combo_ps.currentText()
        self.combo_ps.blockSignals(True)
        self.combo_ps.clear()
        self.combo_ps.addItems(labels)
        self.combo_ps.setCurrentText(current if current in labels else "auto")
        self.combo_ps.blockSignals(False)

    def _refresh_sync_status(self):
        if not hasattr(self, "lbl_sync_status"):
            return
        selected = self.combo_software.currentData() or "csp"
        software_names = {
            "csp": "CSP",
            "sai": "SAI2",
            "udm": "UDM",
            "ps": "Photoshop",
            "companion": i18n.tr("手机"),
        }
        name = software_names.get(selected, selected)
        connected = None
        if self._parent is not None:
            status = getattr(self._parent, "_sync_status", None)
            if status and len(status) == 2 and status[0] == self.cfg.get("syncSoftware"):
                connected = status[1]
        if connected is True:
            self.lbl_sync_status.setText(i18n.tr("{name} 已连接", name=name))
            self._set_label_state(self.lbl_sync_status, "success")
        elif connected is False:
            text = i18n.tr("{name} 未连接", name=name)
            parent = self._parent
            sync_err = getattr(parent, "_sync_error", None) if parent is not None else None
            if sync_err and len(sync_err) >= 2 and sync_err[0] == self.cfg.get("syncSoftware"):
                err = sync_err[1]
                if err:
                    text += f" — {err}"
                    # Keep the label compact; full reason lives in the tooltip.
                    if len(text) > 90:
                        text = text[:90] + "…"
            self.lbl_sync_status.setText(text)
            self.lbl_sync_status.setToolTip(text)
            self._set_label_state(self.lbl_sync_status, "danger")
        else:
            mode = self.cfg.get("syncSoftware", "csp")
            version = {
                "csp": self.combo_csp.currentData() or "auto",
                "sai": self.combo_sai.currentText(),
                "udm": self.combo_udm.currentText(),
                "ps": self.combo_ps.currentText(),
                "companion": "",
            }.get(mode, "")
            self.lbl_sync_status.setText(i18n.tr("当前同步：{name} {version}", name=name, version=version).strip())
            self._set_label_state(self.lbl_sync_status, "muted")
        self._refresh_ps_bridge_status()

    def _on_copy_diagnostics(self):
        """Copy a diagnostics report to the clipboard (for bug reports)."""
        from core import diagnostics
        parent = self._parent
        report = diagnostics.collect_diagnostics(
            sync_thread=getattr(parent, "sync_thread", None) if parent is not None else None,
            cfg=self.cfg,
            mixin=parent,
        )
        QApplication.clipboard().setText(report)
        # Brief in-place confirmation, then restore the label.
        self.btn_copy_diagnostics.setText(i18n.tr("已复制 ✓"))
        QTimer.singleShot(1500, self._restore_copy_diagnostics_label)

    def _restore_copy_diagnostics_label(self):
        """Restore the button label; safe even if the sidebar was closed."""
        try:
            self.btn_copy_diagnostics.setText(i18n.tr("复制诊断信息"))
        except RuntimeError:
            pass  # widget already deleted (settings window closed)

    def _ps_sync(self):
        """Best-effort access to the PhotoshopSync instance (or None)."""
        parent = self._parent
        st = getattr(parent, "sync_thread", None)
        return getattr(st, "ps_sync", None) if st is not None else None

    def _refresh_ps_bridge_status(self):
        """Show / hide the green-edition notice row with the current
        script-bridge state (deployed-pending / alive / deploy failed)."""
        if not hasattr(self, "row_ps_bridge_widget"):
            return
        if self.combo_software.currentData() != "ps":
            self.row_ps_bridge_widget.hide()
            return
        ps_sync = self._ps_sync()
        if ps_sync is None:
            self.row_ps_bridge_widget.hide()
            return
        try:
            # UI thread: use the non-connecting snapshot — status() can
            # block on a flaky COM registration attempt.
            st = ps_sync.status_lite()
        except Exception:
            self.row_ps_bridge_widget.hide()
            return
        if st.get("backend") != "script-bridge":
            self.row_ps_bridge_widget.hide()
            return
        self.row_ps_bridge_widget.show()
        if st.get("bridgeAlive"):
            if st.get("panelStale"):
                self.lbl_ps_bridge_status.setText(
                    i18n.tr("已连接（脚本桥），但 Photoshop 内运行的仍是旧版同步面板："
                    "拖动颜色可能跳动。请重启 Photoshop 一次后点击右侧按钮。"))
                self._set_label_state(self.lbl_ps_bridge_status, "warning")
                self.btn_ps_bridge_restart.show()
            else:
                self.lbl_ps_bridge_status.setText(
                    i18n.tr("绿色版 Photoshop 已连接（脚本桥）：前景 / 背景色双槽同步已启用。"))
                self._set_label_state(self.lbl_ps_bridge_status, "success")
                self.btn_ps_bridge_restart.hide()
        else:
            self.lbl_ps_bridge_status.setText(
                i18n.tr("检测到绿色版 Photoshop：已自动部署同步脚本，"
                "重启 Photoshop（绿色版）后生效；"
                "之后在 PS 中有操作时颜色即会同步。"))
            self._set_label_state(self.lbl_ps_bridge_status, "warning")
            self.btn_ps_bridge_restart.show()

    def _on_ps_bridge_recheck(self):
        """Force instance re-detection after the user restarted Photoshop."""
        ps_sync = self._ps_sync()
        if ps_sync is not None:
            try:
                ps_sync.recheck()
            except Exception:
                pass
        self._refresh_ps_bridge_status()

    def _on_ps_restart(self):
        """Confirm, then restart the selected Photoshop instance so the
        deployed bridge script gets loaded."""
        ps_sync = self._ps_sync()
        if ps_sync is None:
            return
        from core.photoshop_instances import detect_instances, pick_target
        try:
            target = pick_target(detect_instances(), ps_sync.current_version)
        except Exception:
            target = None
        if target is None:
            QMessageBox.warning(self, i18n.tr("重启 Photoshop"),
                                i18n.tr("未检测到运行中的 Photoshop 进程"))
            return
        ret = QMessageBox.question(
            self, i18n.tr("重启 Photoshop"),
            i18n.tr("将关闭并重新启动 Photoshop：\n{path}\n\n未保存的更改可能会丢失，是否继续？", path=target.exe_path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        import psutil as _psutil
        import subprocess as _subprocess
        try:
            proc = _psutil.Process(target.pid)
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except _psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except (_psutil.NoSuchProcess, _psutil.AccessDenied):
            pass
        try:
            _subprocess.Popen([target.exe_path])
        except OSError as exc:
            QMessageBox.warning(self, i18n.tr("重启 Photoshop"), i18n.tr("启动失败：{e}", e=exc))
            return
        # The bridge script reports heartbeats a few seconds after startup.
        QTimer.singleShot(8000, self._refresh_ps_bridge_status)

    def _on_ps_bridge_remove(self):
        """Remove the deployed bridge extension from the green Photoshop.

        The panel is already loaded inside a running Photoshop, so a
        restart is still required for the engine to be fully released —
        this is the "退出插件后需要重启 PS 才恢复" step, made explicit.
        """
        ps_sync = self._ps_sync()
        if ps_sync is None:
            return
        try:
            ok = ps_sync.remove_bridge()
        except Exception:
            ok = False
        if not ok:
            QMessageBox.warning(
                self, i18n.tr("移除扩展"),
                i18n.tr("未能移除同步扩展（目录不可写？请以管理员身份运行）"))
            return
        QMessageBox.information(
            self, i18n.tr("移除扩展"),
            i18n.tr("已移除绿色版 Photoshop 的同步扩展。\n\n"
            "如果 Photoshop 正在运行，需要重启 Photoshop 后才会"
            "完全生效（不再占用其脚本引擎）。"))
        self._refresh_ps_bridge_status()

    def _on_software_changed(self, text):
        """When the user picks Photoshop, offer the green-edition fix once."""
        if text == "Photoshop":
            QTimer.singleShot(400, self._maybe_prompt_ps_bridge)

    def _maybe_prompt_ps_bridge(self):
        """One-time dialog: bridge deployed but Photoshop not restarted yet."""
        if self._ps_bridge_prompted:
            return
        if self.combo_software.currentText() != "Photoshop":
            return
        ps_sync = self._ps_sync()
        if ps_sync is None:
            return
        try:
            # Non-connecting snapshot: never block the UI on COM.
            st = ps_sync.status_lite()
        except Exception:
            return
        if st.get("backend") != "script-bridge":
            return
        self._ps_bridge_prompted = True
        if st.get("bridgeAlive"):
            return
        ret = QMessageBox.question(
            self, i18n.tr("绿色版 Photoshop"),
            i18n.tr("检测到绿色版（便携版）Photoshop：它未注册 COM 自动化接口，"
            "无法直接同步颜色。\n\n"
            "Colorink 已自动部署同步脚本（脚本桥），重启 Photoshop 后即可"
            "同步前景 / 背景色。\n是否现在重启 Photoshop？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self._on_ps_restart()

    def _refresh_companion_status(self):
        if not hasattr(self, 'parent') or self._parent is None: return
        if not hasattr(self._parent, 'sync_thread'): return
        c = self._parent.sync_thread.companion_sync
        connected = getattr(c, '_connected', False)
        if connected:
            self.lbl_companion_status.setText(i18n.tr("● 已连接"))
            self._set_label_state(self.lbl_companion_status, "success")
            self.btn_companion_reconnect.setVisible(False)
            self.btn_companion_disconnect.setVisible(True)
        elif c._has_session():
            self.lbl_companion_status.setText(i18n.tr("○ 已保存 — 等待 CSP..."))
            self._set_label_state(self.lbl_companion_status, "warning")
            self.btn_companion_reconnect.setVisible(True)
            self.btn_companion_disconnect.setVisible(False)
        else:
            self.lbl_companion_status.setText(i18n.tr("○ 未设置"))
            self._set_label_state(self.lbl_companion_status, "muted")
            self.btn_companion_reconnect.setText(i18n.tr("连接智能手机"))
            self.btn_companion_reconnect.setVisible(True)
            self.btn_companion_disconnect.setVisible(False)

    def _on_companion_reconnect(self):
        if hasattr(self, 'parent') and self._parent is not None:
            self._parent._setup_companion_connection()
            self._refresh_companion_status()

    def _on_companion_disconnect(self):
        if hasattr(self, 'parent') and self._parent is not None:
            if hasattr(self._parent, 'sync_thread'):
                self._parent.sync_thread.companion_sync._disconnect()
            self._refresh_companion_status()

    def _build_sync_page(self, page_sync):
        # ═══════════════════ Page 5: 同步 ═══════════════════
        card_sync, cl_sync = self._begin_card(page_sync, i18n.tr("同步与版本"))

        row_sync_status = QHBoxLayout()
        row_sync_status.setSpacing(6)
        self.lbl_sync_status = QLabel("")
        self.lbl_sync_status.setObjectName("StatusHint")
        row_sync_status.addWidget(self.lbl_sync_status, 1)
        self.btn_copy_diagnostics = QPushButton(i18n.tr("复制诊断信息"))
        self.btn_copy_diagnostics.setToolTip(
            i18n.tr("把版本、同步状态与最近日志复制到剪贴板，"
            "用于排查同步 / 崩溃问题"))
        self.btn_copy_diagnostics.clicked.connect(self._on_copy_diagnostics)
        row_sync_status.addWidget(self.btn_copy_diagnostics, 0)
        cl_sync.addLayout(row_sync_status)

        grid_sync = QGridLayout()
        grid_sync.setSpacing(6)
        grid_sync.setColumnMinimumWidth(0, 84)
        grid_sync.setColumnStretch(1, 1)
        grid_sync.addWidget(QLabel(i18n.tr("同步目标")), 0, 0)
        self.combo_software = NonScrollComboBox()
        for _val, _disp in [("csp", "CLIP Studio Paint"), ("sai", "SAI2"),
                            ("udm", "UDM Paint"), ("ps", "Photoshop"),
                            ("companion", "CSP Companion（手机）·推荐")]:
            self.combo_software.addItem(i18n.tr(_disp), _val)
        self.combo_software.currentTextChanged.connect(self.save_settings)
        self.combo_software.currentTextChanged.connect(self._on_software_changed)
        grid_sync.addWidget(self.combo_software, 0, 1)
        cl_sync.addLayout(grid_sync)

        # CSP 各连接模式的问题/推荐说明：5.1 内存同步已移除，推荐手机模式。
        self.row_csp_mode_tip_widget = QWidget()
        row_csp_mode_tip = QVBoxLayout(self.row_csp_mode_tip_widget)
        row_csp_mode_tip.setContentsMargins(0, 0, 0, 0)
        row_csp_mode_tip.setSpacing(4)
        self.lbl_csp_mode_tip = QLabel("")
        self.lbl_csp_mode_tip.setWordWrap(True)
        self.lbl_csp_mode_tip.setObjectName("StatusHint")
        row_csp_mode_tip.addWidget(self.lbl_csp_mode_tip)
        cl_sync.addWidget(self.row_csp_mode_tip_widget)
        self.row_csp_mode_tip_widget.hide()

        # Companion status row (visible only when "CSP 智能手机" selected)
        self.row_companion_widget = QWidget()
        row_comp = QHBoxLayout(self.row_companion_widget)
        row_comp.setContentsMargins(0, 0, 0, 0); row_comp.setSpacing(6)
        self.lbl_companion_status = QLabel(i18n.tr("未连接"))
        self.btn_companion_reconnect = QPushButton(i18n.tr("重新连接"))
        self.btn_companion_reconnect.clicked.connect(self._on_companion_reconnect)
        self.btn_companion_disconnect = QPushButton(i18n.tr("断开"))
        self.btn_companion_disconnect.clicked.connect(self._on_companion_disconnect)
        row_comp.addWidget(self.lbl_companion_status)
        row_comp.addStretch()
        row_comp.addWidget(self.btn_companion_reconnect)
        row_comp.addWidget(self.btn_companion_disconnect)
        cl_sync.addWidget(self.row_companion_widget)

        # CSP Version Container
        self.row_csp_widget = QWidget()
        row_csp_layout = QHBoxLayout(self.row_csp_widget)
        row_csp_layout.setContentsMargins(0, 0, 0, 0)
        row_csp_layout.addWidget(QLabel(i18n.tr("CSP 版本")))
        self.combo_csp = NonScrollComboBox()
        for _val, _disp in _CSP_VERSION_ITEMS:
            self.combo_csp.addItem(i18n.tr(_disp), _val)
        for i, (val, _disp) in enumerate(_CSP_VERSION_ITEMS):
            self.combo_csp.setItemData(
                i, i18n.tr(_CSP_VERSION_TIPS.get(val, "")), Qt.ItemDataRole.ToolTipRole
            )
        self.combo_csp.setToolTip(
            i18n.tr("内存模式仅支持 CSP 4.x/5.0 主色同步；"
            "CSP 5.1 已移除内存同步，请改用手机（Companion）模式")
        )
        self.combo_csp.currentTextChanged.connect(self._on_csp_version_changed)
        row_csp_layout.addWidget(self.combo_csp)
        cl_sync.addWidget(self.row_csp_widget)
        # 版本能力说明行：明确 5.0 与 5.1 的同步能力差异
        self.row_csp_hint_widget = QWidget()
        row_csp_hint = QVBoxLayout(self.row_csp_hint_widget)
        row_csp_hint.setContentsMargins(0, 0, 0, 0)
        row_csp_hint.setSpacing(4)
        self.lbl_csp_hint = QLabel("")
        self.lbl_csp_hint.setWordWrap(True)
        self.lbl_csp_hint.setObjectName("StatusHint")
        row_csp_hint.addWidget(self.lbl_csp_hint)
        cl_sync.addWidget(self.row_csp_hint_widget)

        # SAI2 Version Container
        self.row_sai_widget = QWidget()
        row_sai_layout = QHBoxLayout(self.row_sai_widget)
        row_sai_layout.setContentsMargins(0, 0, 0, 0)
        row_sai_layout.addWidget(QLabel(i18n.tr("SAI2 版本")))
        self.combo_sai = NonScrollComboBox()
        self.combo_sai.addItems(["auto", "pre-2024-sai2", "after-2024-sai2"])
        self.combo_sai.setToolTip(i18n.tr("2024 年后的 SAI2 版本地址偏移不同，自动检测失败时可手动指定"))
        self.combo_sai.currentTextChanged.connect(self.save_settings)
        row_sai_layout.addWidget(self.combo_sai)
        cl_sync.addWidget(self.row_sai_widget)

        # SAI 界面刷新：内存写入不会让 SAI 自己重绘，需要主动推一下
        self.row_sai_refresh_widget = QWidget()
        row_sai_refresh = QHBoxLayout(self.row_sai_refresh_widget)
        row_sai_refresh.setContentsMargins(0, 0, 0, 0)
        row_sai_refresh.addWidget(QLabel(i18n.tr("SAI 界面刷新")))
        self.combo_sai_refresh = NonScrollComboBox()
        for _val, _disp in _SAI_REFRESH_ITEMS:
            self.combo_sai_refresh.addItem(i18n.tr(_disp), _val)
        for _i, (_val, _disp) in enumerate(_SAI_REFRESH_ITEMS):
            self.combo_sai_refresh.setItemData(
                _i, i18n.tr(_SAI_REFRESH_TIPS.get(_val, "")), Qt.ItemDataRole.ToolTipRole
            )
        self.combo_sai_refresh.setToolTip(
            i18n.tr("写入颜色后顺便刷新 SAI 自己的画笔色块，"
                    "让 SAI 界面上的颜色跟着变")
        )
        self.combo_sai_refresh.currentTextChanged.connect(self.save_settings)
        row_sai_refresh.addWidget(self.combo_sai_refresh)
        cl_sync.addWidget(self.row_sai_refresh_widget)

        # UDM Version Container
        self.row_udm_widget = QWidget()
        row_udm_layout = QHBoxLayout(self.row_udm_widget)
        row_udm_layout.setContentsMargins(0, 0, 0, 0)
        row_udm_layout.addWidget(QLabel(i18n.tr("UDM 版本")))
        self.combo_udm = NonScrollComboBox()
        self.combo_udm.addItems(["auto", "udm4.0pro", "udm4.0ex"])
        self.combo_udm.currentTextChanged.connect(self.save_settings)
        row_udm_layout.addWidget(self.combo_udm)
        cl_sync.addWidget(self.row_udm_widget)

        # Photoshop version container
        self.row_ps_widget = QWidget()
        row_ps_layout = QHBoxLayout(self.row_ps_widget)
        row_ps_layout.setContentsMargins(0, 0, 0, 0)
        row_ps_layout.addWidget(QLabel(i18n.tr("Photoshop 版本")))
        self.combo_ps = NonScrollComboBox()
        self.combo_ps.addItems(["auto"])
        self.combo_ps.currentTextChanged.connect(self.save_settings)
        row_ps_layout.addWidget(self.combo_ps)
        cl_sync.addWidget(self.row_ps_widget)

        # Green/portable Photoshop script-bridge notice row (visible only
        # when a green edition is detected and PS sync is selected).
        self.row_ps_bridge_widget = QWidget()
        row_ps_bridge = QVBoxLayout(self.row_ps_bridge_widget)
        row_ps_bridge.setContentsMargins(0, 0, 0, 0)
        row_ps_bridge.setSpacing(4)
        self.lbl_ps_bridge_status = QLabel("")
        self.lbl_ps_bridge_status.setWordWrap(True)
        self.lbl_ps_bridge_status.setObjectName("StatusHint")
        row_ps_bridge.addWidget(self.lbl_ps_bridge_status)
        row_ps_bridge_btns = QHBoxLayout()
        row_ps_bridge_btns.setSpacing(6)
        self.btn_ps_bridge_recheck = QPushButton(i18n.tr("重新检测"))
        self.btn_ps_bridge_recheck.clicked.connect(self._on_ps_bridge_recheck)
        self.btn_ps_bridge_restart = QPushButton(i18n.tr("重启 Photoshop"))
        self.btn_ps_bridge_restart.clicked.connect(self._on_ps_restart)
        self.btn_ps_bridge_remove = QPushButton(i18n.tr("移除扩展"))
        self.btn_ps_bridge_remove.setToolTip(
            i18n.tr("删除已部署到绿色版 Photoshop 的同步扩展；"
            "如 Photoshop 正在运行，需重启后才会完全生效。"
            "当它与其它插件 / 控制器（如 TourBox、Coolorus）"
            "冲突时使用"))
        self.btn_ps_bridge_remove.clicked.connect(self._on_ps_bridge_remove)
        row_ps_bridge_btns.addWidget(self.btn_ps_bridge_recheck)
        row_ps_bridge_btns.addWidget(self.btn_ps_bridge_restart)
        row_ps_bridge_btns.addWidget(self.btn_ps_bridge_remove)
        row_ps_bridge_btns.addStretch()
        row_ps_bridge.addLayout(row_ps_bridge_btns)
        cl_sync.addWidget(self.row_ps_bridge_widget)
        self.row_ps_bridge_widget.hide()
        self._ps_bridge_prompted = False

        page_sync.addWidget(card_sync)


