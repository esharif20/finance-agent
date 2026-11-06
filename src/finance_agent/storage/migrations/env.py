"""Alembic env. Online only: `open_db` passes a connection; the alembic CLI passes
`-x db=<path>` (see alembic.ini)."""

from alembic import context
from sqlalchemy import Connection, create_engine

from finance_agent.storage.schema import metadata


def _run(conn: Connection) -> None:
    context.configure(connection=conn, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


conn = context.config.attributes.get("connection")
if conn is not None:
    _run(conn)
else:
    db = context.get_x_argument(as_dictionary=True).get("db")
    if not db:
        raise SystemExit("pass -x db=<path to a scratch sqlite file>")
    with create_engine(f"sqlite:///{db}").begin() as c:
        _run(c)
