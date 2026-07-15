# 部署指南 · 阿里云 ECS（Docker 容器化）

本指南面向把 `smart_cs` 部署到阿里云 ECS 实例（建议使用 Alibaba Cloud Linux 3 / Ubuntu 22.04，2 vCPU 4 GB 起）。

> 镜像已在 `python:3.13-slim` 下验证（faiss-cpu 有 3.13 预编译 wheel）。如果你本地 Docker 构建时提示 faiss 无对应 wheel，可临时把 `Dockerfile` 的 `FROM python:3.13-slim` 改为 `FROM python:3.13`（带编译工具链），或在 `requirements.txt` 中固定 `faiss-cpu==1.8.0`。

---

## 一、服务器准备

1. 在阿里云控制台创建 ECS 实例，安全组放通：
   - `22` (SSH)，`80` (HTTP)；如需 HTTPS 再加 `443`。
   - **不要**对外暴露 `8000`（由 nginx 内网反代）。
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
# 方式 A：从 Git 拉取
git clone <你的仓库地址> smart_cs && cd smart_cs

# 方式 B：本地构建镜像后 docker save/load 到服务器（适合私有仓库不便的场景）
#   本地： docker compose build && docker compose save smart_cs-app > app.tar
#   服务器：docker load < app.tar
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
- 健康检查：`curl http://localhost/api/health` 应返回 `{"status":"healthy",...}`
- 浏览器访问：`http://<ECS公网IP>` 即可使用（nginx 在 80 端口反代）

---

## 四、数据持久化

- `app-data` 卷：`/app/data` 下的 SQLite（`smart_cs.db`）+ FAISS 索引（`faq_index*`），**重启/重建容器不丢**。
- `redis-data` 卷：Redis 中会话记忆持久化。
- `app-uploads` 卷：FAQ 上传文件。

> 升级代码后只需 `docker compose up -d --build`，卷数据保留。

---

## 五、HTTPS / 域名（可选，生产建议）

1. **域名**：在阿里云 DNS 把 `A` 记录指向 ECS 公网 IP。
2. **证书**：在阿里云 SSL 证书服务申请免费证书，下载 Nginx 版（`.pem` + `.key`）。
3. **启用**：把 `nginx.conf` 中底部 `HTTPS 参考` 段落取消注释，并将证书放到 `./certs/`，在 `docker-compose.yml` 的 `nginx` 服务加挂载：
   ```yaml
   volumes:
     - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
     - ./certs:/etc/nginx/certs:ro
   ```
   然后 `docker compose up -d nginx` 重新加载。

> 另一种更省心的方式：在阿里云 **SLB（负载均衡）** 上挂载 SSL 证书，后端仍然 HTTP 80，由 SLB 做 TLS 终止。

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
