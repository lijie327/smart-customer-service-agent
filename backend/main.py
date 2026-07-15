"""智能客服多Agent系统主入口

FastAPI应用启动、配置和路由注册
"""
import asyncio
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import config
from backend.llm import QwenLLM, QwenEmbeddings
from backend.rag import FAQProcessor
from backend.memory import ConversationMemory
from backend.agents import (
    RouterAgent,
    RefundAgent,
    TechAgent,
    OrderAgent,
    GeneralAgent
)
from backend.api import router as api_router
from backend.static_server import setup_static_files

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def _redis_health_check_loop(memory: ConversationMemory, interval: int = 60):
    """
    Redis 后台健康检查循环

    每 interval 秒检查一次 Redis 连接状态，如果断开则尝试自动重连。
    """
    while True:
        try:
            await asyncio.sleep(interval)
            was_connected = memory.use_redis
            is_connected = await memory.health_check()
            if not was_connected and is_connected:
                logger.info("✅ Redis 自动重连成功！")
            elif was_connected and not is_connected:
                logger.warning("⚠ Redis 连接已断开，降级到内存缓存")
        except asyncio.CancelledError:
            logger.info("Redis 健康检查任务已取消")
            break
        except Exception as e:
            logger.error("Redis 健康检查异常: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动时初始化所有组件
    """
    logger.info("🚀 正在启动智能客服系统...")

    try:
        # 1. 初始化LLM
        logger.info("初始化LLM...")
        llm = QwenLLM(
            api_key=config.DASHSCOPE_API_KEY,
            model=config.LLM_MODEL
        )
        app.state.llm = llm
        logger.info(f"✓ LLM初始化完成，模型: {config.LLM_MODEL}")

        # 2. 初始化Embeddings
        logger.info("初始化Embeddings...")
        embeddings = QwenEmbeddings(api_key=config.DASHSCOPE_API_KEY)
        app.state.embeddings = embeddings
        logger.info("✓ Embeddings初始化完成")

        # 3. 初始化FAQ处理器和FAISS索引
        logger.info("构建FAQ知识库...")
        faq_processor = FAQProcessor(
            embeddings=embeddings,
            index_path=config.FAQ_INDEX_PATH
        )
        app.state.faq_processor = faq_processor
        faq_stats = faq_processor.get_stats()
        logger.info(f"✓ FAQ知识库构建完成，共 {faq_stats['total_faqs']} 条FAQ")

        # 3.1 统一混合检索器（向量 + BM25 + RRF），供 TechAgent / 工具 / API 共用
        from backend.rag_retriever import HybridFAQRetriever, set_default_retriever
        hybrid_retriever = HybridFAQRetriever(faq_processor)
        app.state.hybrid_retriever = hybrid_retriever
        set_default_retriever(hybrid_retriever)
        logger.info("✓ 统一混合检索器(HybridFAQRetriever)初始化完成")

        # 4. 初始化对话记忆 (Redis)
        logger.info("初始化ConversationMemory (Redis)...")
        memory = ConversationMemory(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
        )
        await memory._ensure_connection()
        app.state.memory = memory
        mem_stats = await memory.get_stats()
        if mem_stats.get("use_redis") and mem_stats.get("connected"):
            logger.info(
                "✓ ConversationMemory 初始化完成 (Redis: %s, 内存: %s)",
                mem_stats.get("used_memory", "N/A"),
                mem_stats.get("connected_clients", 0),
            )
        else:
            logger.warning("⚠ Redis 不可用，ConversationMemory 使用内存缓存")

        # 4.5 初始化数据层 (SQLite) + 自动灌入合成数据
        logger.info("初始化数据层 (SQLite)...")
        from backend.db.repository import init_database
        from backend.db.seed import run_seed
        db = init_database(config.DB_PATH)
        seeded = run_seed(db, count=800)
        if seeded:
            logger.info(f"✓ 数据层就绪，已灌入 {seeded} 条合成订单")
        else:
            logger.info("✓ 数据层就绪（订单数据已存在，跳过 seed）")
        app.state.db = db

        # 5. 初始化Router Agent
        logger.info("初始化RouterAgent...")
        router_agent = RouterAgent(llm)
        app.state.router_agent = router_agent
        logger.info("✓ RouterAgent初始化完成")

        # 6. 初始化各专业Agent
        logger.info("初始化专业Agent...")

        # 退货退款Agent
        refund_agent = RefundAgent(llm)
        app.state.refund_agent = refund_agent
        logger.info("✓ RefundAgent初始化完成")

        # 技术支持Agent
        tech_agent = TechAgent(llm, faq_processor, retriever=hybrid_retriever)
        app.state.tech_agent = tech_agent
        logger.info("✓ TechAgent初始化完成")

        # 订单查询Agent
        order_agent = OrderAgent(llm)
        app.state.order_agent = order_agent
        logger.info("✓ OrderAgent初始化完成")

        # 通用咨询Agent
        general_agent = GeneralAgent(llm)
        app.state.general_agent = general_agent
        logger.info("✓ GeneralAgent初始化完成")

        # 7. 创建必要的目录
        os.makedirs(config.UPLOAD_DIR, exist_ok=True)
        os.makedirs("./data", exist_ok=True)
        logger.info("✓ 目录创建完成")

        # 8. 启动 Redis 后台健康检查任务
        redis_health_task = asyncio.create_task(_redis_health_check_loop(memory))
        app.state._redis_health_task = redis_health_task

        logger.info("=" * 50)
        logger.info("✅ 智能客服系统启动成功！")
        logger.info("=" * 50)

    except Exception as e:
        logger.error(f"❌ 系统启动失败: {str(e)}")
        raise

    # 应用运行中
    yield

    # 关闭时清理
    logger.info("🛑 正在关闭智能客服系统...")
    # 取消后台健康检查任务
    health_task = getattr(app.state, "_redis_health_task", None)
    if health_task:
        health_task.cancel()
        try:
            await health_task
        except asyncio.CancelledError:
            pass
    if hasattr(app.state, "memory") and app.state.memory:
        await app.state.memory.close()
    logger.info("✅ 系统已关闭")


# 创建FastAPI应用
app = FastAPI(
    title="智能客服多Agent系统",
    description="基于阿里云百炼Qwen-Max的智能客服系统，支持多Agent协作",
    version="1.0.0",
    lifespan=lifespan
)

# 配置CORS（来源可通过环境变量 ALLOWED_ORIGINS 收口，生产建议限制具体域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=config.ALLOW_CREDENTIALS and config.ALLOWED_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(api_router)

# 配置前端静态文件服务（开发环境）
setup_static_files(app)


# 全局异常处理
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP异常处理"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "path": str(request.url),
            "method": request.method
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理"""
    logger.error(f"未处理的异常: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "服务器内部错误",
            "detail": str(exc) if app.debug else "请联系管理员",
            "path": str(request.url),
            "method": request.method
        }
    )


# 根路径（智能判断：有前端则返回 index.html，否则返回 API 信息）
@app.get("/")
async def root():
    """根路径 - 有前端时返回 UI，否则返回 API 欢迎信息"""
    import os as _os
    frontend_index = _os.path.join(
        _os.path.dirname(__file__), "..", "frontend", "index.html"
    )
    if _os.path.exists(frontend_index):
        from fastapi.responses import FileResponse
        return FileResponse(frontend_index)
    return {
        "message": "欢迎使用智能客服多Agent系统",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/health"
    }


# 测试接口
@app.get("/test")
async def test():
    """测试接口"""
    return {
        "status": "ok",
        "message": "系统运行正常",
        "timestamp": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


if __name__ == "__main__":
    import uvicorn

    logger.info(f"启动服务器: {config.HOST}:{config.PORT}")

    uvicorn.run(
        "backend.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,  # 生产/容器环境关闭热重载；本地开发可改为 True 或用 uvicorn --reload
        log_level="info"
    )
