# 智能客服多 Agent 系统 - 生产镜像
# 基础镜像：python:3.13-slim（faiss-cpu 在 3.13 上有预编译 wheel，已在本地 uv venv 验证可装）
FROM python:3.13-slim

# 环境变量：避免缓冲 / 字节码污染镜像层
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ---------- 依赖层（优先装，充分利用 Docker 缓存） ----------
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------- 源码层 ----------
COPY . .

# 运行时目录（容器启动时 lifespan 也会自动创建，这里提前建好权限更稳）
RUN mkdir -p /app/data /app/uploads

EXPOSE 8000

# 单 worker：避免 SQLite 多进程写锁（多 worker 会触发 database is locked）。
# SSE 长连接友好；如要更高并发，请改用 PostgreSQL + aiosqlite 并增加 worker 数。
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1"]
