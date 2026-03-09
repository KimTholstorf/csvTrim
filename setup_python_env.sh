#!/bin/bash
set -e

echo "Setting up virtual environment with uv..."

# Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "uv not found, installing..."
    if command -v brew &> /dev/null; then
        echo "Homebrew detected — installing uv via brew..."
        brew install uv
    else
        echo "Homebrew not found — installing uv via curl..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi

# Create venv and install requirements
uv venv .venv
echo "Installing requirements..."
uv pip install -r requirements.txt --python .venv/bin/python

echo ""
echo "Done! Activate the environment with:"
echo "  source .venv/bin/activate"
echo ""
echo "Then run the script with:"
echo "  python3 csvTrim.py --input data.csv --output trimmed.csv --preset Azure"
