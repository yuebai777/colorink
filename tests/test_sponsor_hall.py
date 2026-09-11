"""UI + wiring tests for the sponsor honour roll dialog.

The fixture stack mirrors ``tests/test_settings_window.py`` (fixtures are not
shared across modules, so it is copied rather than imported): a stub main
window whose config directory is isolated to ``tmp_path``, a real
``SettingsSidebar``, and a real ``SettingsWindow`` hosting it.
"""

import os

import pytest

from core import i18n
from core.config import load_hotkey_config

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    i18n.set_language(i18n.LANG_ZH)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def stub_main_window(qapp, tmp_path, monkeypatch):
    from core import config as _config

    monkeypatch.setattr(_config, "get_user_data_dir", lambda: str(tmp_path))

    from PyQt6.QtWidgets import QWidget

    class StubCompanionSync:
        _connected = False

        def _has_session(self):
            return False

        def _disconnect(self):
            pass

    class StubSyncThread:
        def __init__(self):
            self.companion_sync = StubCompanionSync()

    class StubMainWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = load_hotkey_config()
            self.cfg.setdefault("ui-theme", "auto")
            self.cfg.setdefault("fontSize", 100)
            self.sync_thread = StubSyncThread()

        def on_settings_saved(self):
            pass

        def zoom_ui(self, *args, **kwargs):
            pass

        def update_window_flags(self):
            pass

        def update_no_focus_policies(self):
            pass

    return StubMainWindow()


@pytest.fixture
def sidebar(stub_main_window, qapp):
    from ui.settings_sidebar import SettingsSidebar

    s = SettingsSidebar(stub_main_window)
    s.setVisible(False)
    stub_main_window.settings_sidebar = s
    return s


@pytest.fixture
def settings_window(stub_main_window, sidebar, qapp):
    from ui.settings_window import SettingsWindow

    return SettingsWindow(stub_main_window, sidebar)


@pytest.fixture
def sponsors_stub(monkeypatch):
    """Control what the dialog reads, without touching the real data file."""
    from core import sponsors

    def _install(names_and_dates):
        entries = [
            sponsors.Sponsor(name, last_pay=date)
            for name, date in names_and_dates
        ]
        monkeypatch.setattr(sponsors, "sorted_sponsors", lambda: list(entries))
        return entries

    return _install


# ── Rendering ───────────────────────────────────────────────────────────


class TestRendering:
    def test_rows_match_the_data_and_keep_its_order(self, qapp, sponsors_stub):
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("最新", "2026-08"), ("中间", "2026-05"), ("最早", "2026-01")])

        dialog = SponsorHallDialog(None)

        assert len(dialog._rows) == 3
        # The dialog must not re-sort: ordering is the data layer's job.
        assert [row.name for row in dialog._rows] == ["最新", "中间", "最早"]
        assert dialog._lbl_count.isHidden() is False
        assert dialog._lbl_empty.isHidden() is True

    def test_empty_state(self, qapp, sponsors_stub):
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([])

        dialog = SponsorHallDialog(None)

        assert dialog._rows == []
        assert dialog._lbl_empty.isHidden() is False
        assert dialog._lbl_count.isHidden() is True
        assert dialog._rows_container.isHidden() is True

    def test_message_and_date_are_optional(self, qapp, monkeypatch):
        from core import sponsors
        from ui.sponsor_hall import SponsorHallDialog

        monkeypatch.setattr(
            sponsors, "sorted_sponsors",
            lambda: [sponsors.Sponsor("极简")],  # name only — the least-typing case
        )

        dialog = SponsorHallDialog(None)

        assert dialog._rows[0].name == "极简"
        # No message label, no date label — and crucially no crash on the
        # empty date when building the pixmap / labels.
        assert dialog._rows[0].avatar.pixmap() is not None

    def test_count_label_reports_the_total(self, qapp, sponsors_stub):
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01"), ("乙", "2026-02")])

        dialog = SponsorHallDialog(None)

        assert dialog._lbl_count.text() == i18n.tr("共 {n} 位赞助者", n=2)

    def test_one_long_nickname_does_not_push_the_dates_off_screen(
        self, qapp, sponsors_stub
    ):
        """A name is arbitrary text; it must not resize the whole list.

        The name label's minimum width used to bubble into the scroll area, so
        one long nickname widened the body past the viewport and — because the
        date sits at the end of each row and horizontal scrolling is off — every
        row silently lost its date, including the short-named ones.
        """
        from PyQt6.QtWidgets import QLabel

        from ui.sponsor_hall import SponsorHallDialog

        long_name = "这是一个特别特别特别长的昵称用来测试布局是否会被撑破呢真的很长"
        sponsors_stub([("短名", "2026-08-01"), (long_name, "2026-07-01")])

        dialog = SponsorHallDialog(None)
        dialog.show()
        qapp.processEvents()

        viewport = dialog._scroll.viewport().width()
        assert dialog._scroll.widget().width() <= viewport, (
            "the list body grew past the viewport because of a long nickname")

        # Every row keeps its date inside the visible width.
        for row in dialog._rows:
            for label in row.widget.findChildren(QLabel):
                if label.objectName() != "SponsorDate":
                    continue
                right_edge = label.mapTo(dialog, label.rect().topLeft()).x() + label.width()
                assert right_edge <= viewport, (
                    f"date for {row.name!r} sits outside the viewport")

        # And the full nickname is still reachable, just not printed in full.
        name_label = next(
            lb for lb in dialog._rows[1].widget.findChildren(QLabel)
            if lb.objectName() == "SponsorName"
        )
        assert name_label.full_text() == long_name
        assert name_label.toolTip() == long_name
        assert name_label.fontMetrics().horizontalAdvance(long_name) > name_label.width(), (
            "this name is not actually long enough to exercise eliding")

    def test_short_nicknames_render_verbatim(self, qapp, sponsors_stub):
        """Eliding must not kick in when the text fits — no stray dots."""
        from PyQt6.QtWidgets import QLabel

        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("张三", "2026-08-01")])

        dialog = SponsorHallDialog(None)
        dialog.show()
        qapp.processEvents()

        label = next(
            lb for lb in dialog._rows[0].widget.findChildren(QLabel)
            if lb.objectName() == "SponsorName"
        )
        assert label.full_text() == "张三"
        assert label.fontMetrics().horizontalAdvance("张三") <= label.width()


class TestAvatars:
    """The avatar must show the sponsor's own image, else a drawn initial."""

    @staticmethod
    def _solid_image(path, color="#ff0000"):
        from PyQt6.QtGui import QColor, QPixmap

        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(color))
        assert pixmap.save(str(path))
        return path

    def _install(self, monkeypatch, tmp_path, avatar_value):
        from core import sponsors

        monkeypatch.setattr(sponsors, "_data_path", lambda: tmp_path / "sponsors.json")
        monkeypatch.setattr(
            sponsors, "sorted_sponsors",
            lambda: [sponsors.Sponsor("甲", last_pay="2026-08", avatar=avatar_value)],
        )

    def test_committed_image_is_actually_drawn(self, qapp, tmp_path, monkeypatch):
        from ui import sponsor_hall

        image = self._solid_image(tmp_path / "avatars" / "x.png")
        self._install(monkeypatch, tmp_path, "avatars/x.png")

        dialog = sponsor_hall.SponsorHallDialog(None)
        rendered = dialog._rows[0].avatar.pixmap()

        assert dialog._rows[0].image_path == image.resolve()
        assert rendered is not None and not rendered.isNull()
        # A solid red source must show red where the accent-filled badge shows
        # blue/ink — proof the photo branch ran, not just that a pixmap exists.
        centre = rendered.toImage().pixelColor(34, 34)
        badge = sponsor_hall._avatar_pixmap("甲", 34, "#5a94e2", "#222222", None)
        assert centre != badge.toImage().pixelColor(34, 34)
        assert centre.red() > 200 and centre.green() < 60

    def test_missing_image_falls_back_to_initials(self, qapp, tmp_path, monkeypatch):
        from ui import sponsor_hall

        self._install(monkeypatch, tmp_path, "avatars/gone.png")

        dialog = sponsor_hall.SponsorHallDialog(None)

        assert dialog._rows[0].image_path is None
        assert not dialog._rows[0].avatar.pixmap().isNull()

    def test_no_avatar_uses_initials(self, qapp, tmp_path, monkeypatch):
        from ui import sponsor_hall

        self._install(monkeypatch, tmp_path, "")

        dialog = sponsor_hall.SponsorHallDialog(None)

        assert dialog._rows[0].image_path is None

    def test_image_survives_a_theme_change(self, qapp, tmp_path, monkeypatch):
        from ui import sponsor_hall

        image = self._solid_image(tmp_path / "avatars" / "x.png", "#00ff00")
        self._install(monkeypatch, tmp_path, "avatars/x.png")

        dialog = sponsor_hall.SponsorHallDialog(None)
        dialog._apply_theme()

        centre = dialog._rows[0].avatar.pixmap().toImage().pixelColor(34, 34)
        assert centre.green() > 200, "repaint after theming dropped the photo"
        assert dialog._rows[0].image_path == image.resolve()

    def test_avatar_covers_the_circle_at_any_aspect_ratio(self, qapp, tmp_path, monkeypatch):
        """A non-square source must be centre-cropped, not letterboxed."""
        from PyQt6.QtGui import QColor, QPixmap

        from ui import sponsor_hall

        wide = tmp_path / "avatars" / "wide.png"
        wide.parent.mkdir(parents=True, exist_ok=True)
        pixmap = QPixmap(128, 32)  # 4:1
        pixmap.fill(QColor("#0000ff"))
        assert pixmap.save(str(wide))
        self._install(monkeypatch, tmp_path, "avatars/wide.png")

        dialog = sponsor_hall.SponsorHallDialog(None)
        rendered = dialog._rows[0].avatar.pixmap().toImage()

        # Corners stay transparent (inside the ring, outside the circle) while
        # the centre is the photo — i.e. it was scaled to cover.
        assert rendered.pixelColor(34, 34).blue() > 200
        assert rendered.pixelColor(2, 2).alpha() < 40


# ── Actions & theming ───────────────────────────────────────────────────


class TestActionsAndTheming:
    def test_sponsor_button_opens_afdian(self, qapp, sponsors_stub, monkeypatch):
        import ui.sponsor_hall as hall
        from core import sponsors

        sponsors_stub([])
        opened = []
        monkeypatch.setattr(hall.webbrowser, "open", lambda url: opened.append(url))

        dialog = hall.SponsorHallDialog(None)
        dialog._btn_sponsor.click()

        assert opened == [sponsors.AFDIAN_URL]

    def test_theme_apply_is_safe_on_both_extremes(self, qapp, sponsors_stub):
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01")])
        dialog = SponsorHallDialog(None)

        for colors in (
            {"bg": "#ffffff", "bar_bg": "#b2b2b2", "bar_text": "#222222",
             "bar_muted": "rgba(34,34,34,0.45)", "accent": "#5a94e2"},
            {"bg": "#1e1e1e", "bar_bg": "#2d2d2d", "bar_text": "#ffffff",
             "bar_muted": "rgba(255,255,255,0.45)", "accent": "#5a94e2"},
            {},  # missing keys must not blow up
        ):
            dialog._colors = lambda c=colors: c
            dialog._apply_theme()

        assert dialog.styleSheet()

    def test_partial_theme_dict_from_host_is_tolerated(
        self, qapp, sidebar, settings_window, sponsors_stub, monkeypatch
    ):
        """A theme dict missing keys must degrade, not raise."""
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01")])
        monkeypatch.setattr(sidebar, "theme_colors", lambda: {"accent": "#ff00ff"})

        dialog = SponsorHallDialog(settings_window)

        assert "#ff00ff" in dialog.styleSheet()
        assert dialog._colors()["bar_text"]

    def test_follows_font_size_through_the_settings_window(
        self, qapp, sidebar, settings_window, sponsors_stub
    ):
        """The real call path: host is SettingsWindow, which owns no cfg itself.

        Without a fallback to the sidebar/main window this silently stayed at
        100%, which is invisible in a unit test that hands the dialog a bare
        stub that happens to carry ``cfg``.
        """
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01")])
        settings_window.cfg["fontSize"] = 100
        dialog = SponsorHallDialog(settings_window)
        assert "font-size: 11px" in dialog.styleSheet()

        settings_window.cfg["fontSize"] = 130
        sidebar.settingChanged.emit()  # what the appearance panel does

        assert "font-size: 14px" in dialog.styleSheet(), (
            "the roll ignored the font-size setting on the real host")

    def test_constructs_without_a_host(self, qapp, sponsors_stub):
        """The ``parent=None`` path must stay usable (tests, odd call sites)."""
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01")])
        dialog = SponsorHallDialog(None)

        assert dialog._colors()["bar_text"]
        assert dialog.width() > 0 and dialog.height() > 0


# ── Host-window mirroring (empirically discovered, see plan Task 4.10) ──


class TestHostMirroring:
    def test_hides_and_returns_with_a_theme_pick(
        self, qapp, sidebar, settings_window, sponsors_stub
    ):
        sponsors_stub([("甲", "2026-01")])
        settings_window.show()
        sidebar.on_open_sponsors()
        dialog = sidebar._sponsor_dialog
        assert dialog.isVisible()

        sidebar.pickingThemePoint.emit(True)
        assert not dialog.isVisible(), "roll must leave the screen while picking"

        sidebar.pickingThemePoint.emit(False)
        assert dialog.isVisible(), "roll should come back after the pick"

    def test_dialog_lands_on_the_host_s_screen(
        self, qapp, sidebar, settings_window, sponsors_stub
    ):
        """Open on a second monitor and the roll must follow the host there.

        Sizing before centring asked about the *old* position, so the window
        could be sized for one screen and moved onto another.

        No ``pytest.skip`` here on purpose: skipping mid-test tears down the
        ``settings_window`` fixture while the roll is still parented to it, and
        that ordering aborts the interpreter at exit (0xC0000409) instead of
        failing a test. The offscreen platform reports a small screen, so this
        simply returns.
        """
        from PyQt6.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        if avail.width() < 900 or avail.height() < 700:
            return

        sponsors_stub([("甲", "2026-01")])
        settings_window.move(avail.left() + 8, avail.top() + 8)
        settings_window.show()

        sidebar.on_open_sponsors()
        dialog = sidebar._sponsor_dialog

        assert dialog.height() <= avail.height() - 40, (
            "the roll was sized for a different screen than the one it opened on")
        assert avail.left() <= dialog.x() and dialog.x() + dialog.width() <= avail.right() + 1

    def test_dismissed_dialog_is_not_resurrected(
        self, qapp, sidebar, settings_window, sponsors_stub
    ):
        sponsors_stub([])
        settings_window.show()
        sidebar.on_open_sponsors()
        dialog = sidebar._sponsor_dialog

        dialog.reject()
        assert not dialog.isVisible()

        settings_window.hide()
        settings_window.show()
        assert not dialog.isVisible(), "a dismissed roll must stay dismissed"

    def test_a_dragged_position_survives_a_theme_pick(
        self, qapp, sidebar, settings_window, sponsors_stub
    ):
        """The drag handle must mean something after an eyedropper round-trip.

        Re-centring on every ``showEvent`` threw the user's position away each
        time the roll came back from a pick (the host hides and re-shows).
        """
        sponsors_stub([("甲", "2026-01")])
        settings_window.move(200, 200)
        settings_window.show()
        sidebar.on_open_sponsors()
        dialog = sidebar._sponsor_dialog

        dialog.move(60, 40)
        dragged = dialog.pos()

        sidebar.pickingThemePoint.emit(True)
        sidebar.pickingThemePoint.emit(False)

        assert dialog.isVisible()
        assert dialog.pos() == dragged, "the roll jumped back to the host centre"

    def test_no_orphan_when_settings_window_hides(
        self, qapp, sidebar, settings_window, sponsors_stub
    ):
        sponsors_stub([("甲", "2026-01")])
        settings_window.show()
        sidebar.on_open_sponsors()
        dialog = sidebar._sponsor_dialog

        settings_window.hide()

        assert not dialog.isVisible(), (
            "Qt.Tool children do not inherit hiding — this is the regression "
            "guard for that"
        )


# ── Entry point wiring ──────────────────────────────────────────────────


class TestEntryPoint:
    def test_button_exists_and_is_not_a_nav_page(self, sidebar):
        assert hasattr(sidebar, "btn_sponsors")
        # Bookkeeping guard for the "no new rail page" constraint.
        assert sidebar.stack.count() == 5

    def test_button_text(self, sidebar):
        assert sidebar.btn_sponsors.text() == i18n.tr("鸣谢赞助者")

    def test_click_opens_the_dialog(self, sidebar, monkeypatch):
        import ui.sponsor_hall as hall

        created = []

        class FakeDialog:
            def __init__(self, parent=None):
                created.append(parent)
                self._visible = False

            def show(self):
                self._visible = True

            def isVisible(self):
                return self._visible

            def raise_(self):
                pass

            def activateWindow(self):
                pass

        # ``on_open_sponsors`` imports lazily, so patch the source module.
        monkeypatch.setattr(hall, "SponsorHallDialog", FakeDialog)

        sidebar.btn_sponsors.click()

        assert len(created) == 1
        assert isinstance(sidebar._sponsor_dialog, FakeDialog)

    def test_repeat_click_reuses_the_live_dialog(self, sidebar, monkeypatch):
        import ui.sponsor_hall as hall

        created = []

        class FakeDialog:
            def __init__(self, parent=None):
                created.append(self)
                self._visible = False
                self.raised = 0

            def show(self):
                self._visible = True

            def isVisible(self):
                return self._visible

            def raise_(self):
                self.raised += 1

            def activateWindow(self):
                pass

            def deleteLater(self):
                pass

        monkeypatch.setattr(hall, "SponsorHallDialog", FakeDialog)

        sidebar.on_open_sponsors()
        sidebar.on_open_sponsors()

        assert len(created) == 1, "a visible roll must be reused, not duplicated"
        assert created[0].raised >= 1

    def test_closed_dialog_is_replaced(self, sidebar, monkeypatch):
        import ui.sponsor_hall as hall

        created = []

        class FakeDialog:
            def __init__(self, parent=None):
                created.append(self)
                self._visible = False
                self.deleted = False

            def show(self):
                self._visible = True

            def isVisible(self):
                return self._visible

            def raise_(self):
                pass

            def activateWindow(self):
                pass

            def deleteLater(self):
                self.deleted = True

        monkeypatch.setattr(hall, "SponsorHallDialog", FakeDialog)

        sidebar.on_open_sponsors()
        first = created[0]
        first._visible = False  # user closed it

        sidebar.on_open_sponsors()

        assert len(created) == 2
        assert first.deleted, "the closed dialog should be released, not leaked"

    def test_host_is_the_settings_window(self, sidebar, settings_window, sponsors_stub):
        sponsors_stub([])
        settings_window.show()

        sidebar.on_open_sponsors()

        assert sidebar._sponsor_dialog.parent() is settings_window


# ── Language switching ──────────────────────────────────────────────────


class TestLanguage:
    def test_dialog_is_english_after_language_switch(self, qapp, sponsors_stub):
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([])
        i18n.set_language(i18n.LANG_EN)
        try:
            dialog = SponsorHallDialog(None)

            assert dialog._title_bar._label.text() == "Thanks · Sponsor list"
            assert dialog._btn_sponsor.text() == "Sponsor on Afdian"
            assert dialog._lbl_empty.text() == "No sponsors yet — be the first ☕"
        finally:
            i18n.set_language(i18n.LANG_ZH)

    def test_english_count_line_formats(self, qapp, sponsors_stub):
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01"), ("乙", "2026-02")])
        i18n.set_language(i18n.LANG_EN)
        try:
            dialog = SponsorHallDialog(None)

            assert dialog._lbl_count.text() == "2 sponsors in total"
        finally:
            i18n.set_language(i18n.LANG_ZH)

    def test_english_singular_is_not_plural(self, qapp, sponsors_stub):
        """One sponsor is a real state — it is where every roll starts."""
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01")])
        i18n.set_language(i18n.LANG_EN)
        try:
            dialog = SponsorHallDialog(None)

            assert dialog._lbl_count.text() == "1 sponsor in total"
        finally:
            i18n.set_language(i18n.LANG_ZH)

    def test_chinese_count_never_shows_the_plural_marker(self, qapp, sponsors_stub):
        """The singular key exists for English; its Chinese side must stay unused."""
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01")])
        i18n.set_language(i18n.LANG_ZH)

        dialog = SponsorHallDialog(None)

        assert dialog._lbl_count.text() == "共 1 位赞助者"
        assert "单数" not in dialog._lbl_count.text()

    def test_an_open_roll_follows_a_live_language_switch(self, qapp, sponsors_stub):
        """The language combo lives on the same page as the button that opens it."""
        from ui.sponsor_hall import SponsorHallDialog

        sponsors_stub([("甲", "2026-01")])
        i18n.set_language(i18n.LANG_ZH)
        dialog = SponsorHallDialog(None)
        assert dialog._btn_sponsor.text() == "前往爱发电赞助"

        try:
            i18n.set_language(i18n.LANG_EN)

            assert dialog._btn_sponsor.text() == "Sponsor on Afdian"
            assert dialog._title_bar._label.text() == "Thanks · Sponsor list"
            assert dialog.windowTitle() == "Thanks · Sponsor list"
            assert dialog._lbl_intro.text().startswith("Colorink is free and open source")
            assert dialog._lbl_count.text() == "1 sponsor in total"
        finally:
            i18n.set_language(i18n.LANG_ZH)

        # …and switching back restores Chinese, without a restart.
        assert dialog._btn_sponsor.text() == "前往爱发电赞助"
