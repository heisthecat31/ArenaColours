"""Echo VR install + package access for the app, over the bundled evrtool (EvrFile).

Everything is read from the STOCK manifest (manifests/<pkg>.bak once the install has been
repacked), so a build never starts from an earlier build's output. Resources the user's
own mod folder (input-pcvr) carries win over stock, so their mods are recoloured, not lost.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
BIN = APP_DIR / 'bin'
EVRTOOL = BIN / 'evrtool.exe'
TEXCONV = BIN / 'texconv.exe'
PLUGIN_DLL = BIN / 'ArenaColours.dll'
PACKAGE = '48037dc70b0ecab2'
DATA_SUBDIR = Path('_data') / '5932408047' / 'rad15' / 'win10'

NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW: no console flashes from the GUI


def norm(h) -> str:
    return '%016x' % (h if isinstance(h, int) else int(h, 16))


@dataclass
class EchoInstall:
    root: Path

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_SUBDIR

    @property
    def bin_dir(self) -> Path:
        return self.root / 'bin' / 'win10'

    @property
    def exe(self) -> Path:
        return self.bin_dir / 'echovr.exe'

    @property
    def plugins_dir(self) -> Path:
        return self.bin_dir / 'plugins'

    @property
    def logs_dir(self) -> Path:
        return self.root / '_local' / 'r14logs'

    def problems(self) -> list[str]:
        out = []
        if not self.exe.is_file():
            out.append('bin\\win10\\echovr.exe not found -- pick the ready-at-dawn-echo-arena folder')
        if not (self.data_dir / 'manifests' / PACKAGE).is_file():
            out.append('_data\\5932408047\\rad15\\win10\\manifests\\%s not found' % PACKAGE)
        return out

    @staticmethod
    def guess() -> Path | None:
        for c in (r'C:\Program Files\Oculus\Software\Software\ready-at-dawn-echo-arena',
                  r'C:\Oculus\Games\Software\Software\ready-at-dawn-echo-arena',
                  r'D:\Oculus\Software\Software\ready-at-dawn-echo-arena'):
            if (Path(c) / 'bin' / 'win10' / 'echovr.exe').is_file():
                return Path(c)
        return None


class Package:
    """Stock resources by (type, name): input-pcvr first, then a cache filled from the package."""

    def __init__(self, install: EchoInstall, cache: Path, input_dir: Path | None, log=print):
        self.install = install
        self.cache = cache
        self.input_dir = input_dir if input_dir and input_dir.is_dir() else None
        self.log = log
        cache.mkdir(parents=True, exist_ok=True)

    # -- evrtool ------------------------------------------------------------------------
    def _run(self, *args, check=True) -> str:
        r = subprocess.run([str(EVRTOOL), *map(str, args)], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        if check and r.returncode != 0:
            raise RuntimeError('evrtool %s failed: %s' % (args[0], (r.stderr or r.stdout).strip()[-500:]))
        return r.stdout

    def list_type(self, typ: str) -> list[str]:
        out = self._run('list', self.install.data_dir, typ)
        return [ln.split('/')[1] for ln in out.split() if '/' in ln]

    def find(self, names) -> list[tuple[str, str]]:
        out = self._run('find', self.install.data_dir, *names)
        pairs = []
        for ln in out.splitlines():
            if '/' in ln:
                t, n = ln.split()[0].split('/')
                pairs.append((t, n))
        return pairs

    def fetch(self, pairs) -> None:
        """Make sure every (type, name) is available locally (input-pcvr or cache)."""
        need = sorted({(norm(t), norm(n)) for t, n in pairs if self.path(t, n) is None})
        if not need:
            return
        lst = self.cache / '_want.txt'
        lst.write_text('\n'.join('%s/%s' % p for p in need))
        self._run('get', self.install.data_dir, lst, self.cache)

    def repack(self, merged_dir: Path) -> str:
        return self._run('repack', self.install.data_dir, merged_dir)

    def restore(self) -> str:
        return self._run('restore', self.install.data_dir)

    # -- local lookup -------------------------------------------------------------------
    def path(self, typ: str, name: str) -> Path | None:
        typ, name = norm(typ), norm(name)
        roots = ([self.input_dir] if self.input_dir else []) + [self.cache]
        for root in roots:
            for t in (typ, typ.lstrip('0')):
                for n in (name, name.lstrip('0') or '0'):
                    p = root / t / n
                    if p.is_file():
                        return p
        return None

    def read(self, typ: str, name: str) -> bytes | None:
        p = self.path(typ, name)
        return p.read_bytes() if p else None

    def root_for(self, typ: str, name: str) -> Path | None:
        """The tree (input-pcvr or cache) that holds this resource, for tree-based readers."""
        p = self.path(typ, name)
        return p.parent.parent if p else None


def merge_dirs(sources: list[Path], dest: Path) -> int:
    """Union of <type>/<name> trees into dest (later sources win). Hard-links when possible."""
    if dest.exists():
        shutil.rmtree(dest)
    count = 0
    for src in sources:
        if not src or not src.is_dir():
            continue
        for tdir in src.iterdir():
            if not tdir.is_dir():
                continue
            tname = tdir.name.lower()
            if len(tname) < 16 and all(c in '0123456789abcdef' for c in tname):
                tname = tname.zfill(16)
            (dest / tname).mkdir(parents=True, exist_ok=True)
            for f in tdir.iterdir():
                if not f.is_file():
                    continue
                name = f.name
                if len(name) < 16 and all(c in '0123456789abcdef' for c in name.lower()):
                    name = name.lower().zfill(16)      # one spelling, so later sources really win
                target = dest / tname / name
                if target.exists():
                    target.unlink()
                try:
                    target.hardlink_to(f)
                except OSError:
                    shutil.copy2(f, target)
                count += 1
    return count
