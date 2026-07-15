"""FastAPI 路由和 API 接口

提供聊天、上传、工单、统计等 API 接口
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import List, Dict, Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, UploadFile, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from backend.models import TicketRequest, TicketResponse, AgentType
from backend.tools import escalate_to_human, query_order_status
from backend.db.repository import get_ticket_repo, get_stats_repo
from backend.config import ESCALATION_CONFIDENCE, RAG_CONF_HIGH
from backend.tracing import RequestTrace, trace_store

logger = logging.getLogger(__name__)

router = APIRouter()

# 上传 FAQ 的事件日志（轻量、非核心，保留内存即可）
faq_upload_history = []

# 串行化 FAQ 索引重建（add_faqs_batch 会全量重建 FAISS，避免并发竞态）
_upload_lock = asyncio.Lock()


def _get_memory(app_state: Any):
    return app_state.memory


_FALLBACK_PATTERNS = [
    "暂时无法处理", "无法处理您的问题", "建议转接人工",
    "建议联系人工客服", "请稍后重试", "无法回答您的问题", "我暂时无法",
]


def _is_fallback_reply(reply: str) -> bool:
    """检查回复是否需要回退到 LLM（空回复或仅含兜底话术）"""
    if not reply or not reply.strip():
        return True
    # 检查是否以兜底话术为主（非正常业务回复）
    return any(p in reply for p in _FALLBACK_PATTERNS)


async def _stream_llm_fallback(llm, user_message: str, conversation_history: list = None):
    messages = [{"role": "system", "content": "你是通用客服，用你自己的知识直接回答用户问题，简洁友好"}]
    if conversation_history:
        for h in conversation_history[-4:]:
            role = h.get("role", "user")
            content = h.get("content", "")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    async for chunk in llm.astream(messages):
        yield chunk


BUSINESS_KEYWORDS = [
    "退货", "退款", "退钱", "退换", "订单", "发货", "物流", "快递",
    "保修", "维修", "使用", "怎么", "如何", "什么", "查询", "客服",
    "坏了", "质量", "换货", "取消", "修改", "地址", "电话", "联系",
]


async def stream_chat_response(request: TicketRequest, app_state: Any) -> AsyncGenerator[str, None]:
    start_time = time.time()
    request_id = str(uuid.uuid4())
    trace = RequestTrace(request_id, request.session_id, request.user_id, request.user_message)
    memory = _get_memory(app_state)

    try:
        conversation_history = await memory.get_conversation_history(request.session_id, limit=10)

        # ========== 0. 纯数字检测（视为订单号查询）==========
        if re.search(r'^\d{2,}$', request.user_message.strip()):
            order_id = request.user_message.strip()

            # 检查历史是否在退款流程中
            history_is_refund = False
            if conversation_history:
                recent_text = " ".join([m.get("content", "") for m in conversation_history[-6:]])
                history_is_refund = any(kw in recent_text for kw in ["退货", "退款", "退钱"])

            await memory.save_message(request.session_id, "user", request.user_message)

            if history_is_refund:
                # 退款流程中 → 走退货Agent
                reply = ""
                async for chunk in app_state.refund_agent.stream_with_memory(
                    order_id, request.session_id, memory
                ):
                    reply += chunk
                    yield f"data: {json.dumps({'type': 'token', 'token': chunk}, ensure_ascii=False)}\n\n"
                if not reply.strip():
                    reply = f"订单{order_id}查询失败"
                    yield f"data: {json.dumps({'type': 'token', 'token': reply}, ensure_ascii=False)}\n\n"
                await memory.save_message(request.session_id, "assistant", reply)
                try:
                    get_stats_repo().increment_daily("refund", time.time() - start_time, success=True)
                except Exception as e:
                    logger.error("统计落库失败: %s", e)
                yield f"data: {json.dumps({'type': 'done', 'done': True}, ensure_ascii=False)}\n\n"
                return

            # 不在退款流程 → 直接查订单（确定性短文本，逐字吐出）
            try:
                order_result = await asyncio.to_thread(query_order_status.invoke, {"order_id": order_id})
                reply = f"订单{order_id}：{order_result.get('status', '未知')}，金额{order_result.get('amount', '?')}元"
            except Exception:
                reply = f"订单{order_id}查询失败，请稍后重试"
            for i in range(0, len(reply), 5):
                yield f"data: {json.dumps({'type': 'token', 'token': reply[i:i+5]}, ensure_ascii=False)}\n\n"
            await memory.save_message(request.session_id, "assistant", reply)
            try:
                get_stats_repo().increment_daily("order_query", time.time() - start_time, success=True)
            except Exception as e:
                logger.error("统计落库失败: %s", e)
            yield f"data: {json.dumps({'type': 'done', 'done': True}, ensure_ascii=False)}\n\n"
            return

        # ========== 1. 上下文感知路由（先于业务关键词检测）==========
        router_agent = app_state.router_agent

        # 检测是否在退款上下文中的简短确认消息
        current_is_refund = any(
            kw in request.user_message
            for kw in ["退货", "退款", "退钱", "退差价", "坏了想退", "到账"]
        )
        history_is_refund = False
        if conversation_history:
            recent_messages = conversation_history[-6:]
            recent_text = " ".join([m.get("content", "") for m in recent_messages])
            history_is_refund = any(kw in recent_text for kw in ["退货", "退款", "退钱"])
            # 检查最近一轮的 agent_used 是否为 refund
            for msg in reversed(recent_messages):
                if msg.get("role") == "assistant" and msg.get("agent_used") == "refund":
                    history_is_refund = True
                    break

        # 退款上下文中的简短消息（<20字），直接保持退款流程
        is_short_in_refund_context = (
            history_is_refund
            and len(request.user_message) < 20
            and not current_is_refund
        )

        force_refund = current_is_refund and history_is_refund

        route_reason = None
        if force_refund or is_short_in_refund_context:
            agent_type = AgentType.REFUND
            confidence = 0.92
            route_reason = "退款上下文保持退款流程"
        else:
            route_result = await router_agent.aroute(
                request.user_message,
                session_id=request.session_id,
                conversation_history=conversation_history
            )
            agent_type = route_result["agent_type"]
            confidence = route_result["confidence"]
            route_reason = route_result.get("reason")
        trace.add_event("routing", agent=agent_type.value, confidence=confidence, reason=route_reason)

        # ========== 2. 业务相关性检测（非退款上下文且无业务关键词 → LLM 兜底）==========
        is_business_related = any(kw in request.user_message for kw in BUSINESS_KEYWORDS)

        if not is_business_related and agent_type == AgentType.GENERAL:
            await memory.save_message(request.session_id, "user", request.user_message)
            full_response = ""
            async for chunk in _stream_llm_fallback(app_state.llm, request.user_message, conversation_history):
                full_response += chunk
                yield f"data: {json.dumps({'type': 'token', 'token': chunk}, ensure_ascii=False)}\n\n"
            await memory.save_message(request.session_id, "assistant", full_response)
            try:
                get_stats_repo().increment_daily("general", time.time() - start_time, success=True)
            except Exception as e:
                logger.error("统计落库失败: %s", e)
            yield f"data: {json.dumps({'type': 'done', 'done': True}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'type': 'routing', 'agent': agent_type.value, 'confidence': confidence}, ensure_ascii=False)}\n\n"

        # ========== 3. 选择 Agent ==========
        agent = None
        actions_taken = []
        precomputed_reply = None
        escalate = False
        escalation_reason = None
        escalation_priority = "normal"

        if agent_type == AgentType.REFUND:
            agent = app_state.refund_agent
            actions_taken.append("路由到退货退款专员")
            await memory.set_agent_context(request.session_id, "refund", {"last_intent": "退款流程"})
        elif agent_type == AgentType.TECH_SUPPORT:
            agent = app_state.tech_agent
            actions_taken.append("路由到技术支持专家")
            tech_conf = None
            if app_state.faq_processor:
                retriever = getattr(app_state, "hybrid_retriever", None) or app_state.faq_processor
                faq_results = await asyncio.to_thread(retriever.search, request.user_message, 2)
                if faq_results:
                    top = faq_results[0]
                    tech_conf = top.get("confidence", top.get("score", 0))
                    trace.add_event("retrieval", top_confidence=round(tech_conf, 3),
                                    sources=[r.get("question") for r in faq_results[:2]])
                    if tech_conf < ESCALATION_CONFIDENCE:
                        # 低置信 → 转人工兜底（防幻觉 / 安全兜底）
                        escalate = True
                        escalation_reason = (
                            f"RAG检索置信度过低（{tech_conf:.2f} < {ESCALATION_CONFIDENCE}），"
                            f"自动转人工"
                        )
                        escalation_priority = "high" if tech_conf < 0.15 else "normal"
                    elif tech_conf >= RAG_CONF_HIGH:
                        precomputed_reply = top["answer"]
                        actions_taken.append("FAQ检索命中")
        elif agent_type == AgentType.ORDER_QUERY:
            agent = app_state.order_agent
            actions_taken.append("路由到订单查询专员")
            order_id = None
            match = re.search(r'[\d]{3,}|[a-zA-Z0-9]{5,}', request.user_message)
            if match:
                order_id = match.group()
            if not order_id:
                for msg in reversed(conversation_history):
                    if msg.get("role") == "user":
                        m = re.search(r'[\d]{3,}|[a-zA-Z0-9]{5,}', msg.get("content", ""))
                        if m:
                            order_id = m.group()
                            break
            if order_id:
                try:
                    order_result = await asyncio.to_thread(query_order_status.invoke, {"order_id": order_id})
                    precomputed_reply = f"订单{order_id}当前状态：{order_result.get('status', '未知')}"
                    actions_taken.append(f"查询订单{order_id}")
                    await memory.set_order_info(request.session_id, order_id, order_result)
                except Exception:
                    pass
        else:
            agent = app_state.general_agent
            actions_taken.append("路由到通用客服")

        await memory.save_message(request.session_id, "user", request.user_message)

        # ========== 3.5 低置信转人工（Agent 执行前短路） ==========
        if escalate:
            async for chunk in _stream_escalation(
                trace, escalation_reason, escalation_priority,
                request, memory, start_time, agent_type, confidence, actions_taken
            ):
                yield chunk
            return

        # ========== 4. 执行 Agent（真·异步流式） ==========
        full_response = ""
        with trace.span("agent_exec", agent=agent_type.value) as span:
            if agent_type == AgentType.REFUND:
                # 退款 Agent：规则化为主，失败回退 LLM 真流式
                async for chunk in agent.stream_with_memory(request.user_message, request.session_id, memory):
                    full_response += chunk
                    yield f"data: {json.dumps({'type': 'token', 'token': chunk}, ensure_ascii=False)}\n\n"
            elif precomputed_reply:
                # 检索/规则命中的确定性短文本，逐字吐出（无需调 LLM）
                for i in range(0, len(precomputed_reply), 5):
                    piece = precomputed_reply[i:i + 5]
                    full_response += piece
                    yield f"data: {json.dumps({'type': 'token', 'token': piece}, ensure_ascii=False)}\n\n"
            else:
                context_messages = []
                recent_history = conversation_history[-6:]
                for h in recent_history:
                    role = h.get("role", "user")
                    content = h.get("content", "")
                    if role in ("user", "assistant"):
                        context_messages.append({"role": role, "content": content})
                context_messages.append({"role": "user", "content": request.user_message})
                # 其他 Agent：ReAct 循环 + LLM 真流式输出
                async for chunk in agent.astream(context_messages):
                    full_response += chunk
                    yield f"data: {json.dumps({'type': 'token', 'token': chunk}, ensure_ascii=False)}\n\n"

            if not full_response.strip():
                fallback_msg = "抱歉，我暂时无法处理您的问题，请稍后重试或转接人工客服。"
                full_response = fallback_msg
                yield f"data: {json.dumps({'type': 'token', 'token': fallback_msg}, ensure_ascii=False)}\n\n"
            span.close(tokens=len(full_response))

        # ========== 5. 兜底话术转人工（安全兜底） ==========
        # 非通用 Agent 的最终回复仍是兜底话术 → 自动转人工，避免把"建议联系人工"直接甩给用户
        if not escalate and _is_fallback_reply(full_response) and agent_type != AgentType.GENERAL:
            async for chunk in _stream_escalation(
                trace, "机器人回复为兜底话术，自动转人工", "normal",
                request, memory, start_time, agent_type, confidence, actions_taken
            ):
                yield chunk
            return

        # ========== 6. 统计 & 工单落库 ==========
        ticket_id = str(uuid.uuid4())
        duration = time.time() - start_time
        ticket_row = {
            "ticket_id": ticket_id,
            "session_id": request.session_id,
            "user_id": request.user_id,
            "user_message": request.user_message,
            "response": full_response,
            "agent_used": agent_type.value,
            "confidence": confidence,
            "actions_taken": json.dumps(actions_taken, ensure_ascii=False),
            "duration": duration,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "escalated": 0,
            "escalated_reason": None,
            "priority": None,
            "human_ticket_id": None,
        }
        try:
            get_ticket_repo().create(ticket_row)
            get_stats_repo().increment_daily(agent_type.value, duration, success=True)
        except Exception as e:
            logger.error("工单/统计落库失败: %s", e)

        await memory.save_message(request.session_id, "assistant", full_response, {
            "agent_used": agent_type.value, "confidence": confidence
        })

        yield f"data: {json.dumps({'type': 'done', 'done': True, 'ticket_id': ticket_id}, ensure_ascii=False)}\n\n"

    except Exception as e:
        logger.error(
            "流式聊天异常 session=%s user_id=%s message=%s: %s",
            request.session_id, request.user_id,
            request.user_message[:100], str(e), exc_info=True
        )
        fallback_msg = "抱歉，我暂时无法处理您的问题，请稍后重试或转接人工客服。"
        yield f"data: {json.dumps({'type': 'token', 'token': fallback_msg}, ensure_ascii=False)}\n\n"
        # 仅在日志中保留完整异常（已在上方 logger.error 记录 exc_info），
        # 面向前端只返回安全提示，避免泄露内部实现细节 / 密钥痕迹
        yield f"data: {json.dumps({'type': 'error', 'error': '系统处理异常，请稍后重试或转接人工客服'}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'done': True}, ensure_ascii=False)}\n\n"

    finally:
        # 链路追踪：无论正常结束还是异常，均落盘并输出结构化日志
        try:
            trace.log()
            trace_store.push(trace)
        except Exception:
            pass


async def _stream_escalation(trace: RequestTrace, reason: str, priority: str,
                             request: TicketRequest, memory, start_time: float,
                             agent_type, confidence: float, actions_taken: list) -> AsyncGenerator[str, None]:
    """转人工兜底：调用 escalate_to_human 工具，发出 escalation 事件并流式播报，落库工单。"""
    trace.escalated = True
    trace.escalation_reason = reason
    trace.add_event("escalation", reason=reason, priority=priority)

    # 调用转人工工具（同步工具，置于线程池避免阻塞事件循环）
    try:
        esc = await asyncio.to_thread(
            escalate_to_human.invoke, {"reason": reason, "priority": priority}
        )
    except Exception as e:
        logger.error("转人工工具调用失败: %s", e)
        esc = {
            "ticket_id": f"TKT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "message": "已为您转接人工客服，请稍候。",
            "estimated_response_time": "尽快",
        }

    human_ticket_id = esc.get("ticket_id")
    human_msg = esc.get("message", "已为您转接人工客服，请稍候。")

    # 发出转人工事件（前端据此展示转人工卡片）
    yield f"data: {json.dumps({'type': 'escalation', 'reason': reason,
                                'priority': priority, 'human_ticket_id': human_ticket_id,
                                'estimated_response_time': esc.get('estimated_response_time')},
                               ensure_ascii=False)}\n\n"

    # 流式播报转人工文案
    for i in range(0, len(human_msg), 5):
        piece = human_msg[i:i + 5]
        yield f"data: {json.dumps({'type': 'token', 'token': piece}, ensure_ascii=False)}\n\n"

    duration = time.time() - start_time
    ticket_id = str(uuid.uuid4())
    ticket_row = {
        "ticket_id": ticket_id,
        "session_id": request.session_id,
        "user_id": request.user_id,
        "user_message": request.user_message,
        "response": human_msg,
        "agent_used": "human",
        "confidence": confidence,
        "actions_taken": json.dumps(actions_taken + ["转人工"], ensure_ascii=False),
        "duration": duration,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "escalated": 1,
        "escalated_reason": reason,
        "priority": priority,
        "human_ticket_id": human_ticket_id,
    }
    try:
        get_ticket_repo().create(ticket_row)
        get_stats_repo().increment_daily("human", duration, success=True, escalations=1)
    except Exception as e:
        logger.error("转人工工单落库失败: %s", e)

    await memory.save_message(request.session_id, "assistant", human_msg,
                               {"agent_used": "human", "escalated": True})

    yield f"data: {json.dumps({'type': 'done', 'done': True, 'ticket_id': ticket_id,
                                'escalated': True, 'escalated_reason': reason},
                               ensure_ascii=False)}\n\n"


@router.post("/api/chat")
async def chat(request: TicketRequest, req: Request):
    app_state = req.app.state
    return StreamingResponse(
        stream_chat_response(request, app_state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )


@router.get("/api/tickets")
async def get_tickets(limit: int = 10, offset: int = 0, agent_type: str = None, user_id: str = None):
    repo = get_ticket_repo()
    total = repo.count(agent_type=agent_type, user_id=user_id)
    tickets = repo.list(limit=limit, offset=offset, agent_type=agent_type, user_id=user_id)
    # actions_taken 在库中以 JSON 字符串存储，返回前端前反序列化为列表
    for t in tickets:
        try:
            t["actions_taken"] = json.loads(t.get("actions_taken") or "[]")
        except Exception:
            t["actions_taken"] = []
    return JSONResponse({"total": total, "limit": limit, "offset": offset, "tickets": tickets})


@router.get("/api/stats")
async def get_stats():
    summary = get_stats_repo().get_summary()
    summary["faq_stats"] = {"total_faqs": 0, "uploads": len(faq_upload_history)}
    return JSONResponse(summary)


@router.get("/api/traces")
async def get_traces(limit: int = 50, escalated_only: bool = False):
    """链路追踪查询：返回最近请求链路（含路由/检索/Agent/转人工 span）。"""
    items = trace_store.recent(n=limit, escalated_only=escalated_only)
    return JSONResponse({"total": len(items), "traces": items})


@router.get("/api/health")
async def health_check(req: Request):
    import datetime as dt
    app_state = req.app.state
    checks = {"llm": hasattr(app_state, "llm") and app_state.llm is not None, "router_agent": hasattr(app_state, "router_agent") and app_state.router_agent is not None, "refund_agent": hasattr(app_state, "refund_agent") and app_state.refund_agent is not None, "tech_agent": hasattr(app_state, "tech_agent") and app_state.tech_agent is not None, "order_agent": hasattr(app_state, "order_agent") and app_state.order_agent is not None, "faq_processor": hasattr(app_state, "faq_processor") and app_state.faq_processor is not None, "db": hasattr(app_state, "db") and app_state.db is not None}
    return JSONResponse({"status": "healthy" if all(checks.values()) else "degraded", "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "checks": checks, "version": "1.0.0"})


@router.post("/api/upload-faq")
async def upload_faq(file: UploadFile, req: Request):
    import json as json_module
    app_state = req.app.state
    faq_processor = app_state.faq_processor
    if not faq_processor: raise HTTPException(status_code=500, detail="FAQ处理器未初始化")
    try:
        content = await file.read()
        try:
            faq_items = json_module.loads(content.decode("utf-8"))
        except:
            faq_items = []
            for line in content.decode("utf-8").strip().split("\n"):
                if not line.strip(): continue
                parts = line.split("|", 1)
                if len(parts) >= 2: faq_items.append({"question": parts[0].strip(), "answer": parts[1].strip(), "category": "上传"})
        if not isinstance(faq_items, list): raise HTTPException(status_code=400, detail="FAQ 数据格式错误")
        valid_items = [item for item in faq_items if isinstance(item, dict) and "question" in item and "answer" in item]
        retriever = getattr(app_state, "hybrid_retriever", None)
        # 向量化 + 全量重建 FAISS 是网络调用，放入线程池避免阻塞事件循环；
        # 加锁串行化，避免并发上传同时重建索引导致竞态。
        async with _upload_lock:
            if retriever is not None:
                added = await asyncio.to_thread(retriever.add_faqs_batch, valid_items) if valid_items else 0
                faq_stats = retriever.get_stats()
            else:
                added = await asyncio.to_thread(faq_processor.add_faqs_batch, valid_items) if valid_items else 0
                faq_stats = faq_processor.get_stats()
        faq_upload_history.append({"filename": file.filename, "total_items": len(faq_items), "added": added, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        return JSONResponse({"success": True, "message": f"成功添加 {added}/{len(faq_items)} 条 FAQ", "added": added, "total_in_file": len(faq_items), "faq_stats": faq_stats})
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=500, detail=f"处理 FAQ 文件失败：{str(e)}")