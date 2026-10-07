"""ArenaColours -- recolour Echo VR's arena (mpl_arena_a + mpl_lobby_b_arena) and nothing else.

    python arenacolours_app.py
"""
from __future__ import annotations

import configparser
import json
import os
import subprocess
import sys
import traceback
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget)

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import numpy as np                                                    # noqa: E402
from core.colourmap import ColourSettings, recolour, rgb_to_hex, hex_to_rgb   # noqa: E402
from core.package import EchoInstall                                  # noqa: E402
from core import pipeline                                             # noqa: E402
from version import __version__                                       # noqa: E402

SETTINGS_DIR = Path(os.environ.get('APPDATA', str(Path.home()))) / 'ArenaColours'
SETTINGS_FILE = SETTINGS_DIR / 'settings.json'
DISCGLOW_API = 'https://api.github.com/repos/bollko/Echo-Restoration/releases/latest'

#: Echo's own team colours, shown before -> after in the preview strip.
PREVIEW = [('Team blue', (0.0, 0.698, 1.0)), ('Cyan glow', (0.5, 1.0, 1.0)), ('Deep blue', (0.05, 0.15, 0.4)),
           ('Team orange', (1.0, 0.5, 0.15)), ('Red-orange glow', (1.0, 0.26, 0.06)),
           ('Dark orange', (0.5, 0.22, 0.05)), ('Neutral grey', (0.5, 0.5, 0.5))]

STYLE = """
QWidget { background: #14151a; color: #e6e6ea; font-family: 'Segoe UI'; font-size: 10pt; }
QLabel, QCheckBox { background: transparent; }
QScrollArea { border: none; }
QFrame#card { background: #1d1f26; border: 1px solid #2a2d36; border-radius: 10px; }
QLabel#title { font-size: 18pt; font-weight: 600; }
QLabel#subtitle { color: #9a9cab; }
QLabel#section { font-size: 11pt; font-weight: 600; color: #ffffff; }
QLabel#hint { color: #8b8e9c; font-size: 9pt; }
QLineEdit { background: #111217; border: 1px solid #2f323c; border-radius: 6px; padding: 6px 8px; }
QPushButton { background: #2a2d37; border: 1px solid #383c48; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #343844; }
QPushButton:disabled { color: #6b6e7a; background: #1e2027; }
QPushButton#primary { background: #d6337f; border: none; color: white; font-weight: 600; padding: 9px 18px; }
QPushButton#primary:hover { background: #e8468f; }
QPushButton#primary:disabled { background: #5a2a41; color: #b89aa8; }
QProgressBar { background: #111217; border: 1px solid #2f323c; border-radius: 6px; text-align: center; height: 18px; }
QProgressBar::chunk { background: #d6337f; border-radius: 6px; }
QPlainTextEdit { background: #0f1014; border: 1px solid #2a2d36; border-radius: 6px; font-family: Consolas; font-size: 9pt; }
QSlider::groove:horizontal { height: 6px; background: #2a2d37; border-radius: 3px; }
QSlider::handle:horizontal { background: #d6337f; width: 16px; margin: -6px 0; border-radius: 8px; }
QCheckBox { spacing: 8px; }
"""


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}


def save_settings(s: dict):
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=1))
    except OSError:
        pass


# ----------------------------------------------------------------------------- widgets
class Swatch(QPushButton):
    changed = Signal(str)

    def __init__(self, colour: str):
        super().__init__()
        self.setFixedSize(84, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.set_colour(colour)
        self.clicked.connect(self.pick)

    def set_colour(self, colour: str):
        self.colour = colour
        self.setStyleSheet('QPushButton { background: %s; border: 2px solid #3a3d48; border-radius: 6px; }'
                           'QPushButton:hover { border-color: #ffffff; }' % colour)
        self.setToolTip(colour)

    def pick(self):
        c = QColorDialog.getColor(QColor(self.colour), self, 'Pick a colour')
        if c.isValid():
            self.set_colour(c.name())
            self.changed.emit(c.name())


class PreviewStrip(QWidget):
    """Before -> after for Echo's own team colours, using the exact build maths."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(66)
        self.cs = ColourSettings()

    def update_settings(self, cs: ColourSettings):
        self.cs = cs
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(PREVIEW)
        w = self.width() / n
        for i, (name, rgb) in enumerate(PREVIEW):
            after = recolour(np.array([[rgb]], np.float32), self.cs)[0, 0]
            x = int(i * w) + 3
            cw = int(w) - 6
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(rgb_to_hex(rgb)))
            p.drawRoundedRect(x, 0, cw, 22, 4, 4)
            p.setBrush(QColor(rgb_to_hex(after)))
            p.drawRoundedRect(x, 24, cw, 22, 4, 4)
            p.setPen(QColor('#9a9cab'))
            f = p.font(); f.setPointSize(7); p.setFont(f)
            p.drawText(x, 48, cw, 14, Qt.AlignHCenter, name)


def card(title: str, hint: str = '') -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(); f.setObjectName('card')
    v = QVBoxLayout(f); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(8)
    t = QLabel(title); t.setObjectName('section'); v.addWidget(t)
    if hint:
        h = QLabel(hint); h.setObjectName('hint'); h.setWordWrap(True); v.addWidget(h)
    return f, v


def slider_row(label: str, lo: float, hi: float, value: float, fmt: str):
    row = QHBoxLayout()
    lab = QLabel(label); lab.setFixedWidth(110)
    s = QSlider(Qt.Horizontal); s.setRange(int(lo * 100), int(hi * 100)); s.setValue(int(value * 100))
    val = QLabel(fmt % value); val.setFixedWidth(52); val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s.valueChanged.connect(lambda v: val.setText(fmt % (v / 100)))
    row.addWidget(lab); row.addWidget(s, 1); row.addWidget(val)
    return row, s


# ----------------------------------------------------------------------------- worker
class Worker(QObject):
    log = Signal(str)
    progress = Signal(float, str)
    done = Signal(bool, str)

    def __init__(self, job, *args):
        super().__init__()
        self.job, self.args = job, args
        self.cancel = False

    def run(self):
        try:
            msg = self.job(self, *self.args) or 'Done'
            self.done.emit(True, msg)
        except pipeline.Cancelled:
            self.done.emit(False, 'Cancelled')
        except Exception as e:
            self.log.emit(traceback.format_exc())
            self.done.emit(False, 'Failed: %s' % e)


def job_build(w: Worker, install, input_dir, workspace, cs, with_plugin=True):
    b = pipeline.Builder(install, input_dir, workspace, cs, log=w.log.emit,
                         progress=lambda f, m='': w.progress.emit(f * 0.9, m), cancelled=lambda: w.cancel)
    stage = b.build()
    pipeline.install(install, stage, input_dir, workspace, cs, log=w.log.emit,
                     progress=lambda f, m='': w.progress.emit(f, m), with_plugin=with_plugin)
    return 'Installed. Launch Echo and join an arena match.'


def job_restore(w: Worker, install, input_dir, workspace):
    w.progress.emit(0.3, 'Restoring')
    pipeline.restore_stock(install, input_dir, workspace, log=w.log.emit)
    w.progress.emit(1.0, 'Restored')
    return 'The arena is back to stock' + (' (your mods are kept).' if input_dir else '.')


def job_test(w: Worker, install):
    w.progress.emit(0.5, 'Waiting for the arena to load')
    ok = pipeline.test_load(install, log=w.log.emit)
    w.progress.emit(1.0, 'Test finished')
    return 'Arena loaded cleanly.' if ok else 'The arena did not load -- see the log.'


def job_discglow(w: Worker, install, workspace, cs):
    w.progress.emit(0.2, 'Finding the latest DiscGlow release')
    req = urllib.request.Request(DISCGLOW_API, headers={'User-Agent': 'ArenaColours'})
    rel = json.loads(urllib.request.urlopen(req, timeout=30).read())
    asset = next((a for a in rel.get('assets', []) if a['name'].lower().endswith('.exe')), None)
    if asset is None:
        raise RuntimeError('no installer in the latest release (%s)' % rel.get('tag_name'))
    dest = workspace / asset['name']
    workspace.mkdir(parents=True, exist_ok=True)
    w.log.emit('downloading %s (%s, %.1f MB)' % (asset['name'], rel.get('tag_name'), asset['size'] / 1e6))
    urllib.request.urlretrieve(asset['browser_download_url'], dest)
    write_discglow_ini(install, cs)
    w.log.emit('starting the DiscGlow installer -- click Install / Update in it')
    subprocess.Popen([str(dest)], cwd=str(workspace))
    w.progress.emit(1.0, 'DiscGlow installer started')
    return 'DiscGlow installer started; its disc colours are set to match.'


def write_discglow_ini(install, cs: ColourSettings):
    path = install.bin_dir / 'DiscGlow.ini'
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    if path.exists():
        cfg.read(path)
    if not cfg.has_section('DiscGlow'):
        cfg.add_section('DiscGlow')
    fmt = lambda h: ' '.join('%.3f' % c for c in hex_to_rgb(h))
    cfg['DiscGlow']['BlueTeamColour'] = fmt(cs.blue_target)
    cfg['DiscGlow']['OrangeTeamColour'] = fmt(cs.orange_target)
    with open(path, 'w') as f:
        cfg.write(f)


# ----------------------------------------------------------------------------- window
class Main(QWidget):
    def __init__(self):
        super().__init__()
        self.s = load_settings()
        self.setWindowTitle('ArenaColours %s' % __version__)
        self.resize(860, 980)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); outer.addWidget(scroll)
        body = QWidget(); scroll.setWidget(body)
        root = QVBoxLayout(body); root.setContentsMargins(20, 18, 20, 18); root.setSpacing(12)

        head = QVBoxLayout(); head.setSpacing(2)
        t = QLabel('ArenaColours  <span style="font-size:10pt;color:#8b8e9c">v%s</span>' % __version__); t.setObjectName('title')
        st = QLabel('Recolour Echo VR\'s arena -- mpl_arena_a and mpl_lobby_b_arena only. Every other map stays stock.')
        st.setObjectName('subtitle'); st.setWordWrap(True)
        head.addWidget(t); head.addWidget(st); root.addLayout(head)

        # folders
        f, v = card('1  Folders')
        g = QGridLayout(); g.setHorizontalSpacing(8)
        self.echo = QLineEdit(self.s.get('echo') or str(EchoInstall.guess() or ''))
        self.echo.setPlaceholderText(r'...\ready-at-dawn-echo-arena')
        b1 = QPushButton('Browse'); b1.clicked.connect(lambda: self.browse(self.echo))
        self.mods = QLineEdit(self.s.get('mods', ''))
        self.mods.setPlaceholderText('optional: your input-pcvr mod folder, so your other mods are kept')
        b2 = QPushButton('Browse'); b2.clicked.connect(lambda: self.browse(self.mods))
        default_ws = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ArenaColours'
        self.ws = QLineEdit(self.s.get('workspace') or str(default_ws))
        b3 = QPushButton('Browse'); b3.clicked.connect(lambda: self.browse(self.ws))
        for r, (lab, edit, btn) in enumerate((('Echo VR', self.echo, b1), ('Mod folder', self.mods, b2),
                                              ('Work folder', self.ws, b3))):
            g.addWidget(QLabel(lab), r, 0); g.addWidget(edit, r, 1); g.addWidget(btn, r, 2)
        v.addLayout(g)
        self.echo_status = QLabel(); self.echo_status.setObjectName('hint'); v.addWidget(self.echo_status)
        self.echo.textChanged.connect(self.check_echo)
        root.addWidget(f)

        # colours
        f, v = card('2  Colours', 'Echo\'s blues and cyans become the first colour, its oranges the second. '
                                   'Saturation applies to everything; brightness scales the arena\'s baked lighting.')
        row = QHBoxLayout(); row.setSpacing(18)
        self.blue = Swatch(self.s.get('blue', '#ff38ac'))
        self.orange = Swatch(self.s.get('orange', '#8c0012'))
        for lab, sw in (('Blue team becomes', self.blue), ('Orange team becomes', self.orange)):
            col = QVBoxLayout(); col.setSpacing(4)
            col.addWidget(QLabel(lab)); col.addWidget(sw); row.addLayout(col)
            sw.changed.connect(lambda _: self.refresh_preview())
        row.addStretch(1)
        v.addLayout(row)
        r1, self.sat = slider_row('Saturation', 0.0, 2.0, self.s.get('saturation', 1.0), '%.2fx')
        r2, self.bright = slider_row('Brightness', 0.25, 8.0, self.s.get('brightness', 1.0), '%.2fx')
        v.addLayout(r1); v.addLayout(r2)
        self.sat.valueChanged.connect(lambda _: self.refresh_preview())
        self.preview = PreviewStrip(); v.addWidget(self.preview)
        hint = QLabel('Top row: Echo\'s colour. Bottom row: what it becomes.'); hint.setObjectName('hint'); v.addWidget(hint)
        root.addWidget(f)

        # extras
        f, v = card('3  Disc & runtime colours',
                    'Goals, holo blocks, the disc and the scoreboards are coloured by the game while it runs. '
                    'The ArenaColours plugin recolours them only while you are in the arena (needs your plugin loader).')
        self.plugin_cb = QCheckBox('Install the runtime plugin (bin\\win10\\plugins\\ArenaColours.dll)')
        self.plugin_cb.setChecked(self.s.get('plugin', True)); v.addWidget(self.plugin_cb)
        dg = QHBoxLayout()
        self.dg_btn = QPushButton('Install DiscGlow')
        self.dg_btn.clicked.connect(self.discglow)
        dgh = QLabel('Downloads the latest DiscGlowSetup.exe from github.com/bollko/Echo-Restoration, '
                     'runs it, and sets its disc colours to match.')
        dgh.setObjectName('hint'); dgh.setWordWrap(True)
        dg.addWidget(self.dg_btn); dg.addWidget(dgh, 1); v.addLayout(dg)
        root.addWidget(f)

        # actions
        act = QHBoxLayout()
        self.build_btn = QPushButton('Build && Install'); self.build_btn.setObjectName('primary')
        self.build_btn.clicked.connect(self.build)
        self.restore_btn = QPushButton('Restore stock arena'); self.restore_btn.clicked.connect(self.restore)
        self.test_btn = QPushButton('Test load'); self.test_btn.clicked.connect(self.test)
        self.cancel_btn = QPushButton('Cancel'); self.cancel_btn.clicked.connect(self.cancel); self.cancel_btn.setEnabled(False)
        act.addWidget(self.build_btn); act.addWidget(self.test_btn); act.addWidget(self.restore_btn)
        act.addStretch(1); act.addWidget(self.cancel_btn)
        root.addLayout(act)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); root.addWidget(self.bar)
        self.status = QLabel('Ready'); self.status.setObjectName('hint'); root.addWidget(self.status)
        self.logbox = QPlainTextEdit(); self.logbox.setReadOnly(True); self.logbox.setMinimumHeight(160)
        root.addWidget(self.logbox, 1)

        self.thread = self.worker = None
        self.check_echo()
        self.refresh_preview()

    # -- state ------------------------------------------------------------------------
    def settings(self) -> ColourSettings:
        return ColourSettings(self.blue.colour, self.orange.colour, self.sat.value() / 100, self.bright.value() / 100)

    def persist(self):
        save_settings({'echo': self.echo.text(), 'mods': self.mods.text(), 'workspace': self.ws.text(),
                       'blue': self.blue.colour, 'orange': self.orange.colour,
                       'saturation': self.sat.value() / 100, 'brightness': self.bright.value() / 100,
                       'plugin': self.plugin_cb.isChecked()})

    def refresh_preview(self):
        self.preview.update_settings(self.settings())

    def install(self) -> EchoInstall | None:
        inst = EchoInstall(Path(self.echo.text().strip().strip('"')))
        return None if inst.problems() else inst

    def mod_dir(self) -> Path | None:
        t = self.mods.text().strip().strip('"')
        return Path(t) if t and Path(t).is_dir() else None

    def check_echo(self):
        inst = EchoInstall(Path(self.echo.text().strip().strip('"')))
        probs = inst.problems() if self.echo.text().strip() else ['pick your ready-at-dawn-echo-arena folder']
        self.echo_status.setText('Found Echo VR' if not probs else probs[0])
        self.echo_status.setStyleSheet('color: %s' % ('#5fd38d' if not probs else '#e07a7a'))

    def browse(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, 'Pick a folder', edit.text() or str(Path.home()))
        if d:
            edit.setText(d)

    # -- jobs -------------------------------------------------------------------------
    def start(self, job, *args):
        self.persist()
        self.logbox.clear()
        self.bar.setValue(0)
        for b in (self.build_btn, self.restore_btn, self.test_btn, self.dg_btn):
            b.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.thread = QThread()
        self.worker = Worker(job, *args)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.logbox.appendPlainText)
        # bound methods of a QObject are QUEUED onto the GUI thread; a bare lambda would run on
        # the worker thread and touch widgets from it (crashed in Qt6Gui.dll)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.finished)
        self.thread.start()

    def on_progress(self, f: float, msg: str):
        self.bar.setValue(int(f * 1000))
        if msg:
            self.status.setText(msg)

    def finished(self, ok: bool, msg: str):
        self.status.setText(msg)
        self.logbox.appendPlainText(('OK  ' if ok else '!!  ') + msg)
        if ok:
            self.bar.setValue(1000)
        for b in (self.build_btn, self.restore_btn, self.test_btn, self.dg_btn):
            b.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.thread.quit(); self.thread.wait()

    def need_install(self) -> EchoInstall | None:
        inst = self.install()
        if inst is None:
            QMessageBox.warning(self, 'ArenaColours', 'Pick your ready-at-dawn-echo-arena folder first.')
        return inst

    def echo_running(self) -> bool:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq echovr.exe'], capture_output=True, text=True,
                             creationflags=0x08000000).stdout
        if 'echovr.exe' in out.lower():
            QMessageBox.warning(self, 'ArenaColours', 'Close Echo VR first -- its packages are in use.')
            return True
        return False

    def build(self):
        inst = self.need_install()
        if inst is None or self.echo_running():
            return
        cs = self.settings()
        self.start(job_build, inst, self.mod_dir(), Path(self.ws.text()), cs, self.plugin_cb.isChecked())

    def restore(self):
        inst = self.need_install()
        if inst is None or self.echo_running():
            return
        if QMessageBox.question(self, 'ArenaColours', 'Put the arena back to stock'
                                + (' (your mod folder is repacked so your other mods stay)?' if self.mod_dir() else '?')) \
                != QMessageBox.Yes:
            return
        self.start(job_restore, inst, self.mod_dir(), Path(self.ws.text()))

    def test(self):
        inst = self.need_install()
        if inst is None or self.echo_running():
            return
        self.start(job_test, inst)

    def discglow(self):
        inst = self.need_install()
        if inst is None:
            return
        if QMessageBox.question(self, 'ArenaColours', 'Download DiscGlowSetup.exe from github.com/bollko/'
                                'Echo-Restoration and run it?\n\nIt is a third-party mod (dinput8.dll).') != QMessageBox.Yes:
            return
        self.start(job_discglow, inst, Path(self.ws.text()), self.settings())

    def cancel(self):
        if self.worker:
            self.worker.cancel = True
            self.status.setText('Cancelling...')

    def closeEvent(self, e):
        self.persist()
        super().closeEvent(e)


def app_icon() -> QIcon:
    pm = QPixmap(64, 64); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor('#ff38ac')); p.setPen(Qt.NoPen); p.drawPie(4, 4, 56, 56, 90 * 16, 180 * 16)
    p.setBrush(QColor('#8c0012')); p.drawPie(4, 4, 56, 56, 270 * 16, 180 * 16)
    p.end()
    return QIcon(pm)


def selftest(echo_root: str, out_file: str) -> int:
    """--selftest <echo folder> <report>: run the real discovery pass from this build, no GUI."""
    lines = []
    try:
        inst = EchoInstall(Path(echo_root))
        probs = inst.problems()
        if probs:
            raise RuntimeError(probs[0])
        ws = Path(os.environ.get('TEMP', '.')) / 'ArenaColours_selftest'
        b = pipeline.Builder(inst, None, ws, ColourSettings(), log=lines.append)
        b.discover()
        lines.append('SELFTEST OK %s' % __version__)
        code = 0
    except Exception:
        lines.append(traceback.format_exc())
        lines.append('SELFTEST FAILED')
        code = 1
    Path(out_file).write_text(chr(10).join(lines))
    return code


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == '--selftest':
        sys.exit(selftest(sys.argv[2], sys.argv[3]))
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
