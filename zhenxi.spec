# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


project_root = Path.cwd()

datas = [
    (str(project_root / "assets"), "assets"),
    (str(project_root / "docs"), "docs"),
]
binaries = []
hiddenimports = [
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
]

for source_name in (
    "api_config.example.json",
    "downloader_config.example.json",
    "tencent_asr_config.example.json",
):
    source_path = project_root / "runtime" / source_name
    if source_path.exists():
        datas.append((str(source_path), "runtime"))

ffmpeg_dir = project_root / "runtime" / "ffmpeg"
if ffmpeg_dir.exists() and any(ffmpeg_dir.iterdir()):
    datas.append((str(ffmpeg_dir), "runtime/ffmpeg"))


a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "paddle",
        "paddleocr",
        "paddlex",
        "torch",
        "tensorflow",
        "matplotlib",
        "pandas",
        "scipy",
        "sklearn",
        "openpyxl",
        "imageio_ffmpeg",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="zhenxi",
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
    name="zhenxi",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="zhenxi.app",
        icon=None,
        bundle_identifier="com.zhenxi.app",
    )
