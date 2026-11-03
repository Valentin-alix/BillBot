# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import msgspec

a = Analysis(
    ['__main__.py'],
    pathex=['DBDofusUnity', 'AnkamaLauncherEmulator'],
    binaries=[],
    datas=[
        ('resources/icons', 'resources/icons'),
        ('VERSION', '.'),
        ('DBDofusUnity/datas/bundles/data', 'DBDofusUnity/datas/bundles/data'),
        ('DBDofusUnity/datas/bundles/standalone', 'DBDofusUnity/datas/bundles/standalone'),
        ('DBDofusUnity/datas/bundles/i18n.json', 'DBDofusUnity/datas/bundles'),
        # Runtime maps are read from the archive; raw extraction outputs can be incomplete.
        ('DBDofusUnity/datas/bundles/maps.zip', 'DBDofusUnity/datas/bundles'),
        ('DBDofusUnity/datas/protos', 'DBDofusUnity/datas/protos'),
        ('AnkamaLauncherEmulator/ankama_launcher_emulator/server/dofus3/script.js', 'AnkamaLauncherEmulator/ankama_launcher_emulator/server/dofus3'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# --no-shell skips new downloads; filter the hook output too for existing caches.
def keep_browser_file(entry):
    return not any(part.startswith('chromium_headless_shell-') for part in Path(entry[0]).parts)


a.binaries = [entry for entry in a.binaries if keep_browser_file(entry)]
a.datas = [entry for entry in a.datas if keep_browser_file(entry)]

# Preserve source JSON files and runtime paths; only compact the packaged copies.
compact_root = Path(SPECPATH) / 'build' / 'package-data'
saved_bytes = 0
for index, (destination, source, kind) in enumerate(a.datas):
    relative = Path(destination)
    if relative.is_relative_to('DBDofusUnity/datas/bundles') and relative.suffix == '.json':
        original = Path(source).read_bytes()
        compact = msgspec.json.encode(msgspec.json.decode(original))
        output = compact_root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(compact)
        a.datas[index] = (destination, str(output), kind)
        saved_bytes += len(original) - len(compact)
print(f'Compacted bundled JSON: saved {saved_bytes / 1_000_000:.1f} MB')

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BillBot',
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
    name='BillBot',
)
