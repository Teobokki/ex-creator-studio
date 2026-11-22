#!/bin/bash
# EX CREATOR STUDIO build (Wine): onedir app -> Inno Setup installer, plus a standalone portable .exe.
set -e
VER=1.5.6
cd "$(dirname "$0")"
export PATH=/workspace/wine-portable/current/bin:$PATH WINEPREFIX=/workspace/exwifi/.wine WINEDEBUG=-all
rm -rf build dist
wine 'C:\Python311\python.exe' -m PyInstaller --noconfirm EX_CREATOR_STUDIO.spec > /workspace/ecs${VER//./}_pyi.log 2>&1
wine 'C:\Python311\python.exe' -m PyInstaller --noconfirm EX_CREATOR_STUDIO_portable.spec > /workspace/ecs${VER//./}_pyi_portable.log 2>&1
wine 'C:\InnoSetup\ISCC.exe' installer.iss > /workspace/ecs${VER//./}_iss.log 2>&1
mv "dist/EX CREATOR STUDIO Portable.exe" "dist/EX.CREATOR.STUDIO.Portable.$VER.exe"
ls -la dist
