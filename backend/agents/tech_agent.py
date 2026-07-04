"""技术支持Agent

处理产品使用、故障排除等技术问题
"""
import re
from backend.agents.base_agent import BaseAgent
from backend.llm import QwenLLM
from backend.rag import FAQProcessor
from backend.tools import search_faq


class TechAgent(BaseAgent):
    """技术支持专家"""

    def __init__(self, llm: QwenLLM, faq_processor: FAQProcessor = None, memory=None):
        """
        初始化TechAgent

        Args:
            llm: QwenLLM实例
            faq_processor: FAQ处理器实例，用于RAG检索
            memory: ConversationMemory实例
        """
        tools = [search_faq]

        system_prompt = """你是专业的技术支持专家。

核心规则：
1. 直接回答问题，不要反问、不要废话
2. 先检索FAQ再回答，参考FAQ内容
3. 简洁回复，20字以内
4. 不要说"您好"、不要自我介绍
5. 如果FAQ有答案直接给，没有就说"建议联系人工客服"

工具调用格式：
[TOOL_CALL]search_faq(query="问题关键词")[/TOOL_CALL]"""

        super().__init__(
            name="TechAgent",
            tools=tools,
            system_prompt=system_prompt,
            llm=llm
        )
        self.faq_processor = faq_processor
        self.memory = memory

    def execute(self, messages=None, session_id: str = None, user_message: str = None, **kwargs) -> dict:
        """
        处理技术支持请求（兼容 BaseAgent.execute 和直接调用）

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
                "reply": "请问有什么技术问题需要帮助？",
                "agent_used": "tech",
                "confidence": 0.80,
                "actions": []
            }

        # 1. 先查FAQ
        faq_results = []
        if self.faq_processor:
            faq_results = self.faq_processor.search(user_message, k=2)

        # 2. 有FAQ结果 → 直接返回
        if faq_results:
            best = faq_results[0]
            return {
                "reply": best.get("answer", "建议联系人工客服"),
                "agent_used": "tech",
                "confidence": 0.90,
                "actions": ["FAQ检索"],
                "sources": [best.get("question", "")]
            }

        # 3. 没找到 → 让 LLM 用自己的知识回答（调用 BaseAgent 的 LLM+工具循环）
        return super().execute(messages, **kwargs)

    def diagnose_problem(self, problem_description: str) -> dict:
        """诊断技术问题"""
        result = self.execute(user_message=problem_description)
        return {
            "problem": problem_description,
            "diagnosis": result["reply"],
            "actions": result.get("actions", [])
        }