"""Alembic environment. The database comes from config.DATABASE_URL (HRMS_DATABASE_URL)."""
from alembic import context

import models  # noqa: F401  (imports all tables onto Base.metadata)

target_metadata = models.Base.metadata


def _configure(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",   # SQLite cannot ALTER most things in place
        compare_type=True,
    )


def run_migrations_online():
    # Re-use a connection handed in by code (manage.py / app start-up / tests); else open our own.
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    with models.engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:      # table rebuilds (batch mode) must not trip over foreign keys mid-way
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()
        if sqlite:
            bad = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError(f"Foreign key problems after migration: {bad[:5]}")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


run_migrations_online()
