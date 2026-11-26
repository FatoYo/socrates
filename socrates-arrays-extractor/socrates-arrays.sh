#!/bin/bash
cd "$(dirname "$0")"

# Check if virtual environment exists, create if not
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Creating..."
    python3 -m venv venv
    source venv/bin/activate
    echo "Installing dependencies..."
    pip3 install -r requirements.txt
else
    source venv/bin/activate
fi

python3 socrates-arrays.py "$@"