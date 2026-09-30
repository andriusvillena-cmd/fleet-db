"""An interactive SQL prompt over fleet.db. Nothing to install.

    python sql.py

Type a query, end it with ';' and press Enter. A query can span several lines.

    .tables            list the tables
    .schema samples    show how a table is defined
    .peek runs         the first five rows of a table
    .q                 quit
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "fleet.db"


def print_table(cursor) -> None:
    headers = [d[0] for d in cursor.description]
    rows = [["NULL" if v is None else str(v) for v in row] for row in cursor.fetchall()]

    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(v)) for w, v in zip(widths, row)]

    print("  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows[:200]:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths)))
    if len(rows) > 200:
        print(f"  ... {len(rows) - 200} more rows not shown")
    print(f"  ({len(rows)} row{'s' if len(rows) != 1 else ''})")


def dot_command(con: sqlite3.Connection, line: str) -> bool:
    """Handle .tables / .schema / .peek / .q. Returns False to quit."""
    parts = line.split()
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else None

    if cmd in (".q", ".quit", ".exit"):
        return False

    if cmd == ".tables":
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        for (name,) in rows:
            (n,) = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
            print(f"  {name:<12} {n:>6} rows")

    elif cmd == ".schema":
        if not arg:
            print("  .schema <table>")
            return True
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (arg,)
        ).fetchone()
        print(row[0] if row else f"  no table called {arg}")

    elif cmd == ".peek":
        if not arg:
            print("  .peek <table>")
            return True
        print_table(con.execute(f"SELECT * FROM {arg} LIMIT 5"))

    else:
        print(f"  unknown command: {cmd}")

    return True


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"{DB.name} not found - run build_db.py first")

    con = sqlite3.connect(DB)
    print(f"{DB.name}   .tables  .schema <t>  .peek <t>  .q to quit")
    print("end a query with ; and press Enter\n")

    buffer = ""
    while True:
        try:
            line = input("... " if buffer else "sql> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        if not buffer and line.startswith("."):
            if not dot_command(con, line):
                break
            continue

        buffer = f"{buffer}\n{line}" if buffer else line
        if not buffer.rstrip().endswith(";"):
            continue

        query, buffer = buffer.rstrip().rstrip(";"), ""
        try:
            cursor = con.execute(query)
            if cursor.description:
                print_table(cursor)
            else:
                con.commit()
                print(f"  ok, {cursor.rowcount} row(s) affected")
        except sqlite3.Error as exc:
            print(f"  {type(exc).__name__}: {exc}", file=sys.stderr)
        print()

    con.close()


if __name__ == "__main__":
    main()
