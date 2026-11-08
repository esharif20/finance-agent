"""Per-user settings (0024): for now the Assistant's model, "fast" or
"careful". None means the user never chose; the default lives with the models
(`assistant.ollama.DEFAULT_CHOICE`)."""

from datetime import UTC, datetime
from typing import Literal, get_args

from sqlalchemy import Connection

from finance_agent.storage import Scoped, schema

Choice = Literal["fast", "careful"]
CHOICES: tuple[Choice, ...] = get_args(Choice)
_S = schema.assistant_setting


class Settings:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def assistant_model(self) -> Choice | None:
        row = self._db.execute(self._db.select(_S, _S.c.model)).scalar()
        return row if row in CHOICES else None

    def set_assistant_model(self, choice: Choice) -> None:
        if choice not in CHOICES:
            raise ValueError("unknown Assistant model choice")
        now = datetime.now(UTC)
        changed = self._db.execute(
            self._db.update(_S).values(model=choice, updated_at=now)
        ).rowcount
        if not changed:
            self._db.insert(_S, model=choice, updated_at=now)
