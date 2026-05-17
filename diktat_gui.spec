# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Spec für KI-Transcribe Pro
Erstellt eine standalone Windows-EXE
"""

block_cipher = None

a = Analysis(
    ['diktat_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'scipy.special._cdflib',
        'scipy.linalg.cython_blas',
        'scipy.linalg.cython_lapack',
        'scipy.integrate',
        'scipy.integrate._ode',
        'scipy.integrate._quadrature',
        'scipy.integrate._odepack',
        'scipy.integrate._vode',
        'scipy.special',
        'scipy.special._ufuncs_cxx',
        'numpy.core._dtype_ctypes',
        'sounddevice',
        'numpy',
        'requests',
        'pyperclip',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'PIL',
        'pytest',
        'IPython',
        'jupyter',
        'notebook',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KI-Transcribe-Pro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Keine Konsole (GUI-Anwendung)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Optional: Füge später ein Icon hinzu
    version_file=None,  # Optional: Versionsinformationen
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KI-Transcribe-Pro',
)
