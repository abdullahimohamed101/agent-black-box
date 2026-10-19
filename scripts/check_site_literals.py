#!/usr/bin/env python3
"""Fails when a site stylesheet types a colour (brief 5.3: tokens only). usage: check_site_literals.py FILE..."""

import re
import sys
from pathlib import Path

PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch|color)\(")
bad = 0
for f in sys.argv[1:]:
    for n, line in enumerate(Path(f).read_text().splitlines(), 1):
        code = re.sub(r"/\*.*?\*/", "", line)
        if PATTERN.search(code):
            print(f"FAIL: {f}:{n}: colour literal: {line.strip()[:100]}")
            bad += 1
if bad:
    sys.exit(1)
print(f"no colour literal in {len(sys.argv) - 1} site stylesheets (every colour is a token)")
