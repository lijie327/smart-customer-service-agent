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

logger = logging.getLogger(__name__)

router = APIRouter()

ticket_history = []
faq_upload_history = []
_stats_lock = asyncio.Lock()
stats_data = {
    "total_requests": 0,
    "success_requests": 0,
    "total_time": 0.0,
    "agent_stats": {"router": 0, "refund": 0, "tech_support": 0, "order_query": 0, "general": 0}
}


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
    for chunk in llm.stream(messages):
        yield chunk


BUSINESS_KEYWORDS = [
    "退货", "退款", "退钱", "退换", "订单", "发货", "物流", "快递",
    "保修", "维修", "使用", "怎么", "如何", "什么", "查询", "客服",
    "坏了", "质量", "换货", "取消", "修改", "地址", "电话", "联系",
]


async def stream_chat_response(request: TicketRequest, app_state: Any) -> AsyncGenerator[str, None]:
    start_time = time.time()
    memory = _get_memory(app_state)

    try:
        conversation_history = await memory.get_conversation_history(request.session_id, limit=10)

        # ========== 0. 纯数字检测 ==========
        if re.search(r'^\d{2,}$', request.user_message.strip()):
            order_id = request.user_message.strip()

            # 检查历史是否在退款流程中
            history_is_refund = False
            if conversation_history:
                recent_text = " ".join([m.get("content", "") for m in conversation_history[-6:]])
                history_is_refund = any(kw in recent_text for kw in ["退货", "退款", "退钱"])

            if history_is_refund:
                # 退款流程中 → 走退货Agent
                agent = app_state.refund_agent
                await memory.save_message(request.session_id, "user", request.user_message)
                refund_result = await agent.execute_with_memory(order_id, request.session_id, memory)
                reply = refund_result.get("reply", f"订单{order_id}查询失败")
                for i in range(0, len(reply), 5):
                    yield f"data: {json.dumps({'type': 'token', 'token': reply[i:i+5]}, ensure_ascii=False)}\n\n"
                await memory.save_message(request.session_id, "assistant", reply)
                async with _stats_lock:
                    stats_data["total_requests"] += 1
                    stats_data["agent_stats"]["refund"] += 1
                yield f"data: {json.dumps({'type': 'done', 'done': True}, ensure_ascii=False)}\n\n"
                return

            # 不在退款流程 → 直接查订单
            try:
                order_result = query_order_status.invoke({"order_id": order_id})
                reply = f"订单{order_id}：{order_result.get('status', '未知')}，金额{order_result.get('amount', '?')}元"
            except Exception:
                reply = f"订单{order_id}查询失败，请稍后重试"

            await memory.save_message(request.session_id, "user", request.user_message)
            for i in range(0, len(reply), 5):
                yield f"data: {json.dumps({'type': 'token', 'token': reply[i:i+5]}, ensure_ascii=False)}\n\n"
            await memory.save_message(request.session_id, "assistant", reply)
            async with _stats_lock:
                stats_data["total_requests"] += 1
                stats_data["agent_stats"]["order_query"] += 1
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

        if force_refund or is_short_in_refund_context:
            agent_type = AgentType.REFUND
            confidence = 0.92
        else:
            route_result = router_agent.route(
                request.user_message,
                session_id=request.session_id,
                conversation_history=conversation_history
            )
            agent_type = route_result["agent_type"]
            confidence = route_result["confidence"]

        # ========== 2. 业务相关性检测（非退款上下文且无业务关键词 → LLM 兜底）==========
        is_business_related = any(kw in request.user_message for kw in BUSINESS_KEYWORDS)

        if not is_business_related and agent_type == AgentType.GENERAL:
            await memory.save_message(request.session_id, "user", request.user_message)
            full_response = ""
            async for chunk in _stream_llm_fallback(app_state.llm, request.user_message, conversation_history):
                full_response += chunk
                yield f"data: {json.dumps({'type': 'token', 'token': chunk}, ensure_ascii=False)}\n\n"
            await memory.save_message(request.session_id, "assistant", full_response)
            async with _stats_lock:
                stats_data["total_requests"] += 1
                stats_data["agent_stats"]["general"] += 1
            yield f"data: {json.dumps({'type': 'done', 'done': True}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'type': 'routing', 'agent': agent_type.value, 'confidence': confidence}, ensure_ascii=False)}\n\n"

        # ========== 3. 选择 Agent ==========
        agent = None
        actions_taken = []
        precomputed_reply = None

        if agent_type == AgentType.REFUND:
            agent = app_state.refund_agent
            actions_taken.append("路由到退货退款专员")
            await memory.set_agent_context(request.session_id, "refund", {"last_intent": "退款流程"})
        elif agent_type == AgentType.TECH_SUPPORT:
            agent = app_state.tech_agent
            actions_taken.append("路由到技术支持专家")
            if app_state.faq_processor:
                faq_results = app_state.faq_processor.search(request.user_message, k=2)
                if faq_results and faq_results[0].get("score", 0) > 0.75:
                    precomputed_reply = faq_results[0]["answer"]
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
                    order_result = query_order_status.invoke({"order_id": order_id})
                    precomputed_reply = f"订单{order_id}当前状态：{order_result.get('status', '未知')}"
                    actions_taken.append(f"查询订单{order_id}")
                    await memory.set_order_info(request.session_id, order_id, order_result)
                except Exception:
                    pass
        else:
            agent = app_state.general_agent
            actions_taken.append("路由到通用客服")

        await memory.save_message(request.session_id, "user", request.user_message)

        # ========== 4. 执行 Agent ==========
        full_response = ""
        agent_reply = ""

        if precomputed_reply:
            agent_reply = precomputed_reply
        elif agent_type == AgentType.REFUND:
            refund_result = await agent.execute_with_memory(
                request.user_message, request.session_id, memory
            )
            is_rule_handled = refund_result.get("handled") is True or bool(refund_result.get("actions"))
            if is_rule_handled:
                agent_reply = refund_result.get("reply", "") or ""
            else:
                messages = [{"role": "user", "content": request.user_message}]
                base_result = agent.execute(messages)
                agent_reply = base_result.get("reply", "") or ""
        else:
            context_messages = []
            recent_history = conversation_history[-6:]
            for h in recent_history:
                role = h.get("role", "user")
                content = h.get("content", "")
                if role in ("user", "assistant"):
                    context_messages.append({"role": role, "content": content})
            context_messages.append({"role": "user", "content": request.user_message})
            base_result = agent.execute(context_messages)
            agent_reply = base_result.get("reply", "") or ""

        # ========== 5. 流式输出 ==========
        if _is_fallback_reply(agent_reply):
            async for chunk in _stream_llm_fallback(app_state.llm, request.user_message, conversation_history):
                full_response += chunk
                yield f"data: {json.dumps({'type': 'token', 'token': chunk}, ensure_ascii=False)}\n\n"
        else:
            full_response = agent_reply
            chunk_size = max(1, len(agent_reply) // 10) or 1
            for i in range(0, len(agent_reply), chunk_size):
                yield f"data: {json.dumps({'type': 'token', 'token': agent_reply[i:i + chunk_size]}, ensure_ascii=False)}\n\n"

        if not full_response.strip():
            fallback_msg = "抱歉，我暂时无法处理您的问题，请稍后重试或转接人工客服。"
            full_response = fallback_msg
            yield f"data: {json.dumps({'type': 'token', 'token': fallback_msg}, ensure_ascii=False)}\n\n"

        # ========== 6. 统计 & 工单 ==========
        async with _stats_lock:
            stats_data["total_requests"] += 1
            stats_data["success_requests"] += 1
            stats_data["total_time"] += (time.time() - start_time)
            stats_data["agent_stats"][agent_type.value] += 1

        ticket = {
            "ticket_id": str(uuid.uuid4()),
            "session_id": request.session_id,
            "user_id": request.user_id,
            "user_message": request.user_message,
            "response": full_response,
            "agent_used": agent_type.value,
            "confidence": confidence,
            "actions_taken": actions_taken,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration": time.time() - start_time
        }
        ticket_history.append(ticket)

        await memory.save_message(request.session_id, "assistant", full_response, {
            "agent_used": agent_type.value, "confidence": confidence
        })

        yield f"data: {json.dumps({'type': 'done', 'done': True, 'ticket_id': ticket['ticket_id']}, ensure_ascii=False)}\n\n"

    except Exception as e:
        logger.error(
            "流式聊天异常 session=%s user_id=%s message=%s: %s",
            request.session_id, request.user_id,
            request.user_message[:100], str(e), exc_info=True
        )
        fallback_msg = "抱歉，我暂时无法处理您的问题，请稍后重试或转接人工客服。"
        yield f"data: {json.dumps({'type': 'token', 'token': fallback_msg}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'done': True}, ensure_ascii=False)}\n\n"


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
    filtered = ticket_history
    if agent_type: filtered = [t for t in filtered if t.get("agent_used") == agent_type]
    if user_id: filtered = [t for t in filtered if t.get("user_id") == user_id]
    filtered.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return JSONResponse({"total": len(filtered), "limit": limit, "offset": offset, "tickets": filtered[offset:offset + limit]})


@router.get("/api/stats")
async def get_stats():
    total = stats_data["total_requests"]
    success = stats_data["success_requests"]
    total_time = stats_data["total_time"]
    return JSONResponse({"total": total, "success": success, "success_rate": round((success / total * 100) if total > 0 else 0, 1), "avg_time": round((total_time / total) if total > 0 else 0, 2), "agent_stats": stats_data["agent_stats"], "faq_stats": {"total_faqs": 0, "uploads": len(faq_upload_history)}})


@router.get("/api/health")
async def health_check(req: Request):
    import datetime as dt
    app_state = req.app.state
    checks = {"llm": hasattr(app_state, "llm") and app_state.llm is not None, "router_agent": hasattr(app_state, "router_agent") and app_state.router_agent is not None, "refund_agent": hasattr(app_state, "refund_agent") and app_state.refund_agent is not None, "tech_agent": hasattr(app_state, "tech_agent") and app_state.tech_agent is not None, "order_agent": hasattr(app_state, "order_agent") and app_state.order_agent is not None, "faq_processor": hasattr(app_state, "faq_processor") and app_state.faq_processor is not None}
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
        added = faq_processor.add_faqs_batch(valid_items) if valid_items else 0
        faq_upload_history.append({"filename": file.filename, "total_items": len(faq_items), "added": added, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        return JSONResponse({"success": True, "message": f"成功添加 {added}/{len(faq_items)} 条 FAQ", "added": added, "total_in_file": len(faq_items), "faq_stats": faq_processor.get_stats()})
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=500, detail=f"处理 FAQ 文件失败：{str(e)}")