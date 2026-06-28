from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from materialhack_memory import to_jsonable
from materialhack_memory.models import JsonValue


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkbenchEvent:
    event_id: str
    event_type: str
    created_at: str
    run_id: str | None = None
    job_id: str | None = None
    loop_id: str | None = None
    data: dict[str, JsonValue] = field(default_factory=dict)

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "run_id": self.run_id,
            "job_id": self.job_id,
            "loop_id": self.loop_id,
            "data": self.data,
        }


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events_by_run: dict[str, list[WorkbenchEvent]] = {}
        self._subscribers: dict[str, list[queue.Queue[WorkbenchEvent]]] = {}

    def publish(
        self,
        event_type: str,
        *,
        run_id: str | None = None,
        job_id: str | None = None,
        loop_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> WorkbenchEvent:
        event = WorkbenchEvent(
            event_id=f"evt_{uuid4().hex}",
            event_type=event_type,
            created_at=utc_now_iso(),
            run_id=run_id,
            job_id=job_id,
            loop_id=loop_id,
            data=to_jsonable(data or {}),
        )
        if run_id is None:
            return event

        with self._lock:
            self._events_by_run.setdefault(run_id, []).append(event)
            subscribers = tuple(self._subscribers.get(run_id, ()))

        for subscriber in subscribers:
            subscriber.put(event)
        return event

    def events_for_run(self, run_id: str) -> tuple[WorkbenchEvent, ...]:
        with self._lock:
            return tuple(self._events_by_run.get(run_id, ()))

    def subscribe(self, run_id: str) -> queue.Queue[WorkbenchEvent]:
        subscriber: queue.Queue[WorkbenchEvent] = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(run_id, []).append(subscriber)
        return subscriber

    def unsubscribe(self, run_id: str, subscriber: queue.Queue[WorkbenchEvent]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(run_id)
            if not subscribers:
                return
            self._subscribers[run_id] = [item for item in subscribers if item is not subscriber]
