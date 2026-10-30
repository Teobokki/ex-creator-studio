# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
d, b, h = collect_all('imageio_ffmpeg')
d2, b2, h2 = collect_all('soundcard')
d, b, h = d + d2, b + b2, h + h2
a = Analysis(['ex_creator_studio.py'], datas=[('assets', 'assets')] + d, binaries=b,
             hiddenimports=['ecs_studio_ui', 'ecs_studio', 'comtypes', 'comtypes.client', 'comtypes.stream', 'winsound', 'PIL.ImageTk', 'ecs_art', 'ecs_art_core', 'ecs_art_icons', 'tkinter.colorchooser', 'tkinter.simpledialog', 'PIL.ImageGrab', 'PIL.ImageFont', 'PIL.ImageDraw', 'ecs_screenrec', 'soundcard', 'soundcard.mediafoundation', 'cffi', '_cffi_backend'] + h,
             excludes=['matplotlib', 'scipy', 'pandas', 'IPython', 'pytest', 'webview', 'fitz', 'pymupdf', 'clr', 'pythonnet'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='EX CREATOR STUDIO', console=False, upx=False,
          version='version.txt', icon=['assets/logo.ico'])
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name='EX CREATOR STUDIO')
