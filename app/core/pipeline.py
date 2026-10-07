"""ArenaColours build: recolour mpl_arena_a + mpl_lobby_b_arena and install it, touching no other map.

Passes (all read STOCK resources through core.package, input-pcvr first):
  1. discover   our levels' archives, level-keyed files, materials, models, placed canvases
  2. textures   every candidate colour texture our levels use -> recoloured, made resident
  3. materials  tint/colour properties (glow, rim, base tints)
  4. canvases   UI element colours (scoreboards, score displays)
  5. lights     placed lights + light volumes in our two level scenes
  6. probes     reflection cubemaps (every mip, in place)
  7. lightmap   the arena's baked GI page: recoloured, scaled by `brightness`
  8. clone      re-key everything shared under new names, rewire only our levels
  9. install    merge with input-pcvr, back up once, repack, install the runtime plugin
"""
from __future__ import annotations

import bisect
import concurrent.futures as cf
import json
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR / 'vendor') not in sys.path:
    sys.path.insert(0, str(APP_DIR / 'vendor'))

import evr_level_map as LM               # noqa: E402
import evr_lights as EL                  # noqa: E402
import evr_material_resource as MR       # noqa: E402
import evr_texture_resource as TR        # noqa: E402

from .colourmap import ColourSettings, recolour, is_team_coloured, hex_to_rgb   # noqa: E402
from .package import (EchoInstall, Package, TEXCONV, PLUGIN_DLL, NO_WINDOW, merge_dirs,  # noqa: E402
                      norm)

TARGETS = json.loads((APP_DIR / 'data' / 'targets.json').read_text())

LEVELS = ['576ed3f8428ebc4b', '6daa00a6d33d44b7']        # mpl_arena_a, mpl_lobby_b_arena
ARENA = LEVELS[0]

T_TEX, T_TEXGPU, T_PACK = '4a4c32c49300b8a0', 'beac1969cb7b8861', 'ae49fad43254367a'
T_MAT, T_SHD, T_SHDGPU = 'e3e0266f1911dafa', '4984e2bbb2ddb256', '43ddbd692282a6a7'
T_MODEL, T_MODELGPU, T_VIS, T_TS, T_PHYS = ('37102e4b27955a14', 'e7a8ab5ceaef49cb', '73d312a620da3824',
                                            'c2434c5a99e139ce', 'b7d338793fa37832')
T_CANVAS, T_CANVASCR, T_SHAREDCR = '59a9bd6e4525ecc4', '822fd4ccb42e8a3c', 'dab7dce1df894ef6'
T_ARCHIVE, T_SCENE, T_PROBEGPU = '2a41cf1c1d9e5d32', 'a388ea69e5108f4c', '5004f0b9f6645271'
T_TEXOVR = '4127ff2ffe6be26a'

M64 = (1 << 64) - 1
FMT = {71: 'BC1_UNORM', 72: 'BC1_UNORM_SRGB', 77: 'BC3_UNORM', 78: 'BC3_UNORM_SRGB',
       98: 'BC7_UNORM', 99: 'BC7_UNORM_SRGB', 95: 'BC6H_UF16', 26: 'R11G11B10_FLOAT'}
SRGB = {72, 78, 99}
FLOAT_FMTS = {95, 26}
CANVAS_BASE, CANVAS_STRIDE, CANVAS_COUNT, CANVAS_SIZE = 568, 224, 0x28, 0x14
PROBE_BYTES = 524448                      # one 256px BC6H cube, 9 mips


# ----------------------------------------------------------------------------- hashing
def _seeds():
    mask, out = 0x95AC9329AC4BC9B5, []
    for i in range(256):
        v = 0x2B5926535897936A if (i & 0x80) else 0
        if i & 0x40:
            v ^= mask
        shift = 0x20
        while shift:
            v = (2 * v) & M64
            if i & shift:
                v ^= mask
            shift >>= 1
        out.append((2 * v) & M64)
    return out


SEEDS = _seeds()


def physics_name(model: int) -> int:
    """Instance physics is named M^14 * model ^ K (CRC-64 affine)."""
    h = model
    for _ in range(14):
        h = (SEEDS[(h >> 56) & 0xFF] ^ (h << 8)) & M64
    return h ^ 0x5F3AFFE712DD1399


def refs(blob: bytes, hashes) -> set:
    return {h for h in hashes if int(h, 16).to_bytes(8, 'little') in blob}


def sgn(x: int) -> int:
    return x - (1 << 64) if x >= 1 << 63 else x


class Cancelled(Exception):
    pass


# ----------------------------------------------------------------------------- pipeline
class Builder:
    def __init__(self, install: EchoInstall, input_dir: Path | None, workspace: Path,
                 colours: ColourSettings, log=print, progress=lambda f, msg='': None,
                 cancelled=lambda: False):
        self.install = install
        self.input_dir = input_dir
        self.ws = workspace
        self.cs = colours
        self.log = log
        self.progress = progress
        self.cancelled = cancelled
        self.pkg = Package(install, workspace / 'cache', input_dir, log)
        self.pool = workspace / 'pool'         # recoloured, stock names
        self.stage = workspace / 'stage'       # arena-only output
        self.work = workspace / 'work'

    def check(self):
        if self.cancelled():
            raise Cancelled()

    # ---------------------------------------------------------------- top level
    def build(self) -> Path:
        t0 = time.time()
        for d in (self.pool, self.stage, self.work):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
        self.progress(0.01, 'Reading the arena from your Echo install')
        self.discover()
        self.progress(0.08, 'Recolouring textures')
        self.pass_textures(0.08, 0.55)
        self.progress(0.55, 'Recolouring materials')
        self.pass_materials()
        self.progress(0.60, 'Recolouring scoreboards and UI')
        self.pass_canvases()
        self.progress(0.64, 'Recolouring lights')
        self.pass_lights()
        self.progress(0.67, 'Recolouring reflections')
        self.pass_probes()
        self.progress(0.80, 'Relighting the arena')
        self.pass_lightmap()
        self.progress(0.88, 'Making it arena-only')
        self.clone()
        self.log('build finished in %.0f s' % (time.time() - t0))
        return self.stage

    # ---------------------------------------------------------------- 1. discover
    def discover(self):
        pkg = self.pkg
        pkg.fetch([(T_ARCHIVE, lv) for lv in LEVELS])
        self.records = set()
        for lv in LEVELS:
            blob = pkg.read(T_ARCHIVE, lv)
            if blob is None:
                raise RuntimeError('level %s is missing from this install' % lv)
            self.records |= {(norm(t), norm(n)) for t, n in LM.read_archive(blob)[1]}
        self.by_type = {}
        for t, n in self.records:
            self.by_type.setdefault(t, set()).add(n)
        self.level_files = pkg.find(LEVELS)
        pkg.fetch(self.level_files)
        self.materials = sorted(self.by_type.get(T_MAT, ()))
        self.models = sorted(self.by_type.get(T_MODEL, ()))
        self.shadersets = sorted(self.by_type.get(T_SHD, ()))
        pkg.fetch([(T_MAT, m) for m in self.materials] + [(T_MODEL, m) for m in self.models] +
                  [(T_SHD, s) for s in self.shadersets])
        # canvases our levels place
        all_canvases = set(pkg.list_type(T_CANVAS))
        placed = set()
        for t, n in self.level_files:
            if t in (T_CANVASCR, T_SHAREDCR):
                placed |= refs(pkg.read(t, n) or b'', all_canvases)
        self.canvases = sorted(placed)
        pkg.fetch([(T_CANVAS, c) for c in self.canvases])
        # candidate colour textures: the known colour set, restricted to what our levels use
        known = set(TARGETS['textures'])
        used = set()
        for m in self.materials:
            used |= refs(pkg.read(T_MAT, m) or b'', known)
        for c in self.canvases:
            used |= refs(pkg.read(T_CANVAS, c) or b'', known)
        self.textures = sorted(used)
        self.log('levels: %d archive records, %d level files, %d materials, %d models, '
                 '%d canvases, %d colour textures'
                 % (len(self.records), len(self.level_files), len(self.materials), len(self.models),
                    len(self.canvases), len(self.textures)))

    # ---------------------------------------------------------------- 2. textures
    def _texconv(self, *args):
        r = subprocess.run([str(TEXCONV), '-nologo', '-y', *map(str, args)], capture_output=True,
                           text=True, creationflags=NO_WINDOW)
        if r.returncode:
            raise RuntimeError((r.stdout + r.stderr)[-300:])

    def _fetch_texture_sources(self, names):
        pkg = self.pkg
        pkg.fetch([(t, n) for n in names for t in (T_TEX, T_TEXGPU)])
        packs = []
        for n in names:
            d = pkg.read(T_TEX, n)
            if d is not None and struct.unpack_from('<I', d, 0xC0)[0] != 1:
                packs += [(T_PACK, h) for h in TR._legacy_high_res_hashes(d)]
        pkg.fetch(packs)

    def recolour_texture(self, h: str):
        self.check()
        pkg = self.pkg
        root = pkg.root_for(T_TEX, h)
        if root is None:
            return h, 'missing'
        desc = pkg.read(T_TEX, h)[:256]
        blob, note = TR.rebuild_dds(root, h)
        if not blob and root != pkg.cache:
            blob, note = TR.rebuild_dds(pkg.cache, h)
        if not blob:
            return h, 'no dds'
        hh, w = struct.unpack_from('<II', blob, 12)
        mips = struct.unpack_from('<I', blob, 28)[0] or 1
        dx = struct.unpack_from('<I', blob, 128)[0] if blob[84:88] == b'DX10' else -1
        mw, mh, mm = struct.unpack_from('<III', desc, 0xC4)
        if (w, hh, mips) != (mw, mh, mm) or dx not in FMT:
            return h, 'skipped'
        d = self.work / 'tex' / h
        for sub in ('dec', 'enc'):
            (d / sub).mkdir(parents=True, exist_ok=True)
        (d / 'src.dds').write_bytes(blob)
        floaty = dx in FLOAT_FMTS
        self._texconv('-f', 'R16G16B16A16_FLOAT' if floaty else ('R8G8B8A8_UNORM_SRGB' if dx in SRGB else 'R8G8B8A8_UNORM'),
                      '-m', '1', '-dx10', '-o', d / 'dec', d / 'src.dds')
        raw = (d / 'dec' / 'src.dds').read_bytes()
        if floaty:
            px = np.frombuffer(raw[148:], np.float16).reshape(-1, 4).astype(np.float32)
            px[:, :3] = recolour(np.maximum(px[:, :3], 0)[None], self.cs, ldr=False)[0]
            body = px.astype(np.float16).tobytes()
        else:
            px = np.frombuffer(raw[148:], np.uint8).reshape(-1, 4).astype(np.float32) / 255.0
            px[:, :3] = recolour(px[:, :3][None], self.cs)[0]
            body = np.rint(np.clip(px, 0, 1) * 255).astype(np.uint8).tobytes()
        (d / 'col.dds').write_bytes(raw[:148] + body)
        self._texconv('-f', FMT[dx], '-m', mips, '-dx10', '-o', d / 'enc', d / 'col.dds')
        new = (d / 'enc' / 'col.dds').read_bytes()
        if len(new) != len(blob):
            return h, 'size mismatch'
        nd = bytearray(desc)
        nd[0:0xC0] = b'\xff' * 0xC0                      # resident: no packfile layout
        struct.pack_into('<I', nd, 0xC0, 1)
        struct.pack_into('<III', nd, 0xE8, w, hh, mips)
        struct.pack_into('<II', nd, 0xF4, len(new), struct.unpack_from('<I', new, 20)[0])
        self._put(self.pool, T_TEX, h, bytes(nd))
        self._put(self.pool, T_TEXGPU, h, new)
        shutil.rmtree(d, ignore_errors=True)
        return h, 'ok'

    def pass_textures(self, p0, p1):
        self._fetch_texture_sources(self.textures)
        done = 0
        results = {}
        with cf.ThreadPoolExecutor(4) as ex:
            futs = [ex.submit(self.recolour_texture, h) for h in self.textures]
            for f in cf.as_completed(futs):
                try:
                    h, r = f.result()
                except Cancelled:
                    raise
                except Exception as e:      # one bad texture never stops the build
                    h, r = '?', 'error: %s' % e
                results[h] = r
                done += 1
                self.progress(p0 + (p1 - p0) * done / max(1, len(self.textures)),
                              'Recolouring textures (%d/%d)' % (done, len(self.textures)))
        ok = sum(1 for r in results.values() if r == 'ok')
        self.log('textures: %d recoloured, %d left as they are' % (ok, len(results) - ok))

    # ---------------------------------------------------------------- 3. materials
    def pass_materials(self):
        named = set(TARGETS['colour_props_named']) | set(TARGETS['colour_props_unnamed'])
        props = {int(h, 16) for h in named}
        changed = 0
        for m in self.materials:
            self.check()
            data = bytearray(self.pkg.read(T_MAT, m))
            words, slots = MR.parse_material_prop_slots(bytes(data))
            if not slots:
                continue
            base = MR.parse_header(bytes(data)).payload_offsets['materialprops']
            seen, n = set(), 0
            for h, i in slots.items():
                if h not in props or i in seen or i + 3 > len(words):
                    continue
                seen.add(i)
                c = np.array(words[i:i + 3], np.float32)
                if not np.all((c >= 0) & (c <= 64)) or c.max() < 0.05:
                    continue
                new = recolour(c[None, None, :], self.cs, ldr=False)[0, 0]
                if np.abs(new - c).max() > 0.02:
                    struct.pack_into('<3f', data, base + 4 * i, *map(float, new))
                    n += 1
            if n:
                self._put(self.pool, T_MAT, m, bytes(data))
                changed += 1
        self.log('materials: %d with recoloured tints' % changed)

    # ---------------------------------------------------------------- 4. canvases
    @staticmethod
    def _element_like(data, e, w, h):
        x0, y0, x1, y1 = struct.unpack_from('<4f', data, e + 0x6C)
        a = struct.unpack_from('<f', data, e + 0xD0 + 12)[0]
        if not np.all(np.isfinite([x0, y0, x1, y1, a])):
            return False
        return (x0 < x1 and y0 < y1 and -w <= x0 <= 2 * w and -h <= y0 <= 2 * h
                and x1 - x0 <= 4 * w and y1 - y0 <= 4 * h and 0.0 <= a <= 1.0001)

    def pass_canvases(self):
        changed = 0
        for c in self.canvases:
            self.check()
            data = bytearray(self.pkg.read(T_CANVAS, c))
            if len(data) < CANVAS_BASE:
                continue
            declared = struct.unpack_from('<I', data, CANVAS_COUNT)[0]
            w, h = struct.unpack_from('<2I', data, CANVAS_SIZE)
            hit = 0
            for i in range(-1, (len(data) - CANVAS_BASE) // CANVAS_STRIDE):
                e = CANVAS_BASE + i * CANVAS_STRIDE
                if e < 0 or e + CANVAS_STRIDE > len(data):
                    continue
                first_table = 0 <= i < declared
                if not first_table and not self._element_like(data, e, w, h):
                    continue
                col = np.array(struct.unpack_from('<4f', data, e + 0xD0), np.float32)
                if np.all((col >= 0) & (col <= 1.0001)) and is_team_coloured(col[:3]):
                    new = recolour(col[None, None, :3], self.cs)[0, 0]
                    if np.abs(new - col[:3]).max() > 0.01:
                        struct.pack_into('<3f', data, e + 0xD0, *map(float, np.clip(new, 0, 1)))
                        hit += 1
                if not first_table:
                    continue          # beyond the first table +0xB4 can be a sprite hash
                u = np.array(data[e + 0xB4:e + 0xB7], np.float32) / 255.0
                if is_team_coloured(u):
                    nu = recolour(u[None, None, :], self.cs)[0, 0]
                    if np.abs(nu - u).max() > 0.02:
                        data[e + 0xB4:e + 0xB7] = bytes(np.rint(np.clip(nu, 0, 1) * 255).astype(np.uint8))
                        hit += 1
            if hit:
                self._put(self.pool, T_CANVAS, c, bytes(data))
                changed += 1
        self.log('canvases: %d recoloured' % changed)

    # ---------------------------------------------------------------- 5. lights
    def pass_lights(self):
        total = 0
        for lv in LEVELS:
            data = bytearray(self.pkg.read(T_SCENE, lv) or b'')
            if not data:
                continue
            n = 0

            def fix(off):
                c = np.array(struct.unpack_from('<3f', data, off), np.float32)
                if c.max() <= 0:
                    return 0
                new = recolour(c[None, None, :], self.cs, ldr=False)[0, 0]
                if np.abs(new - c).max() > 0.02:
                    struct.pack_into('<3f', data, off, *map(float, new))
                    return 1
                return 0
            lights = EL.parse_scene_lights(bytes(data))
            n += sum(fix(4 + l.index * EL.LIGHT_STRIDE + EL.L_COLOR) for l in lights)
            raw = self._volume_table(bytes(data))
            if raw:
                cnt, base = raw
                n += sum(fix(base + i * EL.VOLUME_STRIDE + EL.V_COLOR) for i in range(cnt))
            if n:
                self._put(self.pool, T_SCENE, lv, bytes(data))
                total += n
        self.log('lights: %d recoloured' % total)

    @staticmethod
    def _volume_table(data):
        try:
            import cgsceneresource as SR
        except ImportError:
            return None
        try:
            lead = (SR.read(data).get('lead') or [])
        except Exception:
            return None
        if len(lead) < 2 or not isinstance(lead[1], tuple) or not lead[1][0] or not lead[1][1]:
            return None
        cnt, raw = lead[1][0], lead[1][1]
        if len(raw) // cnt != EL.VOLUME_STRIDE or data.count(raw) != 1:
            return None
        return cnt, data.find(raw)

    # ---------------------------------------------------------------- 6. probes
    def pass_probes(self):
        header = bytes.fromhex(TARGETS['probe_header_hex'])
        for lv in LEVELS:
            self.check()
            gpu = self.pkg.read(T_PROBEGPU, lv)
            if not gpu or len(gpu) % PROBE_BYTES:
                continue
            gpu = bytearray(gpu)
            d = self.work / 'probe'
            (d / 'dec').mkdir(parents=True, exist_ok=True)
            (d / 'enc').mkdir(exist_ok=True)
            for i in range(len(gpu) // PROBE_BYTES):
                self.check()
                src = d / 'p.dds'
                src.write_bytes(header + bytes(gpu[i * PROBE_BYTES:(i + 1) * PROBE_BYTES]))
                self._texconv('-f', 'R16G16B16A16_FLOAT', '-dx10', '-o', d / 'dec', src)
                raw = (d / 'dec' / 'p.dds').read_bytes()
                px = np.frombuffer(raw[148:], np.float16).astype(np.float32).reshape(-1, 4)
                px[:, :3] = recolour(np.maximum(px[:, :3], 0)[None], self.cs, ldr=False)[0]
                (d / 'c.dds').write_bytes(raw[:148] + px.astype(np.float16).tobytes())
                self._texconv('-f', 'BC6H_UF16', '-dx10', '-o', d / 'enc', d / 'c.dds')
                new = (d / 'enc' / 'c.dds').read_bytes()
                if len(new) - 148 != PROBE_BYTES:
                    raise RuntimeError('reflection probe re-encode changed size')
                gpu[i * PROBE_BYTES:(i + 1) * PROBE_BYTES] = new[148:]
            self._put(self.pool, T_PROBEGPU, lv, bytes(gpu))
        self.log('reflections recoloured')

    # ---------------------------------------------------------------- 7. lightmap
    def pass_lightmap(self):
        gi = TARGETS['lightmap_gi']
        self.pkg.fetch([(T_TEXGPU, gi)])
        stock = self.pkg.read(T_TEXGPU, gi)
        if not stock:
            self.log('lightmap: not found, skipped')
            return
        d = self.work / 'lightmap'
        (d / 'dec').mkdir(parents=True, exist_ok=True)
        (d / 'enc').mkdir(exist_ok=True)
        (d / 'gi.dds').write_bytes(stock)
        self._texconv('-f', 'R16G16B16A16_FLOAT', '-m', '1', '-dx10', '-o', d / 'dec', d / 'gi.dds')
        raw = (d / 'dec' / 'gi.dds').read_bytes()
        slices = struct.unpack_from('<I', stock, 140)[0] or 1
        px = np.frombuffer(raw[148:], np.float16).astype(np.float32).reshape(slices, -1, 4)
        for s in range(0, slices, 4):            # SH4: slice 0 of each page is the DC term
            px[s, :, :3] = recolour(np.maximum(px[s, :, :3], 0)[None], self.cs, ldr=False)[0] * self.cs.brightness
        (d / 'col.dds').write_bytes(raw[:148] + px.astype(np.float16).tobytes())
        self._texconv('-f', 'BC6H_UF16', '-m', '1', '-dx10', '-o', d / 'enc', d / 'col.dds')
        new = (d / 'enc' / 'col.dds').read_bytes()
        if len(new) != len(stock):
            raise RuntimeError('lightmap re-encode changed size')
        self._put(self.pool, T_TEXGPU, gi, stock[:148] + new[148:])
        self.log('lightmap: brightness x%.2f' % self.cs.brightness)

    # ---------------------------------------------------------------- 8. arena-only clone
    def _names_of_type(self, t):
        return {norm(n) for n in self.pkg.list_type(t)}

    def clone(self):
        pkg = self.pkg
        pool_tex = {norm(p.name) for p in (self.pool / T_TEX).iterdir()} if (self.pool / T_TEX).exists() else set()
        pool_mat = {norm(p.name) for p in (self.pool / T_MAT).iterdir()} if (self.pool / T_MAT).exists() else set()
        pool_cv = {norm(p.name) for p in (self.pool / T_CANVAS).iterdir()} if (self.pool / T_CANVAS).exists() else set()

        def pooled(t, n):
            p = self.pool / t / n
            return p.read_bytes() if p.exists() else None

        mat_tex, clone_mat = {}, set()
        for m in self.materials:
            b = pooled(T_MAT, m) or pkg.read(T_MAT, m) or b''
            mat_tex[m] = refs(b, pool_tex)
            if m in pool_mat or mat_tex[m]:
                clone_mat.add(m)
        clone_cv = set(pool_cv)
        cv_tex = set()
        for c in clone_cv:
            cv_tex |= refs(pooled(T_CANVAS, c), pool_tex)
        clone_tex = cv_tex.copy()
        for m in clone_mat:
            clone_tex |= mat_tex[m]
        watch = clone_mat | clone_tex
        clone_model = {m for m in self.models if refs(pkg.read(T_MODEL, m) or b'', watch)}
        clone_shd = {s for s in self.shadersets if refs(pkg.read(T_SHD, s) or b'', clone_mat)}

        # names that must stay stock
        written = {}
        for h in clone_tex: written.setdefault(h, set()).update({T_TEX, T_TEXGPU})
        for h in clone_mat: written.setdefault(h, set()).add(T_MAT)
        for h in clone_shd: written.setdefault(h, set()).update({T_SHD, T_SHDGPU})
        for h in clone_model: written.setdefault(h, set()).update({T_MODEL, T_MODELGPU, T_VIS, T_TS})
        for h in clone_cv: written.setdefault(h, set()).add(T_CANVAS)
        archive_types = {}
        for t, n in self.records:
            archive_types.setdefault(n, set()).add(t)
        # (a) the byte rename is type-blind: a name another, uncloned type also uses
        unsafe = {h for h, ts in written.items() if archive_types.get(h, set()) - ts}
        for lv in LEVELS:
            # (b) textures named by texture-override records (live score / clock screens)
            b = pkg.read(T_TEXOVR, lv)
            if b:
                for off in range(0x38, len(b) - 31, 32):
                    unsafe.add(norm(struct.unpack_from('<Q', b, off)[0]))
            # (c) models/textures a canvas placement names (+0x48 model, +0x50 texture)
            for t in (T_CANVASCR, T_SHAREDCR):
                b = pkg.read(t, lv)
                if b:
                    unsafe |= refs(b, clone_model) | refs(b, clone_tex)
        for s in (clone_tex, clone_mat, clone_shd, clone_model, clone_cv):
            s -= unsafe

        # order-preserving new names: engine tables are binary-searched by (signed) hash
        primary = {}
        for h in clone_tex: primary[h] = T_TEX
        for h in clone_mat: primary[h] = T_MAT
        for h in clone_shd: primary[h] = T_SHD
        for h in clone_model: primary[h] = T_MODEL
        for h in clone_cv: primary[h] = T_CANVAS
        taken = set()
        same_type = {}
        for t in set(primary.values()):
            same_type[t] = self._names_of_type(t)
            taken |= {int(n, 16) for n in same_type[t]}
        for t in (T_TEXGPU, T_SHDGPU, T_MODELGPU, T_VIS, T_TS, T_PHYS):
            taken |= {int(n, 16) for n in self._names_of_type(t)}
        rename = {}
        for t in set(primary.values()):
            cloned = {int(h, 16) for h, pt in primary.items() if pt == t}
            seq = sorted({int(n, 16) for n in same_type[t]} | cloned, key=sgn)
            bound = None
            for idx, v in enumerate(seq):
                if v not in cloned:
                    bound = v
                    continue
                nxt = next((u for u in seq[idx + 1:] if u not in cloned), None)
                cand = (sgn(bound) + 1) if bound is not None else sgn(v) - 4096
                while (cand & M64) in taken:
                    cand += 1
                if nxt is not None and cand >= sgn(nxt):
                    raise RuntimeError('no free order-preserving name next to %016x' % v)
                taken.add(cand & M64)
                rename['%016x' % v] = norm(cand & M64)
                bound = cand & M64
        phys = {norm(physics_name(int(m, 16))): norm(physics_name(int(rename[m], 16))) for m in clone_model}
        pairs = [(int(o, 16).to_bytes(8, 'little'), int(n, 16).to_bytes(8, 'little'))
                 for o, n in list(rename.items()) + list(phys.items())]

        def apply(blob):
            for o, n in pairs:
                if o in blob:
                    blob = blob.replace(o, n)
            return blob

        st = self.stage
        for t in clone_tex:
            self._put(st, T_TEX, rename[t], pooled(T_TEX, t))
            self._put(st, T_TEXGPU, rename[t], pooled(T_TEXGPU, t))
        for m in clone_mat:
            self._put(st, T_MAT, rename[m], apply(pooled(T_MAT, m) or pkg.read(T_MAT, m)))
        pkg.fetch([(T_SHDGPU, s) for s in clone_shd])
        for s in clone_shd:
            self._put(st, T_SHD, rename[s], apply(pkg.read(T_SHD, s)))
            self._put(st, T_SHDGPU, rename[s], apply(pkg.read(T_SHDGPU, s) or b''))
        pkg.fetch([(t, m) for m in clone_model for t in (T_MODELGPU, T_VIS, T_TS)] +
                  [(T_PHYS, norm(physics_name(int(m, 16)))) for m in clone_model])
        for m in clone_model:
            self.check()
            self._put(st, T_MODEL, rename[m], apply(pkg.read(T_MODEL, m)))
            for t in (T_MODELGPU, T_VIS, T_TS):
                b = pkg.read(t, m)
                if b is not None:
                    self._put(st, t, rename[m], apply(b))
            pm = norm(physics_name(int(m, 16)))
            b = pkg.read(T_PHYS, pm)
            if b is not None:
                self._put(st, T_PHYS, phys[pm], apply(b))
        for c in clone_cv:
            self._put(st, T_CANVAS, rename[c], apply(pooled(T_CANVAS, c)))
        for t, n in self.level_files:
            src = pooled(t, n) or pkg.read(t, n)
            if src is None:
                continue
            new = apply(src)
            if new != src or pooled(t, n) is not None:
                self._put(st, t, n, new)
        gi = TARGETS['lightmap_gi']           # the arena's own GI page (content-named, per level)
        if pooled(T_TEXGPU, gi):
            self._put(st, T_TEXGPU, gi, pooled(T_TEXGPU, gi))
        (self.ws / 'rename_map.json').write_text(json.dumps({'rename': rename, 'physics': phys}, indent=1))
        self.log('arena-only: %d textures, %d materials, %d models, %d canvases cloned; %d names kept stock'
                 % (len(clone_tex), len(clone_mat), len(clone_model), len(clone_cv), len(unsafe & set(written))))

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _put(root: Path, typ: str, name: str, blob: bytes):
        d = root / typ
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(blob)


# ----------------------------------------------------------------------------- install
def install(install: EchoInstall, stage: Path, input_dir: Path | None, workspace: Path,
            colours: ColourSettings, log=print, progress=lambda f, m='': None, with_plugin=True):
    """Back up the live manifest + delta once, then repack stock + input-pcvr + stage."""
    pkg = Package(install, workspace / 'cache', input_dir, log)
    backup = workspace / 'backup_original'
    data = install.data_dir
    if not backup.exists():
        backup.mkdir(parents=True)
        man = data / 'manifests' / '48037dc70b0ecab2'
        shutil.copy2(man, backup / man.name)
        for p in (data / 'packages').glob('48037dc70b0ecab2_*'):
            idx = int(p.name.rsplit('_', 1)[1])
            if idx >= 3:                      # stock ships _0.._2; anything else is a mod delta
                shutil.copy2(p, backup / p.name)
        log('backed up your current install to %s' % backup)
    progress(0.92, 'Merging with your mods')
    merged = workspace / 'merged'
    n = merge_dirs([input_dir, stage], merged)
    log('merged %d files' % n)
    progress(0.95, 'Repacking Echo')
    log(pkg.repack(merged).strip().splitlines()[-1])
    if with_plugin:
        install_plugin(install, colours, log)
    else:
        uninstall_plugin(install, log)
    progress(1.0, 'Installed')


def install_plugin(install: EchoInstall, colours: ColourSettings, log=print):
    install.plugins_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN_DLL, install.plugins_dir / PLUGIN_DLL.name)
    (install.plugins_dir / 'ArenaColours.ini').write_text(colours.plugin_ini().replace('Enabled=1', 'Enabled=1\r\nArenaOnly=1'))
    if not (install.bin_dir / 'dbgcore.dll').exists():
        log('note: no plugin loader (dbgcore.dll) found next to echovr.exe -- the runtime colours '
            '(goals, disc, scoreboard) need one to load bin\\win10\\plugins\\ArenaColours.dll')
    log('runtime plugin installed')


def uninstall_plugin(install: EchoInstall, log=print):
    for name in ('ArenaColours.dll', 'ArenaColours.ini'):
        p = install.plugins_dir / name
        if p.exists():
            p.unlink()
    log('runtime plugin removed')


def restore_stock(install: EchoInstall, input_dir: Path | None, workspace: Path, log=print):
    """Remove ArenaColours: repack only your own mods (or the pure stock game without them)."""
    pkg = Package(install, workspace / 'cache', input_dir, log)
    if input_dir and input_dir.is_dir():
        merged = workspace / 'merged'
        merge_dirs([input_dir], merged)
        log(pkg.repack(merged).strip().splitlines()[-1])
    else:
        log(pkg.restore().strip())
    uninstall_plugin(install, log)


def test_load(install: EchoInstall, log=print, timeout=240):
    """Launch echovr -spectatorstream and watch its log until the arena loads (or fails)."""
    before = set(install.logs_dir.glob('*.log')) if install.logs_dir.exists() else set()
    proc = subprocess.Popen([str(install.exe), '-spectatorstream'], cwd=str(install.bin_dir))
    log('launched echovr (pid %d), waiting for the arena...' % proc.pid)
    t0 = time.time()
    logfile = None
    while time.time() - t0 < timeout:
        time.sleep(2)
        if logfile is None:
            new = [p for p in install.logs_dir.glob('*.log') if p not in before and str(proc.pid) in p.name]
            logfile = new[0] if new else None
            continue
        text = logfile.read_text(errors='replace')
        if "Finished loading level '0x576ED3F8428EBC4B'" in text:
            errs = [ln for ln in text.splitlines() if '0x2FDA8A8C4D75508F:' in ln]
            log('arena loaded%s' % ('' if not errs else ' with %d engine message(s):' % len(errs)))
            for e in errs[:5]:
                log('  ' + e.strip())
            return True
        if proc.poll() is not None:
            log('echovr exited before the arena loaded (exit code %s)' % proc.returncode)
            return False
    log('timed out waiting for the arena (is a match running?)')
    return False
