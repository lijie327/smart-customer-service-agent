-- 智能客服系统数据层 Schema (SQLite)
-- 设计目标：用真实的 schema + 持久化替代硬编码字典 Mock，
-- 支持 SQL 查询 / 事务 / 索引，便于简历讲解"分层架构 + 数据持久化"。

PRAGMA foreign_keys = ON;

-- 订单表：核心业务数据
CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,          -- 订单号（唯一）
    customer_name   TEXT NOT NULL,             -- 客户姓名
    phone           TEXT,                       -- 手机号
    product         TEXT NOT NULL,             -- 商品名称
    amount          REAL NOT NULL,             -- 金额
    status          TEXT NOT NULL,             -- 订单状态
    refundable      INTEGER NOT NULL DEFAULT 1,-- 1=可退 0=不可退
    payment_method  TEXT,                       -- 支付方式
    shipping_address TEXT,                     -- 收货地址
    order_date      TEXT,                       -- 下单日期
    created_at      TEXT NOT NULL              -- 入库时间
);

CREATE INDEX IF NOT EXISTS idx_orders_phone  ON orders(phone);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- 工单表：每次对话的落库记录
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     TEXT PRIMARY KEY,
    session_id    TEXT,
    user_id       TEXT,
    user_message  TEXT,
    response      TEXT,
    agent_used    TEXT,
    confidence    REAL,
    actions_taken TEXT,        -- JSON 数组：本次执行了哪些动作
    duration      REAL,        -- 处理耗时（秒）
    created_at    TEXT NOT NULL,
    escalated     INTEGER NOT NULL DEFAULT 0,  -- 1=已转人工
    escalated_reason TEXT,                     -- 转人工原因
    priority      TEXT,                         -- 人工工单优先级 low/normal/high/urgent
    human_ticket_id TEXT                       -- 人工客服工单号
);

CREATE INDEX IF NOT EXISTS idx_tickets_session ON tickets(session_id);
CREATE INDEX IF NOT EXISTS idx_tickets_agent   ON tickets(agent_used);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);

-- 每日指标聚合表：统计跨重启持久化
CREATE TABLE IF NOT EXISTS daily_metrics (
    stat_date        TEXT PRIMARY KEY,         -- 日期 YYYY-MM-DD
    total_requests   INTEGER NOT NULL DEFAULT 0,
    success_requests INTEGER NOT NULL DEFAULT 0,
    total_time       REAL    NOT NULL DEFAULT 0,
    escalations      INTEGER NOT NULL DEFAULT 0,  -- 转人工次数
    agent_stats      TEXT    NOT NULL DEFAULT '{}' -- JSON: {agent_type: count}
);
