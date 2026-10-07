"""Build a distributable ArenaColours release zip.

    python make_release.py [--out H:\\Tools\\ArenaColours_release]

1. builds the plugin (build.bat) and evrtool, copies both into app/bin
2. freezes the app with PyInstaller (no Python needed to run it)
3. adds a quick-start README and every third-party licence the bundle needs
4. zips it, writes a SHA-256, and smoke-tests the extracted copy
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'app'
sys.path.insert(0, str(APP))
from version import __version__          # noqa: E402

SITE = Path(sys.executable).parent / 'Lib' / 'site-packages'
GOMOD = Path(subprocess.run(['go', 'env', 'GOMODCACHE'], capture_output=True, text=True).stdout.strip() or '.')

QUICKSTART = """ArenaColours {v}
=================

Recolours Echo VR's arena (mpl_arena_a and mpl_lobby_b_arena) -- blue team and orange team
become colours you pick. Every other map stays stock.

QUICK START
1. Close Echo VR.
2. Run ArenaColours\\ArenaColours.exe (no install needed; keep the folder together).
3. Folders: your ready-at-dawn-echo-arena folder is found automatically if it is in a
   standard Oculus location. If you already use other package mods, point "Mod folder" at
   your input-pcvr folder so they are kept. Pick a work folder with ~3 GB free.
4. Pick what blue and orange become, set saturation / brightness, click Build & Install
   (about 3 minutes). Your current install is backed up to <work folder>\\backup_original.
5. Optional: Test load starts Echo in -spectatorstream mode and reports when the arena loads.

To undo: Restore stock arena.

REQUIREMENTS
- Echo VR for PC, final build (the one installed by the Oculus/Meta app), Windows 10/11.
- Runtime colours (goals, holo blocks, disc, scoreboard bars) come from the ArenaColours
  plugin, which the app copies to bin\\win10\\plugins. It needs an Echo plugin loader
  (dbgcore.dll next to echovr.exe). Without one, everything baked into the files is still
  recoloured; only those runtime-coloured parts keep their stock colours.
- Optional: "Install DiscGlow" downloads github.com/bollko/Echo-Restoration's installer
  (personal disc colour, sticky team colour) and sets its disc colours to match.

Third-party licences are in the LICENSES folder.
"""

DETOURS_MIT = """Microsoft Detours (https://github.com/microsoft/Detours) -- linked into ArenaColours.dll

MIT License

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

DIRECTXTEX_MIT = DETOURS_MIT.replace(
    'Microsoft Detours (https://github.com/microsoft/Detours) -- linked into ArenaColours.dll',
    'DirectXTex texconv.exe (https://github.com/microsoft/DirectXTex) -- bundled as bin\\texconv.exe')

QT_NOTICE = """Qt for Python (PySide6 / Shiboken6) and Qt -- https://www.qt.io/qt-for-python

ArenaColours.exe uses PySide6 and the Qt libraries under the GNU Lesser General Public
License v3 (LGPL-3.0-only); see LGPL-3.0.txt and GPL-3.0.txt in this folder. The Qt
libraries are shipped unmodified as separate DLLs in ArenaColours\\_internal, so they can
be replaced with compatible builds. Qt's source is available at https://download.qt.io/
and PySide6's at https://code.qt.io/cgit/pyside/pyside-setup.git/.
"""


def run(cmd, cwd=None):
    print('>', ' '.join(map(str, cmd)))
    r = subprocess.run(list(map(str, cmd)), cwd=cwd)
    if r.returncode:
        raise SystemExit('failed: %s' % cmd)


def build_binaries():
    run(['cmd', '/c', str(ROOT / 'build.bat')])
    shutil.copy2(ROOT / 'out' / 'ArenaColours.dll', APP / 'bin' / 'ArenaColours.dll')
    run(['go', 'build', '-o', str(APP / 'bin' / 'evrtool.exe'), '.'], cwd=ROOT / 'evrtool')
    for need in ('ArenaColours.dll', 'evrtool.exe', 'texconv.exe'):
        if not (APP / 'bin' / need).is_file():
            raise SystemExit('missing app/bin/' + need)


def freeze(work: Path) -> Path:
    dist = work / 'dist'
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', dist,
         '--workpath', work / 'pyi', 'ArenaColours.spec'], cwd=APP)
    for d in (APP / 'build', APP / 'dist'):
        shutil.rmtree(d, ignore_errors=True)
    return dist / 'ArenaColours'


def licences(dest: Path, cache: Path):
    dest.mkdir(parents=True)
    copies = {
        'EvrFile-cosmetic-editor-MIT.txt': ROOT.parent / 'EchoVR-Cosmetics-Editor' / 'LICENSE',
        'klauspost-compress.txt': GOMOD / 'github.com' / 'klauspost' / 'compress@v1.20.0' / 'LICENSE',
        'Python-PSF.txt': Path(sys.executable).parent / 'LICENSE.txt',
        'numpy-BSD.txt': SITE / 'numpy-1.23.5.dist-info' / 'LICENSE.txt',
    }
    for name, src in copies.items():
        if not src.is_file():
            raise SystemExit('licence source missing: %s' % src)
        shutil.copy2(src, dest / name)
    (dest / 'Detours-MIT.txt').write_text(DETOURS_MIT)
    (dest / 'DirectXTex-texconv-MIT.txt').write_text(DIRECTXTEX_MIT)
    (dest / 'Qt-PySide6-NOTICE.txt').write_text(QT_NOTICE)
    cache.mkdir(parents=True, exist_ok=True)
    for name, url in (('LGPL-3.0.txt', 'https://www.gnu.org/licenses/lgpl-3.0.txt'),
                      ('GPL-3.0.txt', 'https://www.gnu.org/licenses/gpl-3.0.txt')):
        local = cache / name
        if not local.is_file():
            req = urllib.request.Request(url, headers={'User-Agent': 'ArenaColours-release'})
            local.write_bytes(urllib.request.urlopen(req, timeout=30).read())
        shutil.copy2(local, dest / name)


def zip_dir(src: Path, zpath: Path):
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(src.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(src.parent))


def smoke_test(zpath: Path, work: Path):
    t = work / 'smoke'
    shutil.rmtree(t, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(t)
    exe = next(t.rglob('ArenaColours.exe'))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    p = subprocess.Popen([str(exe)], env=env)
    time.sleep(8)
    alive = p.poll() is None
    if alive:
        p.kill()
    shutil.rmtree(t, ignore_errors=True)
    if not alive:
        raise SystemExit('smoke test: the extracted exe exited early (code %s)' % p.returncode)
    print('smoke test: extracted exe starts and stays up')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=r'H:\Tools\ArenaColours_release')
    ap.add_argument('--work', default=r'H:\Tools\Tools\Settings\Temp\arenacolours_release')
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(work / 'dist', ignore_errors=True)

    build_binaries()
    app_dir = freeze(work)
    name = 'ArenaColours-%s-win64' % __version__
    stage = work / 'stage' / name
    shutil.rmtree(stage.parent, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copytree(app_dir, stage / 'ArenaColours')
    (stage / 'README.txt').write_text(QUICKSTART.format(v=__version__))
    shutil.copy2(ROOT / 'README.md', stage / 'HOW-IT-WORKS.md')
    licences(stage / 'LICENSES', work / 'licence_cache')

    zpath = out / (name + '.zip')
    if zpath.exists():
        zpath.unlink()
    zip_dir(stage, zpath)
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    (out / (name + '.zip.sha256')).write_text('%s  %s\n' % (digest, zpath.name))
    smoke_test(zpath, work)
    print('release: %s (%.1f MB)\nsha256: %s' % (zpath, zpath.stat().st_size / 1e6, digest))


if __name__ == '__main__':
    main()
