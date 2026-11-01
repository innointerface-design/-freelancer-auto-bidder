"""Resolve local SQLite storage consistently without moving an existing database."""
import os
from pathlib import Path

from sqlalchemy.engine import make_url


def resolve_database_url(value, base):
    if not value.startswith('sqlite'):
        return value
    url = make_url(value)
    database = url.database
    if not database or database == ':memory:' or os.path.isabs(database):
        return value
    canonical = Path(base).absolute() / database
    previous = Path(database).absolute()
    # Older versions resolved against CWD. Do not silently abandon that file
    # or select another populated database when the operator changes folders.
    if previous != canonical and previous.exists():
        if not canonical.exists() or not os.path.samefile(previous, canonical):
            raise RuntimeError(
                'Relative DATABASE_URL identifies another existing SQLite file in this working directory. '
                'Set an absolute DATABASE_URL to the intended existing database before starting; '
                'no database was moved or replaced.'
            )
    return url.set(database=str(canonical)).render_as_string(hide_password=False)
