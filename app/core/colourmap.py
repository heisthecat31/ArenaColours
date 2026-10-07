"""The one colour transform every layer shares: Echo's team blue/cyan and team orange
are pulled onto the user's two chosen colours; everything else only gets the global
saturation change.

The same maths runs in the runtime plugin (plugin/arenacolours.cpp), so baked files and
colours the game sets while it runs agree.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass, field

import numpy as np


def hex_to_rgb(text: str) -> tuple:
    text = text.lstrip('#')
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return '#%02x%02x%02x' % tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb)


@dataclass
class Band:
    centre: float        # source hue (degrees)
    half: float          # full strength within +-half
    fade: float          # then fades out over this many degrees
    spread: float        # how much of the source hue variation survives (0 = all onto the target)
    boost: float         # value multiplier inside the band (1 + boost)


#: Echo's blues/cyans sit 177-247 deg; its oranges are red-orange (~11 deg) to orange (~35 deg).
BLUE_BAND = Band(212.0, 35.0, 8.0, 0.25, 0.40)
ORANGE_BAND = Band(25.0, 17.0, 6.0, 0.30, 0.25)


@dataclass
class ColourSettings:
    blue_target: str = '#ff38b8'      # what the blue team becomes
    orange_target: str = '#3dff38'    # what the orange team becomes
    saturation: float = 1.5           # 1 = unchanged
    brightness: float = 1.0           # lightmap multiplier, 1 = stock

    def targets(self):
        out = []
        for band, colour in ((BLUE_BAND, self.blue_target), (ORANGE_BAND, self.orange_target)):
            h, s, v = colorsys.rgb_to_hsv(*hex_to_rgb(colour))
            out.append((band, h * 360.0, max(s, 0.05), max(v, 0.05)))
        return out

    def plugin_ini(self) -> str:
        b = ' '.join('%.3f' % c for c in hex_to_rgb(self.blue_target))
        o = ' '.join('%.3f' % c for c in hex_to_rgb(self.orange_target))
        return ('[ArenaColours]\r\nEnabled=1\r\nBlueTarget=%s\r\nOrangeTarget=%s\r\n'
                'Saturation=%.3f\r\nLog=0\r\n' % (b, o, self.saturation))


def recolour(rgb: np.ndarray, cs: ColourSettings, ldr: bool = True) -> np.ndarray:
    """`rgb` (..., 3) float, any range -> recoloured copy. Value is kept (boosted in-band)."""
    rgb = np.asarray(rgb, np.float32)
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    c = mx - mn
    safe = np.where(c > 0, c, 1)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h = np.where(mx == r, ((g - b) / safe) % 6,
                 np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)) * 60.0
    h = np.where(c > 0, h, 0.0)
    s = np.where(mx > 0, c / np.where(mx > 0, mx, 1), 0.0)
    v = mx.copy()
    h0 = h.copy()
    wsum = np.zeros_like(h)
    cap = np.ones_like(h)
    for band, target_hue, target_sat, target_val in cs.targets():
        dh = (h0 - band.centre + 180.0) % 360.0 - 180.0
        w = np.clip((band.half + band.fade - np.abs(dh)) / band.fade, 0.0, 1.0) * np.clip(s / 0.15, 0.0, 1.0)
        tgt = (target_hue + dh * band.spread) % 360.0
        diff = (tgt - h + 180.0) % 360.0 - 180.0
        h = (h + w * diff) % 360.0
        # the band takes the target's brightness too (a dark red stays dark), plus the band boost
        v = v * (1.0 + w * (target_val * (1.0 + band.boost) - 1.0))
        cap = np.where(w > 0, np.minimum(cap, target_sat), cap)
        wsum = wsum + w
    if ldr:
        v = np.minimum(v, 1.0)   # clipping channels instead would skew the target hue
    s = np.clip(s * cs.saturation, 0.0, 1.0)
    w = np.clip(wsum, 0.0, 1.0)
    s = s + w * (np.minimum(s, cap) - s)
    hp = h / 60.0
    x = 1 - np.abs(hp % 2 - 1)
    z = np.zeros_like(hp)
    o = np.ones_like(hp)
    i = np.floor(hp).astype(int) % 6
    lut = [(o, x, z), (x, o, z), (z, o, x), (z, x, o), (x, z, o), (o, z, x)]
    out = np.zeros_like(rgb)
    for k, comps in enumerate(lut):
        m = i == k
        for ch, comp in enumerate(comps):
            out[..., ch] = np.where(m, comp, out[..., ch])
    chroma = (v * s)[..., None]
    return out * chroma + (v[..., None] - chroma)


def is_team_coloured(rgb) -> bool:
    """A blue/cyan or orange colour (used where only team colours may change, e.g. UI)."""
    mx = float(max(rgb))
    if mx <= 0.05:
        return False
    h, s, _v = colorsys.rgb_to_hsv(*(float(x) / mx for x in rgb))
    h *= 360
    return s > 0.2 and (165 <= h <= 255 or 2 <= h <= 48)
