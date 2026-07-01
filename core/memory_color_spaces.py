#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import colorsys
import math
from typing import Dict, Optional, Sequence, Tuple

SPACE_ORDER: Tuple[str, ...] = ("rgb", "cmyk", "hsv", "hls")

CMYK_GCR_SETTINGS = {
    "kStartL": 65.0,
    "kMaxL": 35.0,
    "lightExp": 1.2,
    "chromaRef": 80.0,
    "satExp": 2.0,
    "tac": 3.0,
}

SPACE_DEFINITIONS = {
    "rgb": {
        "channels": ("r", "g", "b"),
        "maxima": (255, 255, 255),
        "relative_offsets": (0x00, 0x04, 0x08),
    },
    "cmyk": {
        "channels": ("c", "m", "y", "k"),
        "maxima": (100, 100, 100, 100),
        "relative_offsets": (0x0C, 0x10, 0x14, 0x18),
    },
    "hsv": {
        "channels": ("h", "s", "v"),
        "maxima": (360, 100, 100),
        "relative_offsets": (0x1C, 0x20, 0x24),
    },
    "hls": {
        "channels": ("h", "l", "s"),
        "maxima": (360, 100, 100),
        "relative_offsets": (0x28, 0x2C, 0x30),
    },
}


def limit_byte_val(v: float) -> int:
    return max(0, min(255, int(round(v))))


def limit_percent_val(v: float) -> int:
    return max(0, min(100, int(round(v))))


def limit_hue_val(v: float) -> int:
    return max(0, min(360, int(round(v))))


def _cap_val(v: float, upper_bound: float) -> float:
    return max(0.0, min(float(upper_bound), float(v)))


def encode_scaled_u32(value: float, max_value: float) -> int:
    if max_value <= 0:
        return 0
    ratio = _cap_val(value, max_value) / float(max_value)
    raw = int(round(ratio * 0xFFFFFFFF))
    return max(0, min(0xFFFFFFFF, raw))


def decode_scaled_u32(raw: int, max_value: float) -> int:
    if max_value <= 0:
        return 0
    val = (int(raw) & 0xFFFFFFFF) / 0xFFFFFFFF * float(max_value)
    return int(round(val))


def build_space_offsets(rgb_base_offset: int) -> Dict[str, Tuple[int, ...]]:
    return {
        name: tuple(int(rgb_base_offset) + r for r in spec["relative_offsets"])
        for name, spec in SPACE_DEFINITIONS.items()
    }


def build_space_addresses(rgb_base_address: int) -> Dict[str, Tuple[int, ...]]:
    return {
        name: tuple(int(rgb_base_address) + r for r in spec["relative_offsets"])
        for name, spec in SPACE_DEFINITIONS.items()
    }


def decode_space_raws(space_name: str, raws: Sequence[int]) -> Dict[str, int]:
    spec = SPACE_DEFINITIONS[space_name]
    return {
        channel: decode_scaled_u32(raw, m)
        for channel, raw, m in zip(spec["channels"], raws, spec["maxima"])
    }


def encode_space_values(space_name: str, values: Dict[str, int]) -> Tuple[int, ...]:
    spec = SPACE_DEFINITIONS[space_name]
    return tuple(
        encode_scaled_u32(values[channel], m)
        for channel, m in zip(spec["channels"], spec["maxima"])
    )


def space_has_nonzero_raws(raws: Sequence[int]) -> bool:
    return any((int(raw) & 0xFFFFFFFF) != 0 for raw in raws)


def normalize_hue_for_colorsys(h: int) -> float:
    h_int = limit_hue_val(h)
    return 0.0 if h_int >= 360 else (h_int / 360.0)


def rgb_to_hsv_values(rgb: Dict[str, int]) -> Dict[str, int]:
    r = limit_byte_val(rgb["r"]) / 255.0
    g = limit_byte_val(rgb["g"]) / 255.0
    b = limit_byte_val(rgb["b"]) / 255.0
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return {
        "h": limit_hue_val(h * 360.0),
        "s": limit_percent_val(s * 100.0),
        "v": limit_percent_val(v * 100.0),
    }


def hsv_to_rgb_values(values: Dict[str, int]) -> Dict[str, int]:
    r, g, b = colorsys.hsv_to_rgb(
        normalize_hue_for_colorsys(values["h"]),
        limit_percent_val(values["s"]) / 100.0,
        limit_percent_val(values["v"]) / 100.0,
    )
    return {
        "r": limit_byte_val(r * 255.0),
        "g": limit_byte_val(g * 255.0),
        "b": limit_byte_val(b * 255.0),
    }


def rgb_to_hls_values(rgb: Dict[str, int]) -> Dict[str, int]:
    r = limit_byte_val(rgb["r"]) / 255.0
    g = limit_byte_val(rgb["g"]) / 255.0
    b = limit_byte_val(rgb["b"]) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return {
        "h": limit_hue_val(h * 360.0),
        "l": limit_percent_val(l * 100.0),
        "s": limit_percent_val(s * 100.0),
    }


def hls_to_rgb_values(values: Dict[str, int]) -> Dict[str, int]:
    r, g, b = colorsys.hls_to_rgb(
        normalize_hue_for_colorsys(values["h"]),
        limit_percent_val(values["l"]) / 100.0,
        limit_percent_val(values["s"]) / 100.0,
    )
    return {
        "r": limit_byte_val(r * 255.0),
        "g": limit_byte_val(g * 255.0),
        "b": limit_byte_val(b * 255.0),
    }


def _rgb_to_xyz_d65(r8: int, g8: int, b8: int) -> Tuple[float, float, float]:
    r = limit_byte_val(r8) / 255.0
    g = limit_byte_val(g8) / 255.0
    b = limit_byte_val(b8) / 255.0
    r = math.pow((r + 0.055) / 1.055, 2.4) if r > 0.04045 else (r / 12.92)
    g = math.pow((g + 0.055) / 1.055, 2.4) if g > 0.04045 else (g / 12.92)
    b = math.pow((b + 0.055) / 1.055, 2.4) if b > 0.04045 else (b / 12.92)
    x = (r * 0.4124564390896922 + g * 0.357576077643909 + b * 0.18043748326639894) * 100.0
    y = (r * 0.21267285140562253 + g * 0.715152155287818 + b * 0.07217499330655958) * 100.0
    z = (r * 0.019330818715591851 + g * 0.11919477979462598 + b * 0.9505321522496607) * 100.0
    return x, y, z


def _xyz_d65_to_d50(x: float, y: float, z: float) -> Tuple[float, float, float]:
    return (
        1.0478112 * x + 0.0228866 * y - 0.0501270 * z,
        0.0295424 * x + 0.9904844 * y - 0.0170491 * z,
        -0.0092345 * x + 0.0150436 * y + 0.7521316 * z,
    )


def _xyz_d50_to_lab_d50(x: float, y: float, z: float) -> Tuple[float, float, float]:
    x /= 96.422
    y /= 100.0
    z /= 82.521
    delta = 6.0 / 29.0
    delta3 = delta * delta * delta

    def f(t: float) -> float:
        return math.pow(t, 1.0 / 3.0) if t > delta3 else (t / (3.0 * delta * delta) + 4.0 / 29.0)

    fx = f(x)
    fy = f(y)
    fz = f(z)
    l = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return (
        max(0.0, min(100.0, round(l * 100.0) / 100.0)),
        max(-128.0, min(127.0, round(a * 100.0) / 100.0)),
        max(-128.0, min(127.0, round(b * 100.0) / 100.0)),
    )


def rgb_to_lab_values(rgb: Dict[str, int]) -> Dict[str, float]:
    x65, y65, z65 = _rgb_to_xyz_d65(rgb["r"], rgb["g"], rgb["b"])
    x50, y50, z50 = _xyz_d65_to_d50(x65, y65, z65)
    l, a, b = _xyz_d50_to_lab_d50(x50, y50, z50)
    return {"l": l, "a": a, "b": b}


def _compute_cmyk_k_fraction_from_rgb(rgb: Dict[str, int]) -> float:
    lab = rgb_to_lab_values(rgb)
    cab = math.sqrt(lab["a"] * lab["a"] + lab["b"] * lab["b"])
    hsv = rgb_to_hsv_values(rgb)
    sat = min(1.0, max(0.0, hsv["s"] / 100.0))

    k_start_l = CMYK_GCR_SETTINGS["kStartL"]
    k_max_l = CMYK_GCR_SETTINGS["kMaxL"]
    light_exp = CMYK_GCR_SETTINGS["lightExp"]
    chroma_ref = CMYK_GCR_SETTINGS["chromaRef"]
    sat_exp = CMYK_GCR_SETTINGS["satExp"]

    light_factor = 0.0
    if lab["l"] < k_start_l:
        t = min(1.0, max(0.0, (k_start_l - lab["l"]) / max(1.0, (k_start_l - k_max_l))))
        light_factor = math.pow(t, light_exp)

    chroma_suppress = min(1.0, max(0.0, 1.0 - cab / chroma_ref))
    sat_suppress = math.pow(1.0 - sat, sat_exp)
    return min(1.0, max(0.0, light_factor * max(chroma_suppress, sat_suppress)))


def rgb_to_cmyk_values(rgb: Dict[str, int]) -> Dict[str, int]:
    r = limit_byte_val(rgb["r"])
    g = limit_byte_val(rgb["g"])
    b = limit_byte_val(rgb["b"])
    rn = r / 255.0
    gn = g / 255.0
    bn = b / 255.0
    cp = 1.0 - rn
    mp = 1.0 - gn
    yp = 1.0 - bn
    gray = min(cp, mp, yp)
    k = gray * _compute_cmyk_k_fraction_from_rgb({"r": r, "g": g, "b": b})
    c_val = max(0.0, cp - k)
    m_val = max(0.0, mp - k)
    y_val = max(0.0, yp - k)
    total = c_val + m_val + y_val + k
    tac = CMYK_GCR_SETTINGS["tac"]
    if total > tac:
        scale = (tac - k) / max(1e-6, (c_val + m_val + y_val))
        c_val *= scale
        m_val *= scale
        y_val *= scale
    return {
        "c": limit_percent_val(c_val * 100.0),
        "m": limit_percent_val(m_val * 100.0),
        "y": limit_percent_val(y_val * 100.0),
        "k": limit_percent_val(k * 100.0),
    }


def cmyk_to_rgb_values(values: Dict[str, int]) -> Dict[str, int]:
    c = limit_percent_val(values["c"]) / 100.0
    m = limit_percent_val(values["m"]) / 100.0
    y = limit_percent_val(values["y"]) / 100.0
    k = limit_percent_val(values["k"]) / 100.0
    return {
        "r": limit_byte_val((1.0 - c) * (1.0 - k) * 255.0),
        "g": limit_byte_val((1.0 - m) * (1.0 - k) * 255.0),
        "b": limit_byte_val((1.0 - y) * (1.0 - k) * 255.0),
    }


def rgb_to_space_values(space_name: str, rgb: Dict[str, int]) -> Dict[str, int]:
    base_rgb = {
        "r": limit_byte_val(rgb["r"]),
        "g": limit_byte_val(rgb["g"]),
        "b": limit_byte_val(rgb["b"]),
    }
    if space_name == "rgb":
        return base_rgb
    if space_name == "cmyk":
        return rgb_to_cmyk_values(base_rgb)
    if space_name == "hsv":
        return rgb_to_hsv_values(base_rgb)
    if space_name == "hls":
        return rgb_to_hls_values(base_rgb)
    raise KeyError(f"unknown color space: {space_name}")


def space_to_rgb_values(space_name: str, values: Dict[str, int]) -> Dict[str, int]:
    if space_name == "rgb":
        return {
            "r": limit_byte_val(values["r"]),
            "g": limit_byte_val(values["g"]),
            "b": limit_byte_val(values["b"]),
        }
    if space_name == "cmyk":
        return cmyk_to_rgb_values(values)
    if space_name == "hsv":
        return hsv_to_rgb_values(values)
    if space_name == "hls":
        return hls_to_rgb_values(values)
    raise KeyError(f"unknown color space: {space_name}")


def resolve_active_rgb(space_snapshots: Dict[str, Dict[str, object]]) -> Tuple[str, Dict[str, int], Dict[str, int]]:
    for space_name in SPACE_ORDER:
        snapshot = space_snapshots.get(space_name)
        if not snapshot:
            continue
        raws = snapshot.get("raws") or ()
        if space_has_nonzero_raws(raws):
            values = snapshot["values"]
            return space_name, space_to_rgb_values(space_name, values), values
    return "rgb", {"r": 0, "g": 0, "b": 0}, {"r": 0, "g": 0, "b": 0}


def any_space_has_nonzero_raws(space_snapshots: Dict[str, Dict[str, object]]) -> bool:
    for snapshot in space_snapshots.values():
        raws = snapshot.get("raws") or ()
        if space_has_nonzero_raws(raws):
            return True
    return False


def format_space_values(space_name: str, values: Dict[str, int]) -> str:
    channels = SPACE_DEFINITIONS[space_name]["channels"]
    return ", ".join(f"{channel.upper()}={int(values[channel])}" for channel in channels)
