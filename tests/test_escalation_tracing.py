"""离线测试：RAG 低置信转人工闭环 + DB 迁移 + 链路追踪

无需网络 / 阿里云 API。验证：
1. escalate_to_human 工具产出结构化转人工结果；
2. tracing 模块 RequestTrace / span / TraceStore 工作正常；
3. _is_fallback_reply 对兜底话术的判定；
4. 老库 ALTER 迁移补齐 escalated / escalations 列；
5. _stream_escalation 端到端：发出 escalation 事件 + 落库 escalated 工单。
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import types

from backend.tools.system_tools import escalate_to_human
from backend.tracing import RequestTrace, TraceStore, trace_store
from backend.api import _is_fallback_reply, _stream_escalation
from backend.db import repository as db_mod


def test_escalate_tool():
    esc = escalate_to_human.invoke({"reason": "RAG检索置信度过低", "priority": "high"})
    assert esc["escalated"] is True
    assert isinstance(esc["ticket_id"], str) and esc["ticket_id"].startswith("TKT-")
    assert esc["estimated_response_time"]
    print("[1] escalate_to_human 工具产出结构正确")


def test_tracing():
    tr = RequestTrace("r1", "s1", "u1", "你好")
    with tr.span("routing", agent="tech_support") as sp:
        sp.close(confidence=0.9)
    tr.add_event("retrieval", top_confidence=0.2)
    tr.escalated = True
    tr.escalation_reason = "low"
    d = tr.to_dict()
    assert d["escalated"] is True
    assert d["spans"][0]["name"] == "routing"
    assert d["spans"][0]["duration_ms"] >= 0
    trace_store.push(tr)
    assert any(t["escalated"] for t in trace_store.recent())
    print("[2] tracing 模块工作正常")


def test_fallback_detection():
    assert _is_fallback_reply("") is True
    assert _is_fallback_reply("建议联系人工客服") is True
    assert _is_fallback_reply("您的订单已发货，预计明天送达") is False
    print("[3] _is_fallback_reply 判定正确")


def test_migration():
    tmp = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(tmp)
    # 模拟"改动前"已部署库：含全部旧列，但缺新增的 escalated / escalations 等列
    conn.execute("""CREATE TABLE tickets (
        ticket_id TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, user_message TEXT,
        response TEXT, agent_used TEXT, confidence REAL, actions_taken TEXT,
        duration REAL, created_at TEXT)""")
    conn.execute("""CREATE TABLE daily_metrics (
        stat_date TEXT PRIMARY KEY, total_requests INTEGER DEFAULT 0,
        success_requests INTEGER DEFAULT 0, total_time REAL DEFAULT 0,
        agent_stats TEXT DEFAULT '{}')""")
    conn.commit()
    conn.close()

    db = db_mod.init_database(tmp)  # init_schema(IF NOT EXISTS 保留旧表) + _migrate 补齐列
    ticket_cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(tickets)").fetchall()}
    assert "escalated" in ticket_cols and "escalated_reason" in ticket_cols \
        and "priority" in ticket_cols and "human_ticket_id" in ticket_cols
    dm_cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(daily_metrics)").fetchall()}
    assert "escalations" in dm_cols
    print("[4] 老库 ALTER 迁移补齐 escalated / escalations 列")


class FakeMemory:
    async def save_message(self, *a, **k):
        return None


async def _run_escalation():
    request = types.SimpleNamespace(session_id="s1", user_id="u1", user_message="卡死了怎么办")
    trace = RequestTrace("r2", "s1", "u1", "卡死了怎么办")
    chunks = []
    async for c in _stream_escalation(
        trace, "RAG检索置信度过低（0.12 < 0.35），自动转人工", "high",
        request, FakeMemory(), 0.0, "tech_support", 0.12, ["路由到技术支持专家"]
    ):
        chunks.append(c)
    return chunks


def test_stream_escalation_e2e():
    chunks = asyncio.run(_run_escalation())
    events = [json.loads(c[len("data: "):]) for c in chunks if c.startswith("data: ")]
    types = [e["type"] for e in events]
    assert "escalation" in types, f"缺少 escalation 事件: {types}"
    assert "token" in types
    done = [e for e in events if e["type"] == "done"][0]
    assert done.get("escalated") is True
    # 工单落库
    tickets = db_mod.get_ticket_repo().list(limit=10)
    assert any(t["escalated"] == 1 for t in tickets), "转人工工单未落库"
    # 统计计 escalations
    summary = db_mod.get_stats_repo().get_summary()
    assert summary["escalations"] >= 1, "统计未累计 escalations"
    print("[5] _stream_escalation 端到端：escalation 事件 + 落库 + 统计累计")


if __name__ == "__main__":
    test_escalate_tool()
    test_tracing()
    test_fallback_detection()
    test_migration()
    test_stream_escalation_e2e()
    print("\nALL ESCALATION / TRACING TESTS PASSED ✅")
