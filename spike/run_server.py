#!/usr/bin/env python3
"""Spike-local launcher; bypasses the editable-install plumbing in pyproject.toml."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_server import main

if __name__ == "__main__":
    main()
