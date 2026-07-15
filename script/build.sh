#!/bin/bash
set -e

ROOT="$(dirname "$0")/.."
cd "$ROOT"

echo "========================================"
echo "  Flight Analyzer - Package"
echo "========================================"
echo

echo "[1/2] Building frontend..."
cd frontend && npm run build && cd ..

echo
echo "[2/2] Running PyInstaller..."
.venv/Scripts/pyinstaller --distpath packaging/dist --workpath packaging/build --noconfirm packaging/FlightAnalyzer.spec

echo
echo "Done: packaging/dist/FlightAnalyzer.exe"
