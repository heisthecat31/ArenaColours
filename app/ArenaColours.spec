# pyinstaller ArenaColours.spec  -> dist/ArenaColours/ArenaColours.exe
a = Analysis(['arenacolours_app.py'], pathex=['.', 'vendor'],
             datas=[('bin', 'bin'), ('data', 'data'), ('vendor', 'vendor')],
             hiddenimports=['core.colourmap', 'core.package', 'core.pipeline', 'version'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ArenaColours', console=False)
coll = COLLECT(exe, a.binaries, a.datas, name='ArenaColours')
