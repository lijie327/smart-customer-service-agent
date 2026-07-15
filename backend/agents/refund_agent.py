"""
退货退款处理 Agent
负责处理用户的退货、退款相关请求
"""
import re
from typing import Dict, Any, List, Optional

from backend.agents.base_agent import BaseAgent
from backend.tools.order_tools import query_order_status, approve_refund
from backend.tools.knowledge_tools import search_policy


class RefundAgent(BaseAgent):
    """退货退款处理专员"""

    def __init__(self, llm=None, name: str = "RefundAgent"):
        system_prompt = """你是一个专业的退货退款处理专员。

核心规则：
1. 先查看对话历史，如果用户已经提供过订单号，直接使用，不要再问
2. 查询订单后，如果符合退货条件，直接告知用户并提交退款，不要反复确认
3. 如果不符合条件，明确告知原因
4. 简洁高效，不要说"请稍等"、"我来帮您"等废话
5. 每次回复控制在3句话以内

工作流程：
- 用户提供订单号 → 立刻查询 → 告知结果 → 符合条件就退款
- 用户描述问题 → 判断是否在退货范围内 → 需要订单号就问，有就直接查"""

        tools = [query_order_status, approve_refund, search_policy]
        super().__init__(name=name, tools=tools, system_prompt=system_prompt, llm=llm)

    # ==================== 订单号提取 ====================

    def _extract_order_id(self, user_message: str, history: list = None) -> Optional[str]:
        """
        从消息或历史中提取订单号。接受任何3位以上数字。

        Args:
            user_message: 当前用户消息
            history: 历史消息列表

        Returns:
            订单号字符串或 None
        """
        # 1. 从当前消息提取 "订单号xxx" 格式
        match = re.search(r'订单号[：:\s]*(\d+)', user_message)
        if match:
            return match.group(1)

        # 2. 从当前消息提取纯数字（3位以上）
        match = re.search(r'\b(\d{3,})\b', user_message)
        if match:
            return match.group(1)

        # 3. 从历史中提取
        if history:
            for msg in reversed(history):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    match = re.search(r'订单号[：:\s]*(\d+)', content)
                    if match:
                        return match.group(1)
                    match = re.search(r'\b(\d{3,})\b', content)
                    if match:
                        return match.group(1)

        return None

    # ==================== 带记忆的规则化处理 ====================

    async def execute_with_memory(
        self,
        user_message: str,
        session_id: str,
        memory,  # ConversationMemory 实例
    ) -> Dict[str, Any]:
        """使用对话记忆和订单上下文处理退款请求"""

        # ========== 1. 读取历史上下文 ==========
        history_text = ""
        history = []

        if memory:
            history = await memory.get_conversation_history(session_id, limit=6)
            for msg in history:
                role = "用户" if msg.get("role") == "user" else "客服"
                content = msg.get("content", "")
                history_text += f"{role}: {content}\n"

        # ========== 2. 提取订单号（接受任何3位以上数字）==========
        order_id = self._extract_order_id(user_message, history)

        # 从 memory 读取已保存的订单信息
        order_info = None
        if memory:
            saved_order = await memory.get_order_info(session_id)
            if saved_order:
                order_id = order_id or saved_order.get("order_id")
                order_info = saved_order.get("order_details")

        # ========== 3. 判断用户意图 ==========
        is_refund_request = any(
            kw in user_message
            for kw in ["退货", "退款", "退钱", "退差价", "坏了", "质量问题", "换货"]
        )
        has_order_id = order_id is not None

        # ========== 4. 政策咨询检测（优先级高于"问订单号"）==========
        POLICY_KEYWORDS = [
            "地址", "流程", "怎么退", "政策", "条件",
            "规则", "期限", "多久", "运费", "包装",
            "怎么申请", "如何退", "步骤", "时效", "到账",
        ]
        is_policy_question = any(kw in user_message for kw in POLICY_KEYWORDS)

        if is_policy_question:
            try:
                if any(kw in user_message for kw in ["保修", "维修", "质保"]):
                    policy_type = "保修政策"
                elif any(kw in user_message for kw in ["配送", "物流", "快递"]) and not any(
                    kw in user_message for kw in ["退货", "退款", "退换"]
                ):
                    policy_type = "配送政策"
                else:
                    policy_type = "退货政策"

                policy_result = search_policy.invoke({"policy_type": policy_type})
                if isinstance(policy_result, dict) and not policy_result.get("error"):
                    title = policy_result.get("title", policy_type)
                    content = policy_result.get("content", "").strip()
                    reply_text = f"【{title}】\n{content}" if title else content
                    if reply_text.strip():
                        return {
                            "reply": reply_text,
                            "agent_used": "refund",
                            "confidence": 0.92,
                            "actions": ["查询退货政策"],
                            "handled": True
                        }
            except Exception:
                pass

        # ========== 5. 有订单号 → 直接处理 ==========
        if has_order_id and is_refund_request:
            order_result = query_order_status.invoke({"order_id": order_id})

            if memory:
                await memory.set_order_info(session_id, order_id, order_result)
                await memory.set_agent_context(session_id, "refund", {
                    "order_id": order_id,
                    "status": order_result.get("status", "")
                })

            if order_result.get("refundable", True):
                refund_result = approve_refund.invoke({"order_id": order_id})
                return {
                    "reply": f"已查询订单{order_id}：{order_result.get('status')}，金额{order_result.get('amount')}元。符合退货条件，已为您提交退款，{refund_result.get('refund_amount', '')}元将在3-5个工作日退回。",
                    "agent_used": "refund",
                    "confidence": 0.95,
                    "actions": ["查询订单", "审批退款"],
                    "handled": True
                }
            else:
                return {
                    "reply": f"订单{order_id}：{order_result.get('status')}，不符合退货条件。原因：{order_result.get('reason', '超出退货期限')}",
                    "agent_used": "refund",
                    "confidence": 0.90,
                    "actions": ["查询订单"],
                    "handled": True
                }

        # ========== 6. 无订单号但想退货 → 问一次 ==========
        if is_refund_request and not has_order_id:
            # 检查最近一轮对话是否已经问过订单号
            already_asked = any(
                kw in history_text
                for kw in ["订单号是多少", "请提供您的订单号", "请提供订单号", "您的订单号"]
            )
            if already_asked:
                return {
                    "reply": "请提供您的订单号，我需要查询订单状态后才能为您处理退款。",
                    "agent_used": "refund",
                    "confidence": 0.85,
                    "actions": ["引导提供订单号"],
                    "handled": True
                }
            return {
                "reply": "请问您的订单号是多少？",
                "agent_used": "refund",
                "confidence": 0.85,
                "actions": ["询问订单号"],
                "handled": True
            }

        # ========== 7. 其他情况 → 交由异步流式（astream）处理 ==========
        # 不再同步调用阻塞式 self.execute（会阻塞事件循环），
        # 返回“未处理”标记，由 stream_with_memory 走 astream 真流式。
        return {"handled": False, "reply": ""}

    # ==================== 流式输出包装 ====================

    async def stream_with_memory(
        self,
        user_message: str,
        session_id: str,
        memory,
    ):
        """使用记忆的流式处理"""
        result = await self.execute_with_memory(user_message, session_id, memory)
        is_rule_handled = result.get("handled") is True or bool(result.get("actions"))
        if is_rule_handled:
            reply = result["reply"]
            chunk_size = 5
            for i in range(0, len(reply), chunk_size):
                yield reply[i:i + chunk_size]
        else:
            messages = [{"role": "user", "content": user_message}]
            async for chunk in self.astream(messages):
                yield chunk