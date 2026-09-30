"""Run the queries in queries.sql against fleet.db and print each result.

Usage:
    python run_queries.py                 # all of them
    python run_queries.py braking_summary # just one
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

NAME = re.compile(r"^--\s*name:\s*(\S+)\s*$", re.MULTILINE)


def split_queries(text: str) -> dict[str, str]:
    """Split queries.sql on its '-- name:' markers, keeping source order."""
    marks = list(NAME.finditer(text))
    if not marks:
        raise SystemExit("queries.sql has no '-- name:' markers")
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[m.end() : end].strip()
    return out


def show(con: sqlite3.Connection, name: str, sql: str) -> None:
    cur = con.execute(sql)
    headers = [d[0] for d in cur.description]
    rows = [["" if v is None else str(v) for v in row] for row in cur.fetchall()]

    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(v)) for w, v in zip(widths, row)]

    print(f"\n{name}")
    print("  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths)))
    print(f"  ({len(rows)} row{'s' if len(rows) != 1 else ''})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="query names to run (default: all)")
    parser.add_argument("--db", default="fleet.db")
    parser.add_argument("--sql", default="queries.sql")
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(f"{args.db} not found - run build_db.py first")

    queries = split_queries(Path(args.sql).read_text(encoding="utf-8"))
    wanted = args.names or list(queries)

    unknown = [n for n in wanted if n not in queries]
    if unknown:
        print(f"unknown query: {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(queries)}", file=sys.stderr)
        raise SystemExit(1)

    con = sqlite3.connect(args.db)
    for name in wanted:
        show(con, name, queries[name])
    con.close()


if __name__ == "__main__":
    main()
