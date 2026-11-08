"""Conversation storage (#139): the rows behind the Assistant's LangGraph
checkpoints, in the encrypted database, scoped to one user. Opaque bytes in and
out; `assistant.checkpoints` turns them into a LangGraph checkpoint saver.

A conversation lasts one session (DATA_POLICY): `delete_thread` ends it and
`purge_idle` drops threads untouched for a day. Nothing here is logged."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, func

from finance_agent.storage import Scoped, schema

_C, _W = schema.assistant_checkpoint, schema.assistant_checkpoint_write


@dataclass(frozen=True)
class CheckpointRow:
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    parent_id: str | None
    checkpoint: tuple[str, bytes]  # (serde type, bytes)
    metadata: tuple[str, bytes]


@dataclass(frozen=True)
class WriteRow:
    task_id: str
    idx: int
    channel: str
    value: tuple[str, bytes]
    task_path: str


class Conversations:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def put_checkpoint(self, row: CheckpointRow) -> None:
        self._db.execute(
            self._db.delete(_C).where(
                _C.c.thread_id == row.thread_id,
                _C.c.checkpoint_ns == row.checkpoint_ns,
                _C.c.checkpoint_id == row.checkpoint_id,
            )
        )
        self._db.insert(
            _C,
            thread_id=row.thread_id,
            checkpoint_ns=row.checkpoint_ns,
            checkpoint_id=row.checkpoint_id,
            parent_id=row.parent_id,
            checkpoint_type=row.checkpoint[0],
            checkpoint=row.checkpoint[1],
            metadata_type=row.metadata[0],
            metadata=row.metadata[1],
            created_at=datetime.now(UTC),
        )

    def checkpoints(
        self,
        thread_id: str,
        checkpoint_ns: str | None = None,
        checkpoint_id: str | None = None,
        before: str | None = None,
        limit: int | None = None,
    ) -> list[CheckpointRow]:
        """Newest first (checkpoint ids sort by time)."""
        q = self._db.select(_C).where(_C.c.thread_id == thread_id)
        if checkpoint_ns is not None:
            q = q.where(_C.c.checkpoint_ns == checkpoint_ns)
        if checkpoint_id is not None:
            q = q.where(_C.c.checkpoint_id == checkpoint_id)
        if before is not None:
            q = q.where(_C.c.checkpoint_id < before)
        q = q.order_by(_C.c.checkpoint_id.desc()).limit(limit)
        return [
            CheckpointRow(
                r.thread_id,
                r.checkpoint_ns,
                r.checkpoint_id,
                r.parent_id,
                (r.checkpoint_type, r.checkpoint),
                (r.metadata_type, r.metadata),
            )
            for r in self._db.execute(q)
        ]

    def put_writes(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        rows: Sequence[WriteRow],
    ) -> None:
        """Store a task's writes; an existing (task, idx) is kept, except for
        LangGraph's special channels (negative idx), which replace."""
        for w in rows:
            key = (
                _W.c.thread_id == thread_id,
                _W.c.checkpoint_ns == checkpoint_ns,
                _W.c.checkpoint_id == checkpoint_id,
                _W.c.task_id == w.task_id,
                _W.c.idx == w.idx,
            )
            if self._db.execute(self._db.select(_W, _W.c.idx).where(*key)).first():
                if w.idx >= 0:
                    continue
                self._db.execute(self._db.delete(_W).where(*key))
            self._db.insert(
                _W,
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
                task_id=w.task_id,
                idx=w.idx,
                channel=w.channel,
                value_type=w.value[0],
                value=w.value[1],
                task_path=w.task_path,
            )

    def writes(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[WriteRow]:
        q = self._db.select(_W).where(
            _W.c.thread_id == thread_id,
            _W.c.checkpoint_ns == checkpoint_ns,
            _W.c.checkpoint_id == checkpoint_id,
        )
        rows = [
            WriteRow(r.task_id, r.idx, r.channel, (r.value_type, r.value), r.task_path)
            for r in self._db.execute(q)
        ]
        return sorted(rows, key=lambda w: (w.task_path, w.task_id, w.idx))

    def has_thread(self, thread_id: str) -> bool:
        q = self._db.select(_C, _C.c.thread_id).where(_C.c.thread_id == thread_id)
        return self._db.execute(q.limit(1)).first() is not None

    def delete_thread(self, thread_id: str) -> int:
        """End a conversation: every checkpoint and write of the thread."""
        self._db.execute(self._db.delete(_W).where(_W.c.thread_id == thread_id))
        res = self._db.execute(self._db.delete(_C).where(_C.c.thread_id == thread_id))
        return res.rowcount

    def purge_idle(self, before: datetime) -> int:
        """Delete threads whose newest checkpoint is older than `before`."""
        last = func.max(_C.c.created_at).label("last")
        q = self._db.select(_C, _C.c.thread_id, last).group_by(_C.c.thread_id)
        idle = [
            r.thread_id
            for r in self._db.execute(q)
            if r.last.replace(tzinfo=UTC) < before
        ]
        for t in idle:
            self.delete_thread(t)
        return len(idle)
