"""Replaces LINKER_DB with the database the extractor's CI last published.

    uv run python -m linker.fetch owner/keiyoushi-extractor

The new file is written beside the old one and swapped in with a rename, so the running site
never reads a half-written database; it notices the new file and reopens it.
"""

import gzip
import os
import shutil
import sys

import httpx

from linker.db import DB_PATH


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else os.environ["LINKER_DB_REPO"]
    url = f"https://github.com/{repo}/releases/download/db/linker.db.gz"
    tmp_gz = DB_PATH.with_suffix(".db.gz.part")
    tmp = DB_PATH.with_suffix(".db.part")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
        r.raise_for_status()
        with tmp_gz.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    with gzip.open(tmp_gz) as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    tmp_gz.unlink()
    os.replace(tmp, DB_PATH)
    print(f"{DB_PATH} updated from {url}")


if __name__ == "__main__":
    main()
