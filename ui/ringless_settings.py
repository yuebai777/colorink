"""Ringless-mode settings widget for the settings sidebar.

Owns a checkbox (enable ringless mode), a control-bar position row
(top/bottom), and a side-selector row (left/right controls placement).
Emits one ``changed`` signal on user interaction;
``set_config()`` blocks signals internally so refresh does not persist
unexpectedly.
"""

from typing import Final

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.ringless_mode import ControlBarPosition, ControlsSide, RinglessConfig

_SIDE_LABEL: Final[str] = "无色环双色位置"
_SIDE_ITEMS: Final[list[tuple[str, ControlsSide]]] = [
    ("左侧", "left"),
    ("右侧", "right"),
]


class RinglessSettingsWidget(QWidget):
    """Composite widget: checkbox + side-row (label + combo).

    The side row is wrapped in a ``QWidget`` so visibility methods never
    operate on a QLayout.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ── public API ──────────────────────────────────────────────────────

    _INDEX_TO_SIDE: Final[dict[int, ControlsSide]] = {0: "left", 1: "right"}
    _INDEX_TO_POSITION: Final[dict[int, ControlBarPosition]] = {0: "top", 1: "bottom"}

    def config(self) -> RinglessConfig:
        """Read current widget state as a typed RinglessConfig."""
        side = self._INDEX_TO_SIDE.get(
            self.side_combo.currentIndex(), "right"
        )
        position = self._INDEX_TO_POSITION.get(
            self.control_bar_position_combo.currentIndex(), "top"
        )
        return RinglessConfig(
            enabled=self.enabled_checkbox.isChecked(),
            controls_side=side,
            control_bar_position=position,
        )

    def set_config(self, config: RinglessConfig) -> None:
        """Push a RinglessConfig into the widget without emitting ``changed``."""
        self.enabled_checkbox.blockSignals(True)
        self.control_bar_position_combo.blockSignals(True)
        self.side_combo.blockSignals(True)

        self.enabled_checkbox.setChecked(config.enabled)
        self._select_position_item(config.control_bar_position)
        self._select_combo_item(config.controls_side)
        self.control_bar_position_row.setVisible(config.enabled)
        self.side_row_wrapper.setVisible(config.enabled)

        self.enabled_checkbox.blockSignals(False)
        self.control_bar_position_combo.blockSignals(False)
        self.side_combo.blockSignals(False)

    # ── internal ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # -- checkbox -----------------------------------------------------
        self.enabled_checkbox = QCheckBox("隐藏色相环并放大切片")
        self.enabled_checkbox.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(self.enabled_checkbox)

        # -- control-bar position row ---------------------------------------
        self.control_bar_position_row = QWidget()
        position_layout = QHBoxLayout(self.control_bar_position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(6)
        position_layout.addWidget(QLabel("切片控制栏位置"))
        self.control_bar_position_combo = QComboBox()
        self.control_bar_position_combo.addItem("上方", "top")
        self.control_bar_position_combo.addItem("下方", "bottom")
        self.control_bar_position_combo.currentIndexChanged.connect(
            self._on_control_bar_position_changed
        )
        position_layout.addWidget(self.control_bar_position_combo)
        position_layout.addStretch()
        layout.addWidget(self.control_bar_position_row)

        # -- side row (wrapped in QWidget) --------------------------------
        self.side_row_wrapper = QWidget()
        row = QHBoxLayout(self.side_row_wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.side_label = QLabel(_SIDE_LABEL)
        row.addWidget(self.side_label)
        self.side_combo = QComboBox()
        for display, key in _SIDE_ITEMS:
            self.side_combo.addItem(display, key)
        self.side_combo.currentIndexChanged.connect(self._on_side_changed)
        row.addWidget(self.side_combo)
        row.addStretch()

        layout.addWidget(self.side_row_wrapper)

        # initial state: disabled → hide side row; combo defaults to "right"
        self._select_combo_item("right")
        self._select_position_item("top")
        self.control_bar_position_row.setVisible(False)
        self.side_row_wrapper.setVisible(False)

    def _select_combo_item(self, side: ControlsSide) -> None:
        idx = self.side_combo.findData(side)
        if idx >= 0:
            self.side_combo.setCurrentIndex(idx)

    def _on_checkbox_changed(self, _state: int) -> None:
        enabled = self.enabled_checkbox.isChecked()
        self.control_bar_position_row.setVisible(enabled)
        self.side_row_wrapper.setVisible(enabled)
        self.changed.emit()

    def _select_position_item(self, position: ControlBarPosition) -> None:
        idx = self.control_bar_position_combo.findData(position)
        if idx >= 0:
            self.control_bar_position_combo.setCurrentIndex(idx)

    def _on_control_bar_position_changed(self, _index: int) -> None:
        self.changed.emit()

    def _on_side_changed(self, _index: int) -> None:
        self.changed.emit()
