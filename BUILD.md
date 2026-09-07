# Building the standalone GUI .exe

## One-time setup (on your Windows machine)
```
pip install "pyinstaller>=6,<7"
```

## Build
From the folder containing `mugen_char_linter.py`, `mugen_char_linter_gui.py`,
and this spec file:
```
pyinstaller mugen_char_linter_gui.spec
```
Output: `dist/MugenCharLinter/MugenCharLinter.exe` — double-clickable,
no Python required on the end user's machine. Ship the whole
`MugenCharLinter` folder (the .exe needs its sibling DLLs/files next to it).

## One-file alternative
If you'd rather ship a single .exe (slightly slower startup, unpacks to a
temp dir each run) instead of a folder:
```
pyinstaller --onefile --windowed --noupx --name MugenCharLinter --icon assets/icon.ico --add-data "assets/icon.png;assets" mugen_char_linter_gui.py
```
Output: `dist/MugenCharLinter.exe` — just that one file. The `--add-data`
argument keeps the runtime window icon available in a one-file build.

## Notes
- Windows Defender / SmartScreen may flag a fresh unsigned .exe on first run
  (common for small PyInstaller apps). Nothing to fix code-side; code-signing
  is the only real cure and costs money. UPX compression is turned off
  (`upx=False` / `--noupx`) because it makes those false positives more likely.
- Rebuild whenever either `.py` file or an icon asset changes — the .exe is a frozen snapshot,
  not auto-updating.
- `assets/icon.png` is the editable 256x256 RGBA source; `assets/icon.ico` contains
  the Windows 16, 24, 32, 48, 64, 128, and 256 pixel sizes used by the build.
- PyInstaller builds for the OS it runs on; build the Windows .exe on Windows.
