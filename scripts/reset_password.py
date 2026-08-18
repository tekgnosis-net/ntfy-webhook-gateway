#!/usr/bin/env python3
"""Reset or clear the admin password directly in the gateway database.

For lockout recovery. Inside the container:
    docker compose exec ntfy-webhook-gateway python scripts/reset_password.py --set NEWPASS
    docker compose exec ntfy-webhook-gateway python scripts/reset_password.py --clear
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password  # noqa: E402
from app.config import db_path  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reset the gateway admin password.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--clear", action="store_true",
                       help="remove the stored password (falls back to ADMIN_PASSWORD env or open mode)")
    group.add_argument("--set", dest="new_password", help="set a new password")
    args = parser.parse_args(argv)

    con = sqlite3.connect(db_path())
    if args.clear:
        con.execute("DELETE FROM settings WHERE key='admin_password_hash'")
        print("Password cleared.")
    else:
        con.execute(
            "INSERT INTO settings(key, value) VALUES('admin_password_hash', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (hash_password(args.new_password),),
        )
        print("Password updated.")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
