#!/usr/bin/env bash
set -euo pipefail

echo "Creating virtual environment (venv)..."
python3 -m venv venv
source venv/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip
pip install feedparser python-dotenv openai tavily-python google-api-python-client youtube-transcript-api

echo
echo "Done."
echo "To use:"
echo "  source venv/bin/activate"
echo "  ./kingdom.py fetch-news                                                  # cloud (OpenAI API)"
echo "  ./kingdom.py --engine local fetch-news                                   # local LLM (RamaLama, etc.)"
echo "  ./kingdom.py bootstrap-constitution --name 'Your Name' --employer 'Acme' # generate personalized constitution"
