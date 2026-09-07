# PyInstaller spec for the Mugen Char Linter GUI.
# Build (Windows, from repo root where both .py files live):
#   pyinstaller mugen_char_linter_gui.spec
# Output: dist/MugenCharLinter/MugenCharLinter.exe (folder build, fast startup)

block_cipher = None

a = Analysis(
    ['mugen_char_linter_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/icon.png', 'assets')],
    # Explicit since it's a local sibling import PyInstaller's static
    # analysis might not always resolve on its own.
    hiddenimports=['mugen_char_linter'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MugenCharLinter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes trigger more antivirus false positives
    console=False,   # windowed app, no console flash
    icon='assets/icon.ico',  # generated from the 256x256 RGBA source image
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MugenCharLinter',
)
