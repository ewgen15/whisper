"""Syntax-check every inline <script> in docs/*.html with `node --check`."""
import glob
import re
import subprocess
import sys
import tempfile

failed = False
for path in sorted(glob.glob("docs/*.html")):
    html = open(path, encoding="utf-8").read()
    for i, m in enumerate(re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(m.group(1))
        r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
        if r.returncode:
            failed = True
            print(f"{path} script #{i}:\n{r.stderr}")
        else:
            print(f"ok  {path} script #{i}")
sys.exit(1 if failed else 0)
