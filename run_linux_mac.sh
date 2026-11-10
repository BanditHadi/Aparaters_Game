#!/usr/bin/env bash
# Country Boost Live - Linux / macOS launcher
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
  source venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
else
  source venv/bin/activate
fi
read -p "Paste your YouTube live link (or press Enter for DEMO mode): " VIDEO
if [ -z "$VIDEO" ]; then
  python main.py --demo
else
  python main.py --video "$VIDEO"
fi
