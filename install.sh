#!/bin/sh
# Agent Keylight -- macOS installer.  Usage: ./install.sh [--uninstall]
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required. Run 'xcode-select --install' or get it from"
    echo "https://python.org, then run ./install.sh again."
    exit 1
fi

python3 installer.py "$@"
