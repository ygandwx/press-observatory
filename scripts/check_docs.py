"""Check inline relative file links in the project's Markdown documents."""

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^\s)]+)\)")


def main():
    documents = sorted(ROOT.glob("*.md"))
    for directory in ("docs", ".github"):
        documents.extend(sorted((ROOT / directory).rglob("*.md")))

    errors = []
    checked = 0
    for document in documents:
        fence = None
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if marker == fence:
                    fence = None
                elif fence is None:
                    fence = marker
                continue
            if fence:
                continue
            for target in LINK.findall(line):
                url = urlsplit(target.strip("<>"))
                if url.scheme or url.netloc or not url.path:
                    continue
                destination = (document.parent / unquote(url.path)).resolve()
                checked += 1
                if not destination.is_relative_to(ROOT) or not destination.is_file():
                    errors.append(f"{document.relative_to(ROOT)}:{number}: {target}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Checked {checked} local file links in {len(documents)} Markdown files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
