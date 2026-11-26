#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# Check if venv exists, create if not
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "Installing dependencies..."
    "$VENV_DIR/bin/pip3" install -r "$SCRIPT_DIR/requirements.txt" -q
fi

# Stop Selenium Manager from sending usage statistics
export SE_AVOID_STATS=true

# Activate venv and run ae-blog.py with all arguments
source "$VENV_DIR/bin/activate"
python3 "$SCRIPT_DIR/ae-blog.py" "$@"
