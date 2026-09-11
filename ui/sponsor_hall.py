"""Sponsor honour roll dialog for Colorink (鸣谢 · 赞助者名单).

Opened from 设置 →「同步」页 →「关于与更新」→「鸣谢赞助者」.

As a child window of ``SettingsWindow`` this dialog inherits its always-on-top
stacking. It does **not**, however, inherit its hiding: a ``Qt.Tool`` child is
a top-level window on Windows and stays on screen when the parent hides —
verified empirically, and the reason for the explicit show/hide mirroring
below. Without it, hiding the settings window (e.g. while the eyedropper theme
pick runs) would leave the honour roll floating over the screen and in the way
of the pick.

Theming mirrors ``ui.settings_window``: colours come from the host sidebar's
``theme_colors()`` so both surfaces agree, and the font size follows the same
``fontSize`` setting. Nothing here touches the network — the roll of honour is
bundled data (see ``core.sponsors``).
"""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import i18n, sponsors
from ui.theme_contrast import muted_ink, readable_ink

_DIALOG_WIDTH = 420
_AVATAR_SIZE = 34
_MAX_HEIGHT = 560
_MIN_HEIGHT = 320

# Mirrors the fallback set in ``appearance_panel.theme_colors`` so a dialog
# built without a host window (tests) still paints something legible.
_FALLBACK_SURFACE = "#787878"


def _fallback_colors() -> dict:
    """A complete palette for a dialog built without a themeable host.

    Complete on purpose: ``_apply_theme`` must never raise on a partial or
    missing theme dict, so every consumer merges onto this.
    """
    surface = _FALLBACK_SURFACE
    ink = readable_ink(surface)
    return {
        "bg": "#b2b2b2",
        "bar_bg": surface,
        "bar_text": ink,
        "bar_muted": muted_ink(ink),
        "border": surface,
        "accent": "#5a94e2",
    }


def _count_text(total: int) -> str:
    """``共 N 位赞助者``, with the English singular handled explicitly.

    Two keys rather than one is a deliberate trade: the translation table is a
    flat text→text map, so it cannot express a plural rule, and a one-person
    honour roll is a real state (it is where every roll starts).

    The singular key is consulted in English only. Its Chinese side exists to
    keep the table complete, and rendering it would leak the "（单数）" marker
    into the interface — Chinese has no plural form to distinguish.
    """
    if total == 1 and i18n.get_language() == i18n.LANG_EN:
        return i18n.tr("共 {n} 位赞助者（单数）", n=total)
    return i18n.tr("共 {n} 位赞助者", n=total)


def _avatar_pixmap(name: str, size: int, surface: str, ink: str,
                   image_path=None) -> QPixmap:
    """A circular avatar: the sponsor's own image, else a drawn initial badge.

    Network avatars are intentionally not used: the afdiancdn URLs are not
    reliably reachable from the target audience, and hot-linking them would
    make every dialog open depend on a third party. Sponsor-chosen images are
    committed alongside the data file instead (see ``core.sponsors``).

    A missing/unreadable image silently falls back to the initial badge, so a
    bad path degrades one row rather than breaking the roll.
    """
    side = size * 2  # device pixels; DPR applied at the end
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, side, side))
    painter.setClipPath(clip)

    photo = QPixmap(str(image_path)) if image_path else QPixmap()
    if not photo.isNull():
        # Cover-fit: fill the circle, centre-crop the overflow.
        scaled = photo.scaled(
            side, side,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(
            int((side - scaled.width()) / 2),
            int((side - scaled.height()) / 2),
            scaled,
        )
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(surface))
        painter.drawEllipse(0, 0, side, side)
        font = QFont()
        # Name the CJK-capable faces explicitly: the badge's initial is often a
        # Chinese character, and relying on the bare default leaves that to
        # platform font fallback.
        font.setFamilies(["Segoe UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC"])
        font.setPixelSize(int(size * 0.95))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(ink))
        painter.drawText(QRect(0, 0, side, side),
                         Qt.AlignmentFlag.AlignCenter,
                         (name.strip()[:1] or "?").upper())

    painter.setClipping(False)
    # Hairline ring, so a pale avatar does not melt into a pale panel.
    ring = QColor(ink)
    ring.setAlpha(80)
    pen = QPen(ring)
    pen.setWidthF(max(1.0, size * 0.055))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(pen)
    painter.drawEllipse(QRectF(0, 0, side, side))
    painter.end()

    pixmap.setDevicePixelRatio(2.0)
    return pixmap


class _ElidedLabel(QLabel):
    """A single-line label that shortens its text instead of growing its row.

    A ``QLabel`` that neither wraps nor elides reports its *full* text width as
    its minimum size hint. That bubbles up through the row layout into the
    scroll area's inner widget, which then resizes to fit the longest nickname
    in the list. Because the horizontal scrollbar is off by design, the whole
    body ends up wider than the viewport — and since the row pushes its date
    label to the far right, *every* row's date ends up off-screen, even the
    rows with short names. One long nickname silently hid all the dates.

    Sizing to zero width and painting the elided form keeps the body at the
    viewport width no matter what the data file contains. Eliding beats plain
    clipping here: a clipped name loses characters with no sign that anything
    is missing, which reads as data corruption rather than truncation.
    """

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def full_text(self) -> str:
        """The untruncated text, for tests and tooltips."""
        return self._full_text

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        metrics = self.fontMetrics()
        width = self.width()
        elided = metrics.elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, width)
        # Tooltip only when something is actually hidden: an unconditional one
        # would make every short nickname pop a box that repeats itself.
        self.setToolTip(self._full_text if elided != self._full_text else "")
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )
        painter.end()


class _TitleBar(QWidget):
    """Drag handle + label + close button.

    Deliberately private to this module rather than reusing
    ``ui.settings_window._TitleBar``: the latter is module-private and wired to
    ``SettingsWindow``, so importing it would create an implicit coupling.
    """

    def __init__(self, dialog: "SponsorHallDialog"):
        super().__init__(dialog)
        self._dialog = dialog
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(28)
        self._drag_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self._label = QLabel(i18n.tr("鸣谢 · 赞助者名单"))
        font = self._label.font()
        font.setBold(True)
        self._label.setFont(font)

        self._btn_close = QPushButton("×")
        self._btn_close.setObjectName("SponsorCloseButton")
        self._btn_close.setFixedSize(20, 20)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.setToolTip(i18n.tr("关闭"))
        self._btn_close.clicked.connect(self._dialog.reject)

        layout.addWidget(self._label)
        layout.addStretch()
        layout.addWidget(self._btn_close)

    def retranslate(self):
        """Re-apply the caption after a language switch."""
        self._label.setText(i18n.tr("鸣谢 · 赞助者名单"))
        self._btn_close.setToolTip(i18n.tr("关闭"))

    def apply_theme(self, colors: dict):
        text = colors["bar_text"]
        divider = muted_ink(text, 0.14)
        self.setStyleSheet(f"""
            _TitleBar {{
                background-color: {colors["bar_bg"]};
                border-bottom: 1px solid {divider};
            }}
            QLabel {{
                color: {text};
                background: transparent;
            }}
            QPushButton#SponsorCloseButton {{
                background: transparent;
                border: none;
                color: {text};
                font-size: 14px;
                border-radius: 3px;
            }}
            QPushButton#SponsorCloseButton:hover {{
                background-color: #ff5050;
                color: white;
            }}
            QPushButton#SponsorCloseButton:pressed {{
                background-color: #cc4040;
            }}
        """)

    def mousePressEvent(self, event):
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if (event is not None
                and event.buttons() == Qt.MouseButton.LeftButton
                and self._drag_offset is not None):
            self._dialog.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None


@dataclass
class _Row:
    """One rendered sponsor row, kept so a theme change can repaint the badge."""

    widget: QWidget
    avatar: QLabel
    name: str
    image_path: object = None


class SponsorHallDialog(QDialog):
    """The honour roll itself: a scrollable, single time-ordered list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._host = parent
        self._sponsors: list[sponsors.Sponsor] = []
        self._rows: list[_Row] = []
        # Mirroring state: see ``eventFilter`` / ``hideEvent``.
        self._restore_with_host = False
        self._hiding_with_host = False
        # Placement: centre on the host once, then leave the position alone.
        # Re-centring on every show would undo the drag handle each time the
        # roll comes back from an eyedropper pick.
        self._placed = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle(i18n.tr("鸣谢 · 赞助者名单"))
        self._apply_fixed_size()

        self._build_ui()
        self._apply_theme()

        # Live theme/font follow, same contract as SettingsWindow.
        sidebar = getattr(parent, "sidebar", None)
        if sidebar is not None and hasattr(sidebar, "settingChanged"):
            sidebar.settingChanged.connect(self._apply_theme)

        # Qt does not hide a Qt.Tool child when its parent hides, so mirror the
        # host window explicitly (see the module docstring).
        if parent is not None and hasattr(parent, "installEventFilter"):
            parent.installEventFilter(self)

        # Live language follow, the analogue of the theme follow above. The
        # language combo sits on the same settings page as the button that
        # opens this roll, so switching it while the roll is open is an
        # ordinary thing to do. The listener is held on the instance so it does
        # not outlive the dialog, and the guard below makes a stale entry (the
        # table has no unregister) harmless.
        self._language_listener = self.retranslate
        i18n.add_language_listener(self._language_listener)

    # ── construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._title_bar = _TitleBar(self)
        layout.addWidget(self._title_bar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(10)

        self._lbl_intro = QLabel(i18n.tr(
            "Colorink 完全免费开源。感谢以下朋友的支持，让这个项目走得更远。"))
        self._lbl_intro.setObjectName("SponsorIntro")
        self._lbl_intro.setWordWrap(True)
        body_layout.addWidget(self._lbl_intro)

        self._sponsors = sponsors.sorted_sponsors()

        self._lbl_count = QLabel(_count_text(len(self._sponsors)))
        self._lbl_count.setObjectName("SponsorCount")
        body_layout.addWidget(self._lbl_count)

        self._rows_container = QWidget()
        rows_layout = QVBoxLayout(self._rows_container)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(8)

        for sponsor in self._sponsors:
            row = self._build_row(sponsor)
            rows_layout.addWidget(row.widget)
            self._rows.append(row)

        body_layout.addWidget(self._rows_container)

        self._lbl_empty = QLabel(
            i18n.tr("还没有赞助记录 —— 你可以成为第一位 ☕"))
        self._lbl_empty.setObjectName("SponsorEmpty")
        self._lbl_empty.setWordWrap(True)
        body_layout.addWidget(self._lbl_empty)

        body_layout.addStretch(1)
        self._scroll.setWidget(body)
        layout.addWidget(self._scroll, 1)

        self._show_empty_state(not self._sponsors)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 8, 14, 12)
        footer_layout.setSpacing(8)
        self._btn_sponsor = QPushButton(i18n.tr("前往爱发电赞助"))
        self._btn_sponsor.setObjectName("SponsorAction")
        self._btn_sponsor.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_sponsor.clicked.connect(self._on_sponsor)
        footer_layout.addWidget(self._btn_sponsor)
        footer_layout.addStretch(1)
        layout.addWidget(footer)

    def _build_row(self, sponsor: sponsors.Sponsor) -> _Row:
        widget = QWidget()
        row_layout = QHBoxLayout(widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)

        avatar = QLabel()
        avatar.setFixedSize(_AVATAR_SIZE, _AVATAR_SIZE)
        row_layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        # Elided, never wrapping: a wrapped nickname would make this row taller
        # than its neighbours for no benefit (the full text is in the tooltip).
        lbl_name = _ElidedLabel(sponsor.name)
        lbl_name.setObjectName("SponsorName")
        text_layout.addWidget(lbl_name)

        if sponsor.message:
            lbl_message = QLabel(sponsor.message)
            lbl_message.setObjectName("SponsorMessage")
            lbl_message.setWordWrap(True)
            text_layout.addWidget(lbl_message)

        row_layout.addLayout(text_layout, 1)

        if sponsor.date_label:
            lbl_date = QLabel(sponsor.date_label)
            lbl_date.setObjectName("SponsorDate")
            row_layout.addWidget(lbl_date, 0, Qt.AlignmentFlag.AlignTop)

        return _Row(
            widget=widget,
            avatar=avatar,
            name=sponsor.name,
            image_path=sponsors.resolve_avatar(sponsor.avatar),
        )

    def _show_empty_state(self, is_empty: bool):
        self._lbl_count.setVisible(not is_empty)
        self._rows_container.setVisible(not is_empty)
        self._lbl_empty.setVisible(is_empty)

    def retranslate(self):
        """Re-apply every string after a language switch.

        Registered as an i18n listener in ``__init__``. Sponsor names, messages
        and dates are data, not interface text, so they are deliberately left
        alone — a nickname does not become English.
        """
        if sip.isdeleted(self):
            # The listener table has no unregister; a dialog whose C++ half is
            # already gone must not be touched. Reachable only if a stale entry
            # survives collection, which the instance-held reference prevents.
            return
        self.setWindowTitle(i18n.tr("鸣谢 · 赞助者名单"))
        self._title_bar.retranslate()
        self._lbl_intro.setText(i18n.tr(
            "Colorink 完全免费开源。感谢以下朋友的支持，让这个项目走得更远。"))
        self._lbl_count.setText(_count_text(len(self._sponsors)))
        self._lbl_empty.setText(
            i18n.tr("还没有赞助记录 —— 你可以成为第一位 ☕"))
        self._btn_sponsor.setText(i18n.tr("前往爱发电赞助"))
        self._btn_sponsor.setToolTip(i18n.tr("前往爱发电赞助"))

    # ── sizing / placement ────────────────────────────────────────────────

    def _apply_fixed_size(self):
        height = _MAX_HEIGHT
        try:
            screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        except Exception:
            screen = None
        if screen is not None:
            height = max(
                _MIN_HEIGHT,
                min(_MAX_HEIGHT, screen.availableGeometry().height() - 40),
            )
        self.setFixedSize(_DIALOG_WIDTH, height)

    def _center_on_host(self):
        host = self._host
        if host is None or not hasattr(host, "frameGeometry"):
            return
        try:
            geo = host.frameGeometry()
            if geo.width() <= 0:
                return
            screen = QApplication.screenAt(geo.center()) or QApplication.primaryScreen()
            target_x = geo.center().x() - self.width() // 2
            target_y = geo.center().y() - self.height() // 2
            if screen is not None:
                avail = screen.availableGeometry()
                target_x = max(avail.left(), min(target_x, avail.right() - self.width()))
                target_y = max(avail.top(), min(target_y, avail.bottom() - self.height()))
        except Exception:
            return
        self.move(target_x, target_y)

    def showEvent(self, event):
        self._restore_with_host = True
        super().showEvent(event)
        # Size before centring, then never re-centre: ``_apply_fixed_size``
        # asks which screen the dialog is on, and the answer before the move is
        # the stale position (0,0 on the first open) — so the placement pass
        # runs first and the size pass corrects itself on the next show.
        if not self._placed:
            self._center_on_host()
            self._placed = True
        self._apply_fixed_size()

    def hideEvent(self, event):
        # A close the user asked for (× / Esc) ends the intent to come back;
        # a hide forced by the host window does not.
        if not self._hiding_with_host:
            self._restore_with_host = False
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        """Keep the roll in step with the host settings window.

        Hiding the settings window (the eyedropper theme pick does exactly
        this) must take the roll off the screen; bringing it back should bring
        the roll back too, unless the user had already dismissed it.
        """
        if obj is self._host and event is not None:
            event_type = event.type()
            if event_type == QEvent.Type.Hide:
                self._hiding_with_host = True
                try:
                    self.hide()
                finally:
                    self._hiding_with_host = False
            elif event_type == QEvent.Type.Show and self._restore_with_host:
                self.show()
                self.raise_()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    # ── theming ───────────────────────────────────────────────────────────

    def _config(self) -> dict:
        """The live settings dict, wherever this dialog's host keeps it.

        The host is normally the ``SettingsWindow``, which is a shell around the
        sidebar and keeps no config of its own — so the sidebar (which loads it,
        and is what ``SettingsWindow`` itself reads for its own font size) is the
        fallback. Without that fallback the roll silently stayed at 100% while
        every other surface followed the font-size setting.
        """
        for owner in (self._host, getattr(self._host, "sidebar", None)):
            cfg = getattr(owner, "cfg", None)
            if isinstance(cfg, dict):
                return cfg
        return {}

    def _font_factor(self) -> float:
        try:
            return float(self._config().get("fontSize", 100)) / 100.0
        except (TypeError, ValueError):
            return 1.0

    def _colors(self) -> dict:
        """Host palette merged onto the fallback, so every key is present."""
        palette = _fallback_colors()
        sidebar = getattr(self._host, "sidebar", None)
        if sidebar is not None and hasattr(sidebar, "theme_colors"):
            try:
                palette.update({
                    key: value
                    for key, value in dict(sidebar.theme_colors()).items()
                    if value
                })
            except Exception:
                pass
        if not palette.get("bar_muted"):
            palette["bar_muted"] = muted_ink(palette["bar_text"])
        return palette

    def _apply_theme(self):
        palette = _fallback_colors()
        palette.update(self._colors() or {})

        text = palette["bar_text"]
        muted = palette["bar_muted"]
        accent = palette["accent"]
        accent_ink = readable_ink(accent)

        factor = self._font_factor()
        font_size = int(11 * factor)
        small_size = max(9, int(10 * factor))

        self.setStyleSheet(f"""
            SponsorHallDialog {{
                background-color: {palette["bar_bg"]};
            }}
            QWidget {{
                color: {text};
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: {font_size}px;
            }}
            QScrollArea, QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QLabel#SponsorIntro, QLabel#SponsorName {{
                color: {text};
            }}
            QLabel#SponsorCount, QLabel#SponsorDate, QLabel#SponsorEmpty {{
                color: {muted};
                font-size: {small_size}px;
            }}
            QLabel#SponsorMessage {{
                color: {muted};
                font-size: {small_size}px;
            }}
            QPushButton#SponsorAction {{
                background-color: {accent};
                color: {accent_ink};
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
            }}
            QPushButton#SponsorAction:hover {{
                background-color: {QColor(accent).lighter(115).name()};
            }}
            QPushButton#SponsorAction:pressed {{
                background-color: {QColor(accent).darker(115).name()};
            }}
        """)
        self._title_bar.apply_theme(palette)
        self._repaint_avatars(accent, accent_ink)

    def _repaint_avatars(self, surface: str, ink: str):
        for row in self._rows:
            row.avatar.setPixmap(_avatar_pixmap(
                row.name, _AVATAR_SIZE, surface, ink, row.image_path))

    # ── actions ───────────────────────────────────────────────────────────

    def _on_sponsor(self):
        """Open the Afdian page. Failures are ignored, as elsewhere in the app."""
        webbrowser.open(sponsors.AFDIAN_URL)
