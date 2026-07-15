#!/bin/bash
set -e

echo "========================================"
echo "  Flight Analyzer - Build & Run"
echo "========================================"
echo

echo "[1/2] Building frontend..."
cd "$(dirname "$0")/../frontend"
npm run build

echo
echo "[2/2] Starting backend..."
cd "$(dirname "$0")/.."
source .venv/Scripts/activate
python main.py
