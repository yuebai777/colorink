"""Tests for ringless cache invalidation and set_ringless_layout idempotency.

Covers idempotent no-op (caches preserved), repaint on change,
and per-category cache clearing (ring, slice-image, gamut boundary).
"""

from unittest.mock import patch

import pytest

from ui.ringless_mode import RinglessLayout

from .test_ringless_support import (
    canonical_layout,
    disabled_layout,
    make_wheel,
    qapp,
)


# ── Idempotency and repaint ──────────────────────────────────────────────

class TestSetRinglessLayoutIdempotency:
    """set_ringless_layout is idempotent and calls update() on real change."""

    def test_same_layout_preserves_caches_and_state(self, qapp):
        """Identical layout: caches NOT cleared, geometry unchanged."""
        layout = RinglessLayout(
            wheel_enabled=True, controls_enabled=True, controls_side="left",
            control_bar_height=39, margin=7,
            swatch_width=43, swatch_height=24, swatch_gap=5,
            corner_radius=4, button_gap=4,
        )
        w = make_wheel(300, 300, layout)
        # Populate caches with sentinel values (no paintEvent needed)
        w._cached_img_key = "before"
        w._cached_hls_key = "before"
        w._cached_rgb_key = "before"
        w._cached_oklch_key = "before"
        w._bdry_h = 180.0
        sg1 = w.get_slice_geometry()

        w.set_ringless_layout(layout)  # same layout again
        sg2 = w.get_slice_geometry()

        assert sg1 == sg2
        assert w._cached_img_key == "before"
        assert hasattr(w, "_cached_hls_key")
        assert hasattr(w, "_cached_rgb_key")
        assert hasattr(w, "_cached_oklch_key")
        assert hasattr(w, "_bdry_h")

    def test_layout_change_requests_repaint(self, qapp):
        """Real layout change calls update().  Duplicate assignment does not."""
        w = make_wheel(300, 300, disabled_layout())

        with patch.object(w, "update", wraps=w.update) as spy_update:
            w.set_ringless_layout(canonical_layout())
            spy_update.assert_called_once()

            # Same layout again → no additional update
            spy_update.reset_mock()
            w.set_ringless_layout(canonical_layout())
            spy_update.assert_not_called()


# ── Cache invalidation per category ──────────────────────────────────────

class TestCacheInvalidation:
    """set_ringless_layout clears geometry-dependent caches on layout change."""

    def test_layout_change_invalidates_ring_cache(self, qapp):
        """Seed _cached_ring_key, assert precondition, change layout, assert removal."""
        w = make_wheel(300, 300, disabled_layout())
        w._cached_ring_key = ("dummy",)  # sentinel
        assert hasattr(w, "_cached_ring_key"), "precondition"

        w.set_ringless_layout(canonical_layout())
        assert not hasattr(w, "_cached_ring_key")

    def test_layout_change_invalidates_slice_caches(self, qapp):
        w = make_wheel(300, 300)
        w._cached_img_key = "dummy"
        w._cached_hls_key = "dummy"
        w._cached_rgb_key = "dummy"
        w._cached_oklch_key = "dummy"

        w.set_ringless_layout(canonical_layout())

        assert w._cached_img_key is None
        assert not hasattr(w, "_cached_hls_key")
        assert not hasattr(w, "_cached_rgb_key")
        assert not hasattr(w, "_cached_oklch_key")

    def test_layout_change_invalidates_oklch_boundary_cache(self, qapp):
        w = make_wheel(300, 300)
        w._bdry_h = 180.0  # sentinel

        w.set_ringless_layout(canonical_layout())

        assert not hasattr(w, "_bdry_h")
