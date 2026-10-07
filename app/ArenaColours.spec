# pyinstaller ArenaColours.spec  -> dist/ArenaColours/ArenaColours.exe
EXCLUDE_MODULES = ['pygame', 'pythonnet', 'clr', 'clr_loader', 'win32com', 'win32api', 'pythoncom', 'pywintypes',
                   'cffi', 'pycparser', 'tkinter', 'matplotlib', 'PIL', 'scipy', 'pandas', 'IPython',
                   'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtPdf',
                   'PySide6.QtOpenGL', 'PySide6.QtVirtualKeyboard', 'PySide6.QtWebEngineCore']
# Qt pieces a plain widgets app never loads (opengl32sw.dll is the 20 MB software-GL fallback)
DROP_BINARIES = ('opengl32sw', 'qt6quick', 'qt6qml', 'qt6pdf', 'qt6network', 'qt6virtualkeyboard',
                 'qt6opengl', 'qtnetwork', 'qtqml', 'qtquick', 'qtpdf', 'qtopengl')

a = Analysis(['arenacolours_app.py'], pathex=['.', 'vendor'],
             datas=[('bin', 'bin'), ('data', 'data'), ('vendor', 'vendor')],
             hiddenimports=['core.colourmap', 'core.package', 'core.pipeline', 'version'],
             excludes=EXCLUDE_MODULES)
import os.path
a.binaries = [b for b in a.binaries if not any(k in os.path.basename(b[0]).lower() for k in DROP_BINARIES)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ArenaColours', console=False)
coll = COLLECT(exe, a.binaries, a.datas, name='ArenaColours')
