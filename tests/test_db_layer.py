"""数据层离线冒烟测试（无网络 / 无 langchain 依赖）

验证：建表、seed 幂等、订单查询、排序索引、工单落库、统计聚合。
运行：python tests/test_db_layer.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.repository import init_database, get_order_repo, get_ticket_repo, get_stats_repo
from backend.db.seed import run_seed


def test_db_layer():
    tmp = tempfile.mkdtemp()
    db = init_database(os.path.join(tmp, "test.db"))

    # 1. seed 幂等
    n1 = run_seed(db, count=200)
    n2 = run_seed(db, count=200)  # 已存在应跳过
    assert n1 == 200, f"seed 应写入 200 条，实际 {n1}"
    assert n2 == 0, f"重复 seed 应为 0，实际 {n2}"

    repo = get_order_repo()
    assert repo.count() == 200

    # 2. 按订单号查询
    o = repo.get_by_id("100000")
    assert o is not None, "首条合成订单应存在"
    assert set(o.keys()) >= {"order_id", "status", "amount", "refundable", "product"}
    assert o["status"] in ("已签收", "运输中", "待支付", "已支付", "已发货", "已完成", "退款中")

    # 3. 不存在订单返回 None
    assert repo.get_by_id("999999") is None

    # 4. 工单落库 + 列表查询
    repo_t = get_ticket_repo()
    repo_t.create({
        "ticket_id": "T1", "session_id": "s1", "user_id": "u1",
        "user_message": "查订单", "response": "已签收", "agent_used": "order_query",
        "confidence": 0.95, "actions_taken": '["查询订单"]', "duration": 0.12,
        "created_at": "2026-07-13 10:00:00",
        "escalated": 0, "escalated_reason": None, "priority": None, "human_ticket_id": None,
    })
    repo_t.create({
        "ticket_id": "T2", "session_id": "s2", "user_id": "u2",
        "user_message": "退款", "response": "已批准", "agent_used": "refund",
        "confidence": 0.9, "actions_taken": '["退款审批"]', "duration": 0.3,
        "created_at": "2026-07-13 10:01:00",
        "escalated": 0, "escalated_reason": None, "priority": None, "human_ticket_id": None,
    })
    all_t = repo_t.list(limit=10, offset=0)
    assert len(all_t) == 2
    # 按 agent 过滤
    refund_t = repo_t.list(agent_type="refund")
    assert len(refund_t) == 1 and refund_t[0]["agent_used"] == "refund"
    assert repo_t.count(agent_type="refund") == 1

    # 5. 统计聚合
    repo_s = get_stats_repo()
    repo_s.increment_daily("order_query", 0.12)
    repo_s.increment_daily("refund", 0.3)
    repo_s.increment_daily("refund", 0.25)
    s = repo_s.get_summary()
    assert s["total"] == 3, s
    assert s["agent_stats"].get("refund") == 2
    assert s["agent_stats"].get("order_query") == 1
    assert s["success_rate"] == 100.0

    print("✓ ALL DB LAYER TESTS PASSED")


if __name__ == "__main__":
    test_db_layer()
