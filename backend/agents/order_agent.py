"""订单查询Agent

处理订单状态查询、物流信息等
"""
import re
from backend.agents.base_agent import BaseAgent
from backend.llm import QwenLLM
from backend.tools import query_order_status, get_order_detail


class OrderAgent(BaseAgent):
    """订单查询专员"""

    def __init__(self, llm: QwenLLM, memory=None):
        """
        初始化OrderAgent

        Args:
            llm: QwenLLM实例
            memory: ConversationMemory实例
        """
        tools = [query_order_status, get_order_detail]

        system_prompt = """你是专业的订单查询专员。

核心规则：
1. 接受任何格式的订单号，数字/字母/组合都行，不要验证格式
2. 用户给什么就用什么查，直接调工具，不要反问确认
3. 从对话历史中提取订单号，如果3轮内提供过直接使用
4. 用户说"是的""对""确认"时，用历史中最后一个订单号查询
5. 简洁回复，15字以内告知结果
6. 不要说"您好"、不要废话、不要自我介绍

可用工具（通过 function calling 直接调用）：
- query_order_status(order_id)：查询订单状态
- get_order_detail(order_id)：查询订单详情"""

        super().__init__(
            name="OrderAgent",
            tools=tools,
            system_prompt=system_prompt,
            llm=llm
        )
        self.memory = memory

    def execute(self, messages=None, session_id: str = None, user_message: str = None, **kwargs) -> dict:
        """
        处理订单查询请求（兼容 BaseAgent.execute 和直接调用）

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]（兼容 BaseAgent 调用）
            session_id: 会话ID
            user_message: 用户消息字符串（直接调用时使用）
            **kwargs: 额外参数（兼容 BaseAgent max_iterations 等）

        Returns:
            {"reply": str, "agent_used": str, "confidence": float, "actions": list}
        """
        # 兼容多种调用方式：提取用户消息文本
        if not user_message:
            if isinstance(messages, list):
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        user_message = msg.get("content", "")
                        break
            elif isinstance(messages, str):
                user_message = messages
            else:
                user_message = str(messages) if messages else ""

        if not user_message:
            return {
                "reply": "请提供订单号以便查询",
                "agent_used": "order",
                "confidence": 0.80,
                "actions": []
            }

        # ========== 1. 从消息中提取订单号 ==========
        order_id = None

        # 提取当前消息中的订单号（任意数字字母组合）
        match = re.search(r'[\d]{3,}|[a-zA-Z0-9]{5,}', user_message)
        if match:
            order_id = match.group()

        # ========== 2. 从历史中提取订单号 ==========
        # execute 为同步方法；在异步上下文（/api/chat）中，订单号提取已在 api.py 中完成
        # 这里仅处理同步调用场景（内存缓存 fallback 模式）
        if not order_id and self.memory and session_id:
            try:
                history_result = self.memory.get_conversation_history(session_id, limit=6)
                import asyncio
                if asyncio.iscoroutine(history_result):
                    # 异步协程无法在同步方法中 await，但在异步上下文中
                    # 订单号已由 api.py 预先提取，安全跳过
                    history = None
                else:
                    history = history_result
            except Exception:
                history = None
            if history:
                for msg in reversed(history):
                    if msg.get("role") == "user":
                        hist_match = re.search(r'[\d]{3,}|[a-zA-Z0-9]{5,}', msg.get("content", ""))
                        if hist_match:
                            order_id = hist_match.group()
                            break

        # ========== 3. 有订单号 → 直接查 ==========
        if order_id:
            try:
                result = query_order_status.invoke({"order_id": order_id})
                status = result.get("status", "未知")
                return {
                    "reply": f"订单{order_id}：{status}",
                    "agent_used": "order",
                    "confidence": 0.95,
                    "actions": ["查询订单"]
                }
            except Exception as e:
                return {
                    "reply": f"查询失败：{str(e)}",
                    "agent_used": "order",
                    "confidence": 0.90,
                    "actions": ["查询订单"]
                }

        # ========== 4. 无订单号 → 让 LLM 用自己的知识回答 ==========
        return super().execute(messages, **kwargs)

    def query_order(self, order_id: str) -> dict:
        """查询订单信息的便捷方法"""
        status_result = query_order_status.invoke({"order_id": order_id})
        detail_result = get_order_detail.invoke({"order_id": order_id})
        return {
            "order_id": order_id,
            "status": status_result,
            "detail": detail_result
        }

    def batch_query(self, order_ids: list) -> list:
        """批量查询订单"""
        results = []
        for order_id in order_ids:
            try:
                result = self.query_order(order_id)
                results.append(result)
            except Exception as e:
                results.append({"order_id": order_id, "error": str(e)})
        return results