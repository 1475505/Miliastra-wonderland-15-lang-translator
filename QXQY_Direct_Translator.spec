# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 千星奇域Direct Translator (user-supplied glossaries only)."""

from pathlib import Path

root = Path.cwd()

a = Analysis(
    [str(root / "app" / "qxqy_direct_translator_gui.py")],
    pathex=[str(root), str(root / "tools"), str(root / "app")],
    binaries=[],
    datas=[
        (str(root / "app" / "windows_dpi.manifest"), "app"),
        (str(root / "app" / "icon.ico"), "app"),
        (str(root / "app" / "app_logo.png"), "app"),
    ],
    hiddenimports=["ui_helpers", "glossary_dialog", "custom_glossary", "prompts", "translate_from_terms", "llm_stage2", "validate_output", "m3_theme"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="QXQY_Direct_Translator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest=str(root / "app" / "windows_dpi.manifest"),
    icon=str(root / "app" / "icon.ico"),
)
