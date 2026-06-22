from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass(slots=True)
class RenderRequestState:
    request_id: str
    scope: str
    node_id: str
    frame: int
    priority: str
    started: float = field(default_factory=time.time)
    completed: float | None = None
    aborted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class RenderScheduler:
    def __init__(self, max_requests: int = 256) -> None:
        self.max_requests = max(1, int(max_requests))
        self._lock = RLock()
        self._latest_by_scope: dict[str, str] = {}
        self._requests: OrderedDict[str, RenderRequestState] = OrderedDict()

    def begin(
        self,
        request_id: str,
        *,
        scope: str,
        node_id: str,
        frame: int,
        priority: str,
        cancel_before: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RenderRequestState:
        with self._lock:
            if cancel_before:
                state = self._requests.get(cancel_before)
                if state is not None:
                    state.aborted = True

            previous_id = self._latest_by_scope.get(scope)
            if previous_id and previous_id != request_id and priority == "interactive":
                previous = self._requests.get(previous_id)
                if previous is not None and previous.completed is None:
                    previous.aborted = True

            state = self._requests.get(request_id)
            if state is None:
                state = RenderRequestState(
                    request_id=request_id,
                    scope=scope,
                    node_id=node_id,
                    frame=int(frame),
                    priority=priority,
                    metadata=metadata or {},
                )
                self._requests[request_id] = state
            else:
                state.scope = scope
                state.node_id = node_id
                state.frame = int(frame)
                state.priority = priority
                state.aborted = False
                state.completed = None
                state.metadata = metadata or state.metadata
                self._requests.move_to_end(request_id)

            self._latest_by_scope[scope] = request_id
            self._prune_locked()
            return state

    def is_current(self, scope: str, request_id: str) -> bool:
        with self._lock:
            state = self._requests.get(request_id)
            return state is not None and not state.aborted and self._latest_by_scope.get(scope) == request_id

    def complete(self, request_id: str) -> None:
        with self._lock:
            state = self._requests.get(request_id)
            if state is not None:
                state.completed = time.time()

    def abort(self, request_id: str) -> None:
        with self._lock:
            state = self._requests.get(request_id)
            if state is not None:
                state.aborted = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests = [
                {
                    "request_id": state.request_id,
                    "scope": state.scope,
                    "node_id": state.node_id,
                    "frame": state.frame,
                    "priority": state.priority,
                    "started": state.started,
                    "completed": state.completed,
                    "aborted": state.aborted,
                    "metadata": state.metadata,
                }
                for state in self._requests.values()
            ]
            return {
                "latest_by_scope": dict(self._latest_by_scope),
                "requests": requests,
                "active": [item for item in requests if item["completed"] is None and not item["aborted"]],
            }

    def _prune_locked(self) -> None:
        while len(self._requests) > self.max_requests:
            request_id, _state = self._requests.popitem(last=False)
            self._latest_by_scope = {
                scope: latest_id
                for scope, latest_id in self._latest_by_scope.items()
                if latest_id != request_id
            }
