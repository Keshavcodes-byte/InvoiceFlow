# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks.tcl_tk import tcltk_info


datas = [('web/index.html', 'web')]
binaries = []

# Keep Tcl/Tk's standard relative layout inside the frozen bundle. Tk's
# pkgIndex.tcl resolves its Windows DLL from ../../bin/tk86t.dll.
if tcltk_info.available and tcltk_info.tcl_data_dir and tcltk_info.tk_data_dir:
    tcl_major, tcl_minor = tcltk_info.tcl_version
    tk_major, tk_minor = tcltk_info.tk_version
    datas.extend([
        (tcltk_info.tcl_data_dir, f'tcl/tcl{tcl_major}.{tcl_minor}'),
        (tcltk_info.tk_data_dir, f'tcl/tk{tk_major}.{tk_minor}'),
    ])
    if tcltk_info.tk_shared_library:
        binaries.append((tcltk_info.tk_shared_library, 'bin'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='InvoiceFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='InvoiceFlow',
)
