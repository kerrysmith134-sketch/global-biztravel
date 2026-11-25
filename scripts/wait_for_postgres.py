#!/usr/bin/env python
"""Wait for an authenticated PostgreSQL TCP connection from the host."""

from __future__ import annotations

import sys
import time

import psycopg

DATABASE_URL = "postgresql://gbt_owner:gbt_owner_pw@127.0.0.1:55432/gbt_os"
MAX_ATTEMPTS = 60


def main() -> int:
    last_error: psycopg.OperationalError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with psycopg.connect(DATABASE_URL, connect_timeout=2) as connection:
                connection.execute("SELECT 1")
            print("db ready on localhost:55432 (authenticated TCP query succeeded)")
            return 0
        except psycopg.OperationalError as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(1)

    print(
        f"PostgreSQL did not accept authenticated TCP connections on localhost:55432 "
        f"after {MAX_ATTEMPTS} attempts: {last_error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
