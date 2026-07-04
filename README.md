# 智能客服多Agent系统

基于阿里云百炼Qwen-Max的智能客服多Agent系统，支持意图识别、任务路由、RAG检索增强生成。

## 🏗️ 系统架构

```
用户请求
   ↓
RouterAgent (意图识别)
   ↓
┌──────────────┬──────────────┬──────────────┬──────────────┐
│              │              │              │              │
RefundAgent   TechAgent    OrderAgent    GeneralAgent
(退货退款)    (技术支持)   (订单查询)    (通用咨询)
   │              │              │              │
   ↓              ↓              ↓              ↓
[工具调用]    [RAG检索]     [工具调用]     [工具调用]
query_order   search_faq    query_order   search_faq
approve_refund              get_detail    search_policy
search_policy
   │              │              │              │
   └──────────────┴──────────────┴──────────────┘
                  ↓
            生成最终回复 (SSE流式)
```

## 📦 项目结构

```
.
├── backend/
│   ├── __init__.py
│   ├── config.py          # 配置管理
│   ├── models.py          # 数据模型
│   ├── llm.py             # LLM封装
│   ├── rag.py             # RAG系统
│   ├── api.py             # API路由
│   ├── main.py            # 主入口
│   ├── agents/            # Agent模块
│   │   ├── base_agent.py
│   │   ├── router_agent.py
│   │   ├── refund_agent.py
│   │   ├── tech_agent.py
│   │   └── order_agent.py
│   └── tools/             # 工具模块
│       ├── order_tools.py
│       ├── knowledge_tools.py
│       └── system_tools.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入API密钥：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
DASHSCOPE_API_KEY=your_actual_api_key_here
LLM_MODEL=qwen-max
```

### 3. 启动服务

```bash
python -m backend.main
```

或使用uvicorn：

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. 访问API文档

打开浏览器访问：http://localhost:8000/docs

## 📡 API接口

### 聊天接口（SSE流式）

```http
POST /api/chat
Content-Type: application/json

{
  "user_message": "我想查询订单ORD123456的状态",
  "session_id": "session_001",
  "user_id": "user_001"
}
```

**响应（SSE流式）：**

```
data: {"type": "routing", "agent": "order_query", "confidence": 0.95}

data: {"type": "token", "token": "正在"}

data: {"type": "token", "token": "为您"}

data: {"type": "token", "token": "查询"}

data: {"type": "done", "done": true, "ticket_id": "xxx"}
```

### 聊天接口（同步）

```http
POST /api/chat/sync
Content-Type: application/json

{
  "user_message": "如何申请退款？",
  "session_id": "session_001",
  "user_id": "user_001"
}
```

**响应：**

```json
{
  "reply": "申请退款的步骤如下...",
  "agent_used": "refund",
  "confidence": 0.92,
  "actions_taken": ["路由到退货退款专员"],
  "ticket_id": "xxx",
  "duration": 2.5
}
```

### 上传FAQ

```http
POST /api/upload-faq
Content-Type: multipart/form-data

file: faq.json
```

**JSON格式示例：**

```json
[
  {
    "question": "如何退货？",
    "answer": "您可以在收到商品后7天内申请退货...",
    "category": "退货"
  }
]
```

**TXT格式示例：**

```
如何退货？|您可以在收到商品后7天内申请退货...
退款多久到账？|退款审批通过后，款项将在3-5个工作日内退回...
```

### 获取工单历史

```http
GET /api/tickets?limit=10&offset=0&agent_type=refund&user_id=user_001
```

**响应：**

```json
{
  "total": 100,
  "limit": 10,
  "offset": 0,
  "tickets": [
    {
      "ticket_id": "xxx",
      "session_id": "session_001",
      "user_id": "user_001",
      "user_message": "...",
      "response": "...",
      "agent_used": "refund",
      "confidence": 0.92,
      "actions_taken": ["..."],
      "timestamp": "2024-01-01 12:00:00",
      "duration": 2.5
    }
  ]
}
```

### 获取统计信息

```http
GET /api/stats
```

**响应：**

```json
{
  "total": 100,
  "success": 95,
  "success_rate": 95.0,
  "avg_time": 2.3,
  "agent_stats": {
    "router": 100,
    "refund": 30,
    "tech_support": 25,
    "order_query": 35,
    "general": 10
  },
  "faq_stats": {
    "total_faqs": 15,
    "uploads": 2
  }
}
```

### 健康检查

```http
GET /api/health
```

**响应：**

```json
{
  "status": "healthy",
  "timestamp": "2024-01-01 12:00:00",
  "checks": {
    "llm": true,
    "router_agent": true,
    "refund_agent": true,
    "tech_agent": true,
    "order_agent": true,
    "faq_processor": true
  },
  "version": "1.0.0"
}
```

## 🛠️ 核心组件

### Agent类型

| Agent | 职责 | 工具 |
|-------|------|------|
| RouterAgent | 意图识别和路由分发 | - |
| RefundAgent | 退货退款处理 | query_order_status, approve_refund, search_policy |
| TechAgent | 技术支持和FAQ解答 | search_faq + RAG |
| OrderAgent | 订单查询 | query_order_status, get_order_detail |

### 工具集

**订单工具：**
- `query_order_status(order_id)`: 查询订单状态
- `approve_refund(order_id)`: 审批退款
- `get_order_detail(order_id)`: 获取订单详情

**知识库工具：**
- `search_faq(query, top_k)`: FAQ检索
- `search_policy(policy_type)`: 政策查询

**系统工具：**
- `get_current_time()`: 获取当前时间
- `escalate_to_human(reason, priority)`: 升级人工客服
- `validate_user_input(input_text, input_type)`: 输入验证

## 🔧 开发指南

### 添加新Agent

1. 在 `backend/agents/` 创建新Agent类，继承 `BaseAgent`
2. 定义系统提示词和工具列表
3. 在 `backend/main.py` 的 `lifespan` 中初始化
4. 在 `backend/api.py` 中添加路由逻辑

### 添加新工具

1. 在 `backend/tools/` 创建工具函数，使用 `@tool` 装饰器
2. 在 `backend/tools/__init__.py` 中导出
3. 在相应Agent的工具列表中添加工具

### 扩展FAQ知识库

方法1：API上传

```bash
curl -X POST http://localhost:8000/api/upload-faq \
  -F "file=@faq.json"
```

方法2：代码添加

```python
from backend.rag import FAQProcessor

faq_processor.add_faq(
    question="新问题",
    answer="新答案",
    category="分类"
)
```

## 📝 注意事项

1. **API密钥安全**：不要将 `.env` 文件提交到版本控制
2. **生产环境**：
   - 禁用 `reload=True`
   - 配置具体的CORS域名
   - 使用数据库存储工单和统计数据
   - 添加认证和授权机制
3. **性能优化**：
   - FAQ索引可以预加载到内存
   - 使用Redis缓存会话状态
   - 考虑使用消息队列处理异步任务

## 🐛 常见问题

**Q: 启动时报错 "DASHSCOPE_API_KEY is not set"**

A: 请确保创建了 `.env` 文件并填入了有效的API密钥。

**Q: 流式响应不工作**

A: 检查客户端是否正确解析SSE格式，确保读取 `data:` 前缀的内容。

**Q: 工具调用失败**

A: 检查工具函数是否正确装饰了 `@tool`，并确保在Agent中正确注册。

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交Issue和Pull Request！

## 📧 联系方式

如有问题，请联系：l2172433823@163.com
