#!/usr/bin/env python3
"""Fail if converted MDX still contains GitBook syntax or GitBook-hosted URLs outside code."""
import glob, re, sys

PATTERNS = r"\{%\s*(?:end)?[a-z-]+\b[^}]*%\}|\.gitbook/|app\.gitbook\.com|gitbook\.io|files\.gitbook\.com"
bad = 0
for f in sorted(glob.glob("**/*.mdx", recursive=True)):
    if f.startswith(("node_modules/", "scripts/")):
        continue
    s = open(f, encoding="utf-8").read()
    s = re.sub(r"(?ms)^[ \t>]*(`{3,}|~{3,}).*?^[ \t>]*\1[ \t]*$", "", s)
    s = re.sub(r"`[^`\n]*`", "", s)
    for m in re.finditer(PATTERNS, s):
        bad += 1
        print(f"{f}: {s[max(0, m.start() - 50):m.end() + 50]!r}")
print(f"{bad} leftover(s)")
sys.exit(1 if bad else 0)
