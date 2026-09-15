"""Write the JSON a running aperture reads to learn there is a newer release.

    python tools/build_update_manifest.py                 # to stdout
    python tools/build_update_manifest.py -o latest.json  # to a file

Upload the result to brand.UPDATE_URL (https://lucya.sh/aperture/latest.json)
AFTER the release is pushed. What the fields mean, and what a running copy
does with them, is in aperture/update_check.py:

  version  brand.VERSION
  date     the newest CHANGELOG.md entry's date (the test net holds the two
           to each other, so they are the same release)
  url      that entry on GitHub
  notes    the entry's first paragraph, its one-line summary
"""

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})\s*$", re.M)


def _brand():
    # brand.py imports nothing; loading it by path keeps the package's own
    # startup (the environment, the directories) out of a build step
    spec = importlib.util.spec_from_file_location("brand", ROOT / "aperture" / "brand.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _anchor(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation out, spaces to
    hyphens -- `1.8.0 — 2026-09-15` -> `180--2026-09-15`."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def build() -> dict:
    brand = _brand()
    text = CHANGELOG.read_text(encoding="utf-8")
    match = HEADING.search(text)
    if not match or match.group(1) != brand.VERSION:
        sys.exit("CHANGELOG.md's newest entry is not %s" % brand.VERSION)
    rest = text[match.end():].lstrip("\n")
    notes = " ".join(rest.split("\n\n", 1)[0].split())
    heading = match.group(0)[3:].strip()
    return {
        "version": brand.VERSION,
        "date": match.group(2),
        "url": "%s/blob/main/CHANGELOG.md#%s" % (brand.REPO_URL, _anchor(heading)),
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args()
    body = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
        print("wrote %s" % args.output)
    else:
        sys.stdout.write(body)


if __name__ == "__main__":
    main()
