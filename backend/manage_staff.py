"""Apply private role schema and bootstrap the first confirmed Auth administrator.

No identities, passwords or email messages are created by this operator command.
"""
import argparse
from pathlib import Path

from fastapi import HTTPException
from backend import auth, database, main


def migrate():
    if not database.using_postgres():
        raise ValueError('Set DOCUMENT_BACKEND=postgres first')
    sql = (Path(__file__).resolve().parents[1] / 'supabase/migrations/005_staff_auth.sql').read_text(encoding='utf-8')
    with main.connect() as db:
        db.raw.execute(sql)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['migrate', 'bootstrap'])
    parser.add_argument('--email', help='Existing confirmed Supabase Auth email')
    args = parser.parse_args()
    if args.action == 'bootstrap' and not args.email:
        parser.error('--email is required')
    try:
        migrate()
        if args.action == 'bootstrap':
            with main.connect() as db:
                auth.grant_member(db, args.email.strip(), 'admin', True, bootstrap=True)
            print('First administrator assigned. Sign in with that Supabase Auth account.')
        else:
            print('Private staff role migration applied.')
    except HTTPException as exc:
        parser.exit(1, exc.detail + '\n')
    except Exception as exc:
        parser.exit(1, 'Staff setup failed ('+type(exc).__name__+'). Check database configuration; credentials are not logged.\n')
