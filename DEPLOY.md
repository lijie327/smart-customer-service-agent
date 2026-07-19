# 部署指南 · 阿里云 ECS（Docker 容器化）

本指南面向把 `smart_cs` 部署到阿里云 ECS 实例（建议使用 Alibaba Cloud Linux 3 / Ubuntu 22.04，2 vCPU 4 GB 起）。

> 镜像已在 `python:3.13-slim` 下验证（faiss-cpu 有 3.13 预编译 wheel）。如果你本地 Docker 构建时提示 faiss 无对应 wheel，可临时把 `Dockerfile` 的 `FROM python:3.13-slim` 改为 `FROM python:3.13`（带编译工具链），或在 `requirements.txt` 中固定 `faiss-cpu==1.8.0`。

> **部署形态说明**：本仓库采用「精简版」编排——**不含 nginx 反向代理**，`app` 容器直接通过 `8000` 端口对外。下文所有访问地址均为 `:8000`。如需反向代理 / HTTPS，见第五节。

---

## 一、服务器准备

1. 在阿里云控制台创建 ECS 实例，安全组放通：
   - `22` (SSH)，`8000` (app 直接对外，已无 nginx 反代)；如需 HTTPS 再加 `443`。
   - 本项目 app 容器直接暴露 8000，无需为 nginx 开放 `80`。
2. SSH 登录后安装 Docker 与 docker-compose：

```bash
# Alibaba Cloud Linux / CentOS
sudo dnf install -y docker docker-compose-plugin
sudo systemctl enable --now docker

# Ubuntu
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

---

## 二、获取代码与配置

```bash
# 方式 A（推荐）：从 Git 拉取，在服务器上直接构建（服务器有外网，自动拉取 redis:7.2-alpine + PyPI 依赖）
git clone <你的仓库地址> smart_cs && cd smart_cs

# 方式 B：本机 scp 整目录到服务器（同样在服务器上 docker compose up 构建）
#   scp -r /path/to/smart_cs user@<ECS公网IP>:/home/user/
```

配置环境变量（**必填 `DASHSCOPE_API_KEY`**）：

```bash
cp .env.example .env
vim .env            # 至少填入 DASHSCOPE_API_KEY=sk-xxxx
```

> 容器内 `REDIS_HOST` 已由 `docker-compose.yml` 的 `environment` 覆盖为 `redis`，无需在 `.env` 里改。

---

## 三、构建并启动

```bash
# 构建镜像并后台启动（-d 守护态）
docker compose up -d --build

# 查看状态
docker compose ps
docker compose logs -f app      # 观察启动日志，确认 "启动成功"
```

启动后：
- 健康检查：`curl http://localhost:8000/api/health` 应返回 `{"status":"healthy",...}`
- 浏览器访问：`http://<ECS公网IP>:8000` 即可使用（app 直出 8000，无 nginx 反代）

---

## 四、数据持久化

- `app-data` 卷：`/app/data` 下的 SQLite（`smart_cs.db`）+ FAISS 索引（`faq_index*`），**重启/重建容器不丢**。
- `redis-data` 卷：Redis 中会话记忆持久化。
- `app-uploads` 卷：FAQ 上传文件。

> 升级代码后只需 `docker compose up -d --build`，卷数据保留。

---

## 五、HTTPS / 域名（可选，生产建议）

当前部署 **app 直出 8000 的纯 HTTP，没有反向代理**。需要 HTTPS 时二选一：

1. **云厂商 CLB/SLB 做 TLS 终止（最省心，推荐）**：在阿里云 CLB/SLB 上挂载 SSL 证书，前端 443 → 后端 ECS 的 `8000`（HTTP）。后端代码无需改动，证书也不进容器。
2. **加回 nginx 反代（仍走容器）**：若想在主机上自管 TLS，可恢复历史 `nginx.conf` 模板（已删除，可从 `git log` 取回）并在 `docker-compose.yml` 加回 `nginx` 服务、把 `app` 从 `ports` 改回 `expose`。参考历史版本即可，本文件不重复粘贴。

> 域名解析：在阿里云 DNS 把 `A` 记录指向 ECS 公网 IP 即可。

---

## 六、常用运维命令

```bash
docker compose ps                         # 服务状态
docker compose logs -f app               # 跟踪后端日志
docker compose restart app               # 重启后端（配置/代码更新后）
docker compose up -d --build             # 重新构建并启动
docker compose down                      # 停止并移除容器（卷保留）
docker compose down -v                   # 停止并删除卷（⚠ 数据清空）
```

---

## 七、生产加固建议

- **CORS 收口**：`.env` 设 `ALLOWED_ORIGINS=https://your-domain.com`，并将 `ALLOW_CREDENTIALS` 按需开启（仅在 origins 不含 `*` 时生效）。
- **并发**：当前为单 worker（规避 SQLite 写锁）。如需更高并发，将 `backend/db` 切换为 PostgreSQL + `aiosqlite`/`asyncpg`，再提高 `--workers` 数量。
- **密钥**：`.env` 勿提交进 Git；生产可用阿里云 **KMS / 密钥管理** 或 **ACK 保密字典** 注入。
- **监控**：后端已内置 `/api/traces` 链路追踪与 `/api/stats` 统计；可再接 Prometheus + Grafana 或阿里云 **ARMS**。
- **资源**：2 vCPU / 4 GB 内存起步；若 FAQ 量大、并发高，建议 4 GB+ 内存。

---

## 八、排错

| 现象 | 排查 |
|---|---|
| 启动报 `DASHSCOPE_API_KEY is not set` | `.env` 未填或路径不对；确认 `docker compose` 在含 `.env` 的目录执行 |
| 首页空白 / 静态 404 | 确认 `frontend/` 已 COPY 进镜像；`docker compose logs app` 看静态挂载日志 |
| 对话一直转圈 | 看 `docker compose logs app` 是否有 LLM 调用报错（Key 无效 / 欠费） |
| 重启后订单/统计清空 | 没挂卷；确认 `app-data` 卷存在：`docker volume ls` |
| 容器反复重启 | `docker compose logs app` 看 lifespan 异常；多为依赖（Redis/Key）问题 |
