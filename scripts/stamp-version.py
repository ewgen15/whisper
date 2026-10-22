#!/usr/bin/env python3
"""Stamps docs/index.html with a version: <date>.<hash of the file with the stamp blanked>.

Run before committing frontend changes. `--check` (pre-push, CI) fails if the stamp is stale,
so the number shown in the app always changes when the page does."""
import datetime
import hashlib
import re
import sys
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "docs" / "index.html"
PATTERN = re.compile(r"const APP_VERSION = '[^']*';")


def digest(text: str) -> str:
    return hashlib.sha1(PATTERN.sub("const APP_VERSION = '';", text).encode()).hexdigest()[:6]


def main() -> int:
    text = PAGE.read_text()
    h = digest(text)
    current = PATTERN.search(text).group(0)
    if "--check" in sys.argv:
        if not current.endswith(f".{h}';"):
            print(f"stale APP_VERSION ({current}); run python3 scripts/stamp-version.py")
            return 1
        return 0
    stamp = f"{datetime.date.today():%Y-%m-%d}.{h}"
    PAGE.write_text(PATTERN.sub(f"const APP_VERSION = '{stamp}';", text))
    print(stamp)
    return 0


sys.exit(main())
