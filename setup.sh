#!/usr/bin/env bash
set -e

echo ""
echo "╔══════════════════════════════╗"
echo "║     Docify Setup Script      ║"
echo "╚══════════════════════════════╝"
echo ""

# ── System dependencies check ──────────────────────────────────────────────────
echo "==> Checking system dependencies..."
if ! command -v pdftoppm &> /dev/null; then
  echo "    ⚠  poppler-utils not found — needed for pdf2image"
  echo "       Ubuntu/Debian: sudo apt install poppler-utils"
  echo "       macOS:         brew install poppler"
fi

# ── Python venv ────────────────────────────────────────────────────────────────
echo "==> Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "==> Installing Playwright Chromium..."
playwright install chromium

# ── Django setup ───────────────────────────────────────────────────────────────
echo "==> Copying .env..."
cp -n .env.example .env 2>/dev/null || true

echo "==> Running Django migrations..."
python manage.py migrate

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅  Setup complete!                                 ║"
echo "╟──────────────────────────────────────────────────────╢"
echo "║  1. Edit .env  →  add your GEMMA_API_KEY            ║"
echo "║  2. source .venv/bin/activate                        ║"
echo "║  3. python manage.py runserver                       ║"
echo "║                                                      ║"
echo "║  Debug pipeline without browser:                     ║"
echo "║  python manage.py test_pipeline --pdf file.pdf       ║"
echo "║    --step 1   → test PDF→images only                 ║"
echo "║    --step 2   → test + vision analysis               ║"
echo "║    --step 4   → full end-to-end                      ║"
echo "║                                                      ║"
echo "║  View logs in browser: http://localhost:8000/logs/   ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
