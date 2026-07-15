"""数据访问层：SQLite 连接管理 + Repository 模式

设计要点：
- 单文件 SQLite（无需独立服务进程），适合简历 Demo 与本地运行，且天然持久化。
- Repository 模式把"业务查询语义"与"SQL 实现细节"解耦，Agent / 工具只依赖接口，
  后续可无缝替换为 PostgreSQL / MySQL（仅改 Repository 实现，不动调用方）。
- 同步 sqlite3 + 全局锁保证线程安全；生产环境可替换为 aiosqlite / asyncpg 等异步驱动。
"""
import os
import json
import sqlite3
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

# 默认库路径：项目根目录 / data / smart_cs.db
DEFAULT_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "smart_cs.db")
)
_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

_db_lock = threading.Lock()
_db: Optional["Database"] = None


class Database:
    """SQLite 连接封装：建表、执行、查询。"""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def init_schema(self) -> None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            self._conn.executescript(f.read())
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """兼容已存在库的增量迁移：补齐新增列（SQLite ALTER TABLE ADD COLUMN）。

        新建库由 schema.sql 直接带新列；此处只为老库兜底，保证升级无损。
        """
        ticket_cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(tickets)").fetchall()}
        for col, ddl in [
            ("escalated", "INTEGER NOT NULL DEFAULT 0"),
            ("escalated_reason", "TEXT"),
            ("priority", "TEXT"),
            ("human_ticket_id", "TEXT"),
        ]:
            if col not in ticket_cols:
                self._conn.execute(f"ALTER TABLE tickets ADD COLUMN {col} {ddl}")
        dm_cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(daily_metrics)").fetchall()}
        if "escalations" not in dm_cols:
            self._conn.execute(
                "ALTER TABLE daily_metrics ADD COLUMN escalations INTEGER NOT NULL DEFAULT 0")
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with _db_lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with _db_lock:
            cur = self._conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


def init_database(db_path: str = None) -> Database:
    """初始化（幂等）：进程内仅建一次连接与表结构。"""
    global _db
    if _db is None:
        path = os.path.abspath(db_path) if db_path else DEFAULT_DB_PATH
        _db = Database(path)
        _db.init_schema()
    return _db


def get_db() -> Database:
    if _db is None:
        init_database()
    return _db


# ============ Repository ============

class OrderRepository:
    """订单仓储：封装订单的增删查。"""

    def __init__(self, db: Database = None):
        self.db = db or get_db()

    def get_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM orders WHERE order_id=?", (order_id,))
        return rows[0] if rows else None

    def count(self) -> int:
        rows = self.db.query("SELECT COUNT(*) AS c FROM orders")
        return rows[0]["c"]

    def save(self, order: Dict[str, Any]) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO orders
               (order_id, customer_name, phone, product, amount, status, refundable,
                payment_method, shipping_address, order_date, created_at)
               VALUES (:order_id, :customer_name, :phone, :product, :amount, :status,
                       :refundable, :payment_method, :shipping_address, :order_date, :created_at)""",
            order,
        )

    def update_status(self, order_id: str, status: str) -> None:
        self.db.execute("UPDATE orders SET status=? WHERE order_id=?", (status, order_id))


class TicketRepository:
    """工单仓储：每次对话的落库与查询。"""

    def __init__(self, db: Database = None):
        self.db = db or get_db()

    def create(self, ticket: Dict[str, Any]) -> None:
        self.db.execute(
            """INSERT INTO tickets
               (ticket_id, session_id, user_id, user_message, response, agent_used,
                confidence, actions_taken, duration, created_at,
                escalated, escalated_reason, priority, human_ticket_id)
               VALUES (:ticket_id, :session_id, :user_id, :user_message, :response, :agent_used,
                       :confidence, :actions_taken, :duration, :created_at,
                       :escalated, :escalated_reason, :priority, :human_ticket_id)""",
            ticket,
        )

    def list(self, limit: int = 10, offset: int = 0,
             agent_type: str = None, user_id: str = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM tickets WHERE 1=1"
        params: List[Any] = []
        if agent_type:
            sql += " AND agent_used=?"
            params.append(agent_type)
        if user_id:
            sql += " AND user_id=?"
            params.append(user_id)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self.db.query(sql, params)

    def count(self, agent_type: str = None, user_id: str = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM tickets WHERE 1=1"
        params: List[Any] = []
        if agent_type:
            sql += " AND agent_used=?"
            params.append(agent_type)
        if user_id:
            sql += " AND user_id=?"
            params.append(user_id)
        return self.db.query(sql, params)[0]["c"]


class StatsRepository:
    """统计仓储：每日指标聚合，跨重启持久化。"""

    def __init__(self, db: Database = None):
        self.db = db or get_db()

    def increment_daily(self, agent_type: str, duration: float,
                        success: bool = True, escalations: int = 0,
                        stat_date: str = None) -> None:
        stat_date = stat_date or datetime.now().strftime("%Y-%m-%d")
        with _db_lock:
            conn = self.db._conn
            rows = conn.execute(
                "SELECT total_requests, success_requests, total_time, escalations, agent_stats "
                "FROM daily_metrics WHERE stat_date=?", (stat_date,)
            ).fetchall()
            if rows:
                r = dict(rows[0])
                agent_stats = json.loads(r["agent_stats"] or "{}")
                agent_stats[agent_type] = agent_stats.get(agent_type, 0) + 1
                conn.execute(
                    "UPDATE daily_metrics SET total_requests=?, success_requests=?, "
                    "total_time=?, escalations=?, agent_stats=? WHERE stat_date=?",
                    (r["total_requests"] + 1, r["success_requests"] + (1 if success else 0),
                     r["total_time"] + duration, r["escalations"] + escalations,
                     json.dumps(agent_stats), stat_date),
                )
            else:
                agent_stats = {agent_type: 1}
                conn.execute(
                    "INSERT INTO daily_metrics(stat_date, total_requests, success_requests, "
                    "total_time, escalations, agent_stats) VALUES (?,?,?,?,?,?)",
                    (stat_date, 1, 1 if success else 0, duration, escalations,
                     json.dumps(agent_stats)),
                )
            conn.commit()

    def get_summary(self) -> Dict[str, Any]:
        rows = self.db.query(
            "SELECT SUM(total_requests) AS total, SUM(success_requests) AS success, "
            "SUM(total_time) AS total_time, SUM(escalations) AS escalations FROM daily_metrics"
        )
        totals = rows[0] if rows else {"total": 0, "success": 0, "total_time": 0, "escalations": 0}
        total = totals.get("total") or 0
        success = totals.get("success") or 0
        total_time = totals.get("total_time") or 0.0
        escalations = totals.get("escalations") or 0

        agent_stats: Dict[str, int] = {}
        for r in self.db.query("SELECT agent_stats FROM daily_metrics"):
            try:
                d = json.loads(r["agent_stats"] or "{}")
            except Exception:
                d = {}
            for k, v in d.items():
                agent_stats[k] = agent_stats.get(k, 0) + v

        return {
            "total": total,
            "success": success,
            "success_rate": round(success / total * 100, 1) if total else 0.0,
            "avg_time": round(total_time / total, 2) if total else 0.0,
            "escalations": escalations,
            "agent_stats": agent_stats,
        }


# ============ 便捷访问器 ============

def get_order_repo() -> OrderRepository:
    return OrderRepository(get_db())


def get_ticket_repo() -> TicketRepository:
    return TicketRepository(get_db())


def get_stats_repo() -> StatsRepository:
    return StatsRepository(get_db())
