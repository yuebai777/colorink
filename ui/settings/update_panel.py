"""Update check / download / config management for the settings sidebar.

Extracted from ``ui.settings_sidebar``: the About page's update worker flow,
self-update/download helpers, language switch and config import/export/reset.
"""

import json
import os
import sys
import webbrowser

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
)

from core import autostart, config, i18n, updater


class UpdatePanelMixin:

    def on_check_update(self):
        """Run the update check on a worker thread, then show a dialog."""
        if getattr(self, "_update_worker", None) is not None:
            return  # Already running
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText(i18n.tr("检查中..."))
        worker = _UpdateWorker(self)
        worker.done.connect(self._on_update_result)
        # Keep a reference alive until the signal fires; QThread auto-deletes
        # via finished->deleteLater once we let go in the slot.
        worker.finished.connect(worker.deleteLater)
        self._update_worker = worker
        worker.start()

    def _on_update_result(self, result: dict):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText(i18n.tr("检查更新"))
        self._update_worker = None
        self.prompt_update(result)

    def prompt_update(self, result: dict):
        """Show the "new version available" dialog and act on the choice.

        Shared by the manual check and the tray-notification path so both
        offer the same in-app download flow.
        """
        if "error" in result:
            QMessageBox.warning(self, i18n.tr("检查更新"), self._update_error_text(result))
            return

        current = result.get("current_version", "?")
        latest = result.get("latest_version", "?")
        url = result.get("release_url", updater.GITHUB_URL)
        notes = result.get("release_notes", "")
        has_update = result.get("has_update", False)

        if has_update:
            current_flavor = updater.build_flavor(sys.executable)
            other_flavor = "onedir" if current_flavor == "onefile" else "onefile"
            assets = result.get("assets", [])
            # Only offer a download button when a usable asset actually exists
            # for that flavor.  This prevents the “switch” button from sending
            # the user to a GitHub source archive when no onedir zip exists.
            current_asset = updater.find_installer_asset(assets, flavor=current_flavor)
            other_asset = updater.find_installer_asset(assets, flavor=other_flavor)
            msg = (
                f"{i18n.tr('发现新版本')} {latest}！\n"
                f"{i18n.tr('当前版本')}: v{current}\n\n"
            )
            if notes:
                snippet = notes if len(notes) <= 600 else notes[:600] + "..."
                msg += f"{i18n.tr('更新内容:')}\n{snippet}\n\n"
            msg += i18n.tr("可一键下载安装包，或前往 GitHub 页面。")
            box = QMessageBox(self)
            box.setWindowTitle(i18n.tr("发现新版本"))
            box.setText(msg)
            dl_btn = None
            if current_asset is not None:
                dl_btn = box.addButton(
                    i18n.tr("下载更新 ({flavor})", flavor=current_flavor),
                    QMessageBox.ButtonRole.AcceptRole,
                )
            switch_btn = None
            if other_asset is not None:
                switch_btn = box.addButton(
                    i18n.tr("下载 {flavor} 版（切换）", flavor=other_flavor),
                    QMessageBox.ButtonRole.ActionRole,
                )
            open_btn = box.addButton(i18n.tr("前往下载"), QMessageBox.ButtonRole.ActionRole)
            skip_btn = box.addButton(i18n.tr("跳过此版本"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(i18n.tr("稍后"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if dl_btn is not None and clicked is dl_btn:
                self._download_release(result, flavor=current_flavor)
            elif switch_btn is not None and clicked is switch_btn:
                self._download_release(result, flavor=other_flavor)
            elif clicked is open_btn:
                webbrowser.open(url)
            elif clicked is skip_btn:
                self.cfg["skippedUpdateVersion"] = latest
                self._persist_config()
        else:
            QMessageBox.information(
                self, i18n.tr("检查更新"),
                f"{i18n.tr('已是最新版本')} (v{current})"
            )

    def _update_error_text(self, result: dict) -> str:
        """Translate a structured updater error (source-as-key + detail)."""
        return i18n.tr(result.get("error", ""), detail=result.get("error_detail", ""))

    def _download_release(self, result: dict, flavor: str | None = None):
        """Download the picked installer asset to a user-chosen path.

        ``flavor`` selects which build to download: ``"onefile"`` or
        ``"onedir"``. It defaults to the currently running build.
        """
        flavor = flavor or updater.build_flavor(sys.executable)
        asset = updater.find_installer_asset(result.get("assets", []), flavor=flavor)
        if asset is None:
            # No installer asset on the release — fall back to the page.
            webbrowser.open(result.get("release_url", updater.GITHUB_URL))
            return
        name = asset.get("name") or ("Colorink-Onedir.zip" if flavor == "onedir" else "Colorink.exe")
        default_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        is_zip = name.lower().endswith(".zip")
        file_filter = i18n.tr("更新包 (*.exe *.zip)") if is_zip else i18n.tr("程序 (*.exe)")
        dest, _ = QFileDialog.getSaveFileName(
            self, i18n.tr("保存安装包"), os.path.join(default_dir, name), file_filter
        )
        if not dest:
            return
        # GitHub 对 release asset 提供 SHA-256 digest（"sha256:<hex>"）；
        # 有则校验，老资产没有 digest 时退回仅字节数校验。
        sha256 = None
        digest = asset.get("digest") or ""
        if isinstance(digest, str) and digest.startswith("sha256:"):
            sha256 = digest[len("sha256:"):].strip()
        # The actual downloaded flavor follows the asset extension: a onedir
        # request may fall back to the onefile EXE when no zip is published.
        self._pending_download_flavor = "onedir" if is_zip else "onefile"
        self._download_worker = _DownloadWorker(
            asset["url"], dest, asset.get("size"), self, sha256=sha256
        )
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.done.connect(self._on_download_done)
        self._download_worker.finished.connect(self._download_worker.deleteLater)
        self.btn_check_update.setText(i18n.tr("下载中"))
        self.btn_check_update.setEnabled(False)
        self._download_worker.start()

    def _on_download_progress(self, downloaded: int, total: int):
        label = i18n.tr("下载中")
        if total:
            pct = int(downloaded * 100 / total)
            self.btn_check_update.setText(f"{label} {pct}%")
        else:
            self.btn_check_update.setText(f"{label} {downloaded // 1024}KB")

    def _flush_state_before_update(self):
        """Persist settings + window geometry before the self-replace exit.

        ``os._exit`` bypasses normal shutdown, so without this the user's last
        window position and any unsaved settings changes are lost on update.
        """
        parent = getattr(self, "_parent", None)
        # Flush the main window's pending module write first so a stale copy
        # can't clobber the sidebar's fuller settings snapshot below.
        if parent is not None:
            try:
                flush = getattr(parent, "_flush_module_config_save", None)
                if callable(flush):
                    flush()
            except Exception:
                pass
        try:
            self._persist_config()
        except Exception:
            pass
        if parent is not None:
            try:
                save_geom = getattr(parent, "save_window_geometry", None)
                if callable(save_geom):
                    save_geom()
            except Exception:
                pass

    def _on_download_done(self, result: dict):
        self.btn_check_update.setText(i18n.tr("检查更新"))
        self.btn_check_update.setEnabled(True)
        self._download_worker = None
        downloaded_flavor = getattr(self, "_pending_download_flavor", None) or updater.build_flavor(sys.executable)
        self._pending_download_flavor = None
        if "error" in result:
            QMessageBox.warning(self, i18n.tr("下载失败"), self._update_error_text(result))
            return
        path = result["path"]
        is_zip = path.lower().endswith(".zip")
        current_flavor = updater.build_flavor(sys.executable)
        same_flavor = downloaded_flavor == current_flavor
        can_update = same_flavor and updater.can_self_update(sys.executable)
        # A onedir update must be a zip; an onefile update must be an exe.
        if (downloaded_flavor == "onedir") != is_zip:
            can_update = False

        box = QMessageBox(self)
        box.setWindowTitle(i18n.tr("下载完成"))
        text = i18n.tr("已下载到:\n{path}", path=path)
        if not same_flavor:
            if is_zip:
                text += "\n\n" + i18n.tr(
                    "这是 onedir 版。请先退出当前 Colorink，解压 zip 后运行其中的 Colorink.exe 以切换。"
                )
            else:
                text += "\n\n" + i18n.tr(
                    "这是 onefile 版。请先退出当前 Colorink，再运行该文件以切换。"
                )
        box.setText(text)
        install_btn = None
        if can_update:
            install_btn = box.addButton(i18n.tr("更新并重启"), QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton(i18n.tr("打开所在文件夹"), QMessageBox.ButtonRole.ActionRole)
        run_btn = None
        if not is_zip:
            run_btn = box.addButton(i18n.tr("立即运行"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(i18n.tr("关闭"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if install_btn is not None and clicked is install_btn:
            # Hand the update over to a detached helper, then exit. The helper
            # waits for our lock to release, replaces the app payload and
            # relaunches. If spawning fails, fall back to just running it.
            if downloaded_flavor == "onedir":
                launched = updater.launch_onedir_update(path, sys.executable)
            else:
                launched = updater.launch_self_replace(path, sys.executable)
            if launched:
                self._flush_state_before_update()
                os._exit(0)
            os.startfile(path)
        elif clicked is folder_btn:
            os.startfile(os.path.dirname(path))
        elif clicked is run_btn:
            os.startfile(path)

    def on_about_author(self):
        """Open the author's Bilibili homepage in the default browser."""
        webbrowser.open(updater.BILIBILI_URL)

    def _on_check_updates_toggled(self, checked: bool):
        self.cfg["checkUpdatesOnStartup"] = bool(checked)
        self._persist_config()

    def _on_language_changed(self, _index=None):
        # Read currentData() rather than the signal argument: PyQt6's
        # currentIndexChanged is overloaded (int / str) and the bound overload
        # can differ, so the argument is unreliable.
        lang = self.cmb_language.currentData()
        if not lang:
            return
        self.cfg["language"] = lang
        self._persist_config()
        i18n.set_language(i18n.resolve_language(lang))
        self.retranslate()
        parent = getattr(self, "_parent", None)
        if parent is not None and hasattr(parent, "retranslate"):
            parent.retranslate()

    def export_config(self):
        default_name = os.path.join(os.path.expanduser("~"), "Colorink-配置.json")
        path, _ = QFileDialog.getSaveFileName(self, i18n.tr("导出配置"), default_name, i18n.tr("JSON 文件 (*.json)"))
        if not path:
            return
        try:
            config.export_settings_to_file(self.cfg, path)
            QMessageBox.information(self, i18n.tr("导出配置"), i18n.tr("配置已导出到：\n{path}", path=path))
        except Exception as e:
            QMessageBox.warning(self, i18n.tr("导出配置"), i18n.tr("导出失败：{e}", e=e))

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, i18n.tr("导入配置"), os.path.expanduser("~"), i18n.tr("JSON 文件 (*.json)"))
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("format") == config.SETTINGS_EXPORT_FORMAT:
                imported = config.import_settings(data)
            else:
                # Legacy raw config (pre-envelope): merge + migrate + normalize.
                if not isinstance(data, dict):
                    raise ValueError(i18n.tr("配置文件格式不正确"))
                imported = config.merge_imported_config(data)
        except ValueError as e:
            QMessageBox.warning(self, i18n.tr("导入配置"), str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, i18n.tr("导入配置"), i18n.tr("读取失败：{e}", e=e))
            return
        old_autostart = self.cfg.get("openAtLogin", False)
        new_autostart = imported.get("openAtLogin", False)
        self.cfg = imported
        config.save_hotkey_config(self.cfg)
        if old_autostart != new_autostart:
            autostart.apply_autostart(new_autostart)
        self.refresh_ui()
        self.settingChanged.emit()
        QMessageBox.information(self, i18n.tr("导入配置"), i18n.tr("配置已导入并生效。"))

    def reset_config(self):
        answer = QMessageBox.question(
            self,
            i18n.tr("恢复默认"),
            i18n.tr("确定要恢复所有设置为默认值吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        old_autostart = self.cfg.get("openAtLogin", False)
        self.cfg = config.default_hotkey_config()
        config.save_hotkey_config(self.cfg)
        if old_autostart:
            autostart.apply_autostart(False)
        self.refresh_ui()
        self.settingChanged.emit()
        QMessageBox.information(self, i18n.tr("恢复默认"), i18n.tr("设置已恢复为默认值。"))


    def _build_about_page(self, page_about):
        # ═══════════════════ Page 6: 关于 ═══════════════════
        card_about, cl_about = self._begin_card(page_about, i18n.tr("关于"))

        row_version = QHBoxLayout()
        row_version.addWidget(QLabel(i18n.tr("当前版本")))
        self.lbl_version_value = QLabel(f"v{updater.APP_VERSION}")
        self.lbl_version_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_version.addStretch()
        row_version.addWidget(self.lbl_version_value)
        cl_about.addLayout(row_version)

        row_about_actions = QHBoxLayout()
        row_about_actions.setSpacing(6)
        self.btn_check_update = QPushButton(i18n.tr("检查更新"))
        self.btn_check_update.clicked.connect(self.on_check_update)
        self.btn_about_author = QPushButton(i18n.tr("关于作者"))
        self.btn_about_author.clicked.connect(self.on_about_author)
        row_about_actions.addWidget(self.btn_check_update)
        row_about_actions.addWidget(self.btn_about_author)
        row_about_actions.addStretch()
        cl_about.addLayout(row_about_actions)

        self.cb_check_updates = QCheckBox(i18n.tr("启动时自动检查更新"))
        self.cb_check_updates.setChecked(self.cfg.get("checkUpdatesOnStartup", True))
        self.cb_check_updates.toggled.connect(self._on_check_updates_toggled)
        cl_about.addWidget(self.cb_check_updates)

        cl_about.addStretch()
        page_about.addWidget(card_about)

        card_config, cl_config = self._begin_card(page_about, i18n.tr("配置管理"))
        row_config_actions = QHBoxLayout()
        row_config_actions.setSpacing(6)
        self.btn_export_config = QPushButton(i18n.tr("导出配置"))
        self.btn_export_config.setToolTip(i18n.tr("把当前设置保存为 JSON 文件"))
        self.btn_export_config.clicked.connect(self.export_config)
        self.btn_import_config = QPushButton(i18n.tr("导入配置"))
        self.btn_import_config.setToolTip(i18n.tr("从 JSON 文件恢复设置"))
        self.btn_import_config.clicked.connect(self.import_config)
        self.btn_reset_config = QPushButton(i18n.tr("恢复默认"))
        self.btn_reset_config.setToolTip(i18n.tr("恢复全部设置为出厂默认值"))
        self.btn_reset_config.clicked.connect(self.reset_config)
        row_config_actions.addWidget(self.btn_export_config)
        row_config_actions.addWidget(self.btn_import_config)
        row_config_actions.addWidget(self.btn_reset_config)
        cl_config.addLayout(row_config_actions)
        page_about.addWidget(card_config)


class _UpdateWorker(QThread):
    """Background worker that queries GitHub for the latest release."""

    done = pyqtSignal(dict)

    def run(self):  # noqa: D401 - QThread override
        self.done.emit(updater.check_for_update())


class _DownloadWorker(QThread):
    """Background worker that downloads a release asset to disk."""

    progress = pyqtSignal(int, int)
    done = pyqtSignal(dict)

    def __init__(self, url: str, dest_path: str, total_size, parent=None,
                 sha256: str | None = None):
        super().__init__(parent)
        self._url = url
        self._dest_path = dest_path
        self._total_size = total_size
        self._sha256 = sha256

    def run(self):  # noqa: D401 - QThread override
        self.done.emit(updater.download_release(
            self._url,
            self._dest_path,
            total_size=self._total_size,
            progress_cb=lambda downloaded, total: self.progress.emit(downloaded, total),
            sha256=self._sha256,
        ))
