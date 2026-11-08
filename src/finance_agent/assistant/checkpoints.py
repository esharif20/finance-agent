"""The graph's checkpoint saver (#139, ADR 0008): LangGraph checkpoints kept in
the encrypted database through `ledger.conversation`, one user's threads only.
It stores each checkpoint whole, as LangGraph's own SQLite saver does.

LangGraph saves from its own threads, but a SQLite connection must not be used
from two threads at once, so saves are queued and written by `flush` on the
thread that owns the connection (after each run, or before any read)."""

import threading
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

from finance_agent.ledger.conversation import CheckpointRow, Conversations, WriteRow


def _conf(c: RunnableConfig) -> dict[str, Any]:
    return dict(c.get("configurable") or {})


class LedgerSaver(BaseCheckpointSaver[str]):
    def __init__(self, store: Conversations) -> None:
        super().__init__()
        self._store = store
        self._owner = threading.get_ident()
        self._lock = threading.Lock()
        self._queued: list[Callable[[], None]] = []

    def flush(self) -> None:
        """Write the queued saves; only on the connection's own thread."""
        if threading.get_ident() != self._owner:
            raise RuntimeError("checkpoints are written on the owning thread only")
        with self._lock:
            queued, self._queued = self._queued, []
        for save in queued:
            save()

    def _queue(self, save: Callable[[], None]) -> None:
        with self._lock:
            self._queued.append(save)

    def _tuple(self, r: CheckpointRow) -> CheckpointTuple:
        writes = self._store.writes(r.thread_id, r.checkpoint_ns, r.checkpoint_id)
        conf = {
            "thread_id": r.thread_id,
            "checkpoint_ns": r.checkpoint_ns,
            "checkpoint_id": r.checkpoint_id,
        }
        parent = (
            {"configurable": {**conf, "checkpoint_id": r.parent_id}}
            if r.parent_id
            else None
        )
        return CheckpointTuple(
            config={"configurable": conf},
            checkpoint=self.serde.loads_typed(r.checkpoint),
            metadata=self.serde.loads_typed(r.metadata),
            parent_config=parent,  # type: ignore[arg-type]
            pending_writes=[
                (w.task_id, w.channel, self.serde.loads_typed(w.value)) for w in writes
            ],
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        self.flush()
        c = _conf(config)
        rows = self._store.checkpoints(
            c["thread_id"],
            c.get("checkpoint_ns", ""),
            get_checkpoint_id(config),
            limit=1,
        )
        return self._tuple(rows[0]) if rows else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return  # never across threads
        self.flush()
        c = _conf(config)
        rows = self._store.checkpoints(
            c["thread_id"],
            c.get("checkpoint_ns"),
            get_checkpoint_id(config),
            before=get_checkpoint_id(before) if before else None,
        )
        n = 0
        for r in rows:
            t = self._tuple(r)
            if filter and any(t.metadata.get(k) != v for k, v in filter.items()):
                continue
            yield t
            n += 1
            if limit is not None and n >= limit:
                return

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        c = _conf(config)
        thread, ns = c["thread_id"], c.get("checkpoint_ns", "")
        row = CheckpointRow(
            thread,
            ns,
            checkpoint["id"],
            c.get("checkpoint_id"),
            self.serde.dumps_typed(checkpoint),
            self.serde.dumps_typed(get_checkpoint_metadata(config, metadata)),
        )
        self._queue(lambda: self._store.put_checkpoint(row))
        return {
            "configurable": {
                "thread_id": thread,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        c = _conf(config)
        rows = [
            WriteRow(
                task_id,
                WRITES_IDX_MAP.get(ch, i),
                ch,
                self.serde.dumps_typed(v),
                task_path,
            )
            for i, (ch, v) in enumerate(writes)
        ]
        thread, ns, cid = c["thread_id"], c.get("checkpoint_ns", ""), c["checkpoint_id"]
        self._queue(lambda: self._store.put_writes(thread, ns, cid, rows))

    def delete_thread(self, thread_id: str) -> None:
        self.flush()
        self._store.delete_thread(thread_id)
