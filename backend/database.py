"""Database connections for the local prototype and the Supabase deployment."""
import os
from contextlib import contextmanager

import psycopg


def using_postgres():
    return os.getenv('DOCUMENT_BACKEND', 'sqlite') == 'postgres'


class Row(dict):
    """Named rows with positional access for existing scalar queries."""
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def rows(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


class PostgresSession:
    dialect = 'postgres'

    def __init__(self, connection):
        self.raw = connection

    def execute(self, sql, parameters=()):
        # All statements are application-owned. Values always use bound params.
        return self.raw.execute(sql.replace('?', '%s'), parameters)


@contextmanager
def postgres_connection():
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=5,
                         options='-c statement_timeout=30000', row_factory=rows) as connection:
        connection.execute('SET LOCAL search_path TO campus, public, extensions')
        # Preserve IEEE-754 values on text-protocol reads, including index times.
        connection.execute('SET LOCAL extra_float_digits=3')
        yield PostgresSession(connection)
