"""轻量级链路追踪（Observability）

设计目标：
- 为每次请求建立一条 RequestTrace，记录路由决策、检索、Agent 执行、转人工、落库等关键
  span 与事件，用于问题定位与效果分析（简历可讲"可观测性"）。
- 进程内 TraceStore 环形缓冲（演示用，零外部依赖）；生产可平滑替换为
  OpenTelemetry / Langfuse 等（仅需替换 push/log 实现）。
- 每条 trace 结束自动输出一行结构化 JSON 日志，便于 ELK / Grafana 采集。
"""
import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("smartcs.trace")


class TraceSpan:
    """单次操作的耗时记录（如一次路由 LLM 调用、一次检索）。"""

    def __init__(self, name: str, meta: Dict[str, Any] = None):
        self.name = name
        self.start = time.time()
        self.end: Optional[float] = None
        self.meta = meta or {}

    def close(self, **meta) -> None:
        self.end = time.time()
        if meta:
            self.meta.update(meta)

    @property
    def duration_ms(self) -> float:
        if self.end is None:
            return 0.0
        return round((self.end - self.start) * 1000, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "duration_ms": self.duration_ms, "meta": self.meta}


class _SpanGuard:
    """`with trace.span(...)` 上下文管理器，自动开始/结束 span。"""

    def __init__(self, trace: "RequestTrace", name: str, meta: Dict[str, Any]):
        self.trace = trace
        self.span = TraceSpan(name, meta)

    def __enter__(self) -> TraceSpan:
        return self.span

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.span.close(error=str(exc))
        else:
            self.span.close()
        with self.trace._lock:
            self.trace.spans.append(self.span)


class RequestTrace:
    """一次用户请求的完整链路追踪。"""

    def __init__(self, request_id: str, session_id: str, user_id: str, message: str):
        self.request_id = request_id
        self.session_id = session_id
        self.user_id = user_id
        self.message = message
        self.start = time.time()
        self.spans: List[TraceSpan] = []
        self.events: List[Dict[str, Any]] = []
        self.escalated = False
        self.escalation_reason: Optional[str] = None
        self._lock = threading.Lock()

    def span(self, name: str, **meta) -> _SpanGuard:
        return _SpanGuard(self, name, meta)

    def add_event(self, name: str, **meta) -> None:
        with self._lock:
            self.events.append({
                "name": name,
                "ts_ms": round((time.time() - self.start) * 1000, 2),
                **meta,
            })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "message": self.message,
            "total_ms": round((time.time() - self.start) * 1000, 2),
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
            "spans": [s.to_dict() for s in self.spans],
            "events": self.events,
        }

    def log(self) -> None:
        logger.info("TRACE %s", json.dumps(self.to_dict(), ensure_ascii=False))


class TraceStore:
    """进程内链路追踪环形缓冲（演示用，生产可换 OpenTelemetry/Langfuse）。"""

    def __init__(self, max_size: int = 200):
        self._max = max_size
        self._buf: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def push(self, trace: RequestTrace) -> None:
        d = trace.to_dict()
        with self._lock:
            self._buf.append(d)
            if len(self._buf) > self._max:
                self._buf = self._buf[-self._max:]

    def recent(self, n: int = 50, escalated_only: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._buf)
        if escalated_only:
            items = [t for t in items if t.get("escalated")]
        return items[-n:][::-1]


# 全局单例（演示用进程内缓冲）
trace_store = TraceStore()
