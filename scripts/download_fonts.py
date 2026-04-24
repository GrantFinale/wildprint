"""Download Google Font TTFs via the official CSS API, which always has
current URLs regardless of repo reshuffles.
"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "fonts"
OUT.mkdir(parents=True, exist_ok=True)

FAMILIES = [
    ("Playfair+Display", "PlayfairDisplay"),
    ("Lora", "Lora"),
    ("Merriweather", "Merriweather"),
    ("Cormorant+Garamond", "CormorantGaramond"),
    ("EB+Garamond", "EBGaramond"),
    ("Montserrat", "Montserrat"),
    ("Oswald", "Oswald"),
    ("Raleway", "Raleway"),
    ("Poppins", "Poppins"),
    ("Bebas+Neue", "BebasNeue"),
]

# This User-Agent triggers the CSS API to return TTF URLs (older-browser fallback)
UA = "Mozilla/5.0 (X11; Linux i686) AppleWebKit/537.36"

VARIANTS = [
    ("400", "normal", "Regular"),
    ("700", "normal", "Bold"),
    ("400", "italic", "Italic"),
    ("700", "italic", "BoldItalic"),
]

done, failed = 0, 0
for family, local in FAMILIES:
    url = f"https://fonts.googleapis.com/css2?family={family}:ital,wght@0,400;0,700;1,400;1,700&display=swap"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        css = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    except Exception as e:
        print(f"{family}: css fetch failed: {e}")
        failed += 1
        continue

    blocks = re.split(r"@font-face\s*\{", css)[1:]
    for block in blocks:
        style_m = re.search(r"font-style:\s*(\w+)", block)
        weight_m = re.search(r"font-weight:\s*(\d+)", block)
        url_m = re.search(r"url\(([^)]+\.ttf)\)", block)
        if not (style_m and weight_m and url_m):
            continue
        suffix = next(
            (s for w, st, s in VARIANTS if w == weight_m.group(1) and st == style_m.group(1)),
            None,
        )
        if not suffix:
            continue
        dest = OUT / f"{local}-{suffix}.ttf"
        if dest.exists() and dest.stat().st_size > 10_000:
            continue
        try:
            urllib.request.urlretrieve(url_m.group(1), dest)
            print(f"  {local}-{suffix}: {dest.stat().st_size // 1024} KB")
            done += 1
        except Exception as e:
            print(f"  FAIL {local}-{suffix}: {e}")
            failed += 1

print(f"\nDone: {done} downloaded, {failed} failed")
