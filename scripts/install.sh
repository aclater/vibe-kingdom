#!/usr/bin/env bash
set -euo pipefail

echo "Creating virtual environment (venv)..."
python3 -m venv venv
source venv/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip
pip install feedparser python-dotenv requests openai

echo
echo "Done."
echo "To use:"
echo "  source venv/bin/activate"
echo "  ./kingdom_local.py fetch-news"
echo "or:"
echo "  ./kingdom_cloud.py fetch-news"
