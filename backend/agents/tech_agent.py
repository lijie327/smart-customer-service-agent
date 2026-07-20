"""技术支持Agent

处理产品使用、故障排除等技术问题
"""
import asyncio
import re
from backend.agents.base_agent import BaseAgent
from backend.llm import QwenLLM
from backend.rag import FAQProcessor
from backend.rag_retriever import HybridFAQRetriever
from backend.tools import search_faq
from backend.config import RAG_CONF_HIGH as CONF_HIGH, RAG_CONF_LOW as CONF_LOW


# 检索置信度阈值（用于「规则优先防幻觉」与「低置信转人工」）
# 实际值来自 backend.config，保持 tech_agent 内部使用的别名一致
CONF_HIGH = CONF_HIGH   # 高于此值直接命中 FAQ，不再调用 LLM（规则优先）
CONF_LOW = CONF_LOW    # 低于此值标记为低置信，建议转人工客服


class TechAgent(BaseAgent):
    """技术支持专家"""

    def __init__(self, llm: QwenLLM, faq_processor: FAQProcessor = None,
                 memory=None, retriever: HybridFAQRetriever = None):
        """
        初始化TechAgent

        Args:
            llm: QwenLLM实例
            faq_processor: FAQ处理器实例，用于RAG检索
            memory: ConversationMemory实例
            retriever: 统一混合检索器（向量+BM25+RRF），缺省时由 faq_processor 构造
        """
        tools = [search_faq]

        system_prompt = """你是专业的技术支持专家。

核心规则：
1. 直接回答问题，不要反问、不要废话
2. 先检索FAQ再回答，参考FAQ内容
3. 简洁回复，20字以内
4. 不要说"您好"、不要自我介绍
5. 如果FAQ有答案直接给，没有就说"建议联系人工客服"

可用工具：search_faq（检索 FAQ 知识库，通过 function calling 直接调用）"""

        super().__init__(
            name="TechAgent",
            tools=tools,
            system_prompt=system_prompt,
            llm=llm
        )
        self.faq_processor = faq_processor
        self.retriever = retriever or (HybridFAQRetriever(faq_processor) if faq_processor else None)
        self.memory = memory

    def _retrieve(self, user_message: str):
        """统一混合检索并做规则优先决策。

        Returns:
            (direct_reply_dict | None, faq_results)
            - 高置信 FAQ 命中时返回可直接作答的字典（无需调 LLM）；
            - 否则返回 None，交由 LLM 基于候选作答。
        """
        faq_results = []
        if self.retriever:
            faq_results = self.retriever.search(user_message, k=3)
        elif self.faq_processor:
            faq_results = self.faq_processor.search(user_message, k=2)

        if faq_results:
            best = faq_results[0]
            confidence = best.get("confidence", best.get("score", 0.0))
            if confidence >= CONF_HIGH:
                return {
                    "reply": best.get("answer", "建议联系人工客服"),
                    "agent_used": "tech",
                    "confidence": confidence,
                    "actions": ["RAG混合检索"],
                    "sources": [r["question"] for r in faq_results[:3]],
                    "citations": [
                        {"question": r["question"], "answer": r["answer"],
                         "score": r.get("score")}
                        for r in faq_results[:3]
                    ],
                    "low_confidence": False,
                }, faq_results
        return None, faq_results

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

        # 1. 规则优先：高置信 FAQ 直接命中，避免 LLM 幻觉
        direct, faq_results = self._retrieve(user_message)
        if direct is not None:
            return direct

        # 2. 低/中置信 → 交 LLM 基于检索候选作答（工具已统一为混合检索）
        result = super().execute(messages, **kwargs)
        result["agent_used"] = "tech"
        top_conf = (
            faq_results[0].get("confidence", faq_results[0].get("score", 0.0))
            if faq_results else 0.3
        )
        result["confidence"] = top_conf
        result["actions"] = (result.get("actions") or []) + ["RAG混合检索"]
        if faq_results:
            result["sources"] = [r["question"] for r in faq_results[:3]]
            result["citations"] = [
                {"question": r["question"], "answer": r["answer"],
                 "score": r.get("score")}
                for r in faq_results[:3]
            ]
        result["low_confidence"] = top_conf < CONF_LOW
        return result

    async def astream(self, messages, max_iterations: int = 3):
        """
        技术支持 Agent 的真·异步流式：

        - 高置信 FAQ 直接命中 → 确定性短文本逐字吐出（无需调用 LLM）；
        - 否则 → 走 BaseAgent 的 ReAct + LLM 真流式输出。
        """
        user_message = ""
        if isinstance(messages, list):
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_message = msg.get("content", "")
                    break
        elif isinstance(messages, str):
            user_message = messages

        if user_message:
            direct, _ = await asyncio.to_thread(self._retrieve, user_message)
        else:
            direct = None

        if direct is not None:
            for i in range(0, len(direct["reply"]), 5):
                yield direct["reply"][i:i + 5]
            return

        async for chunk in super().astream(messages, max_iterations):
            yield chunk

    def diagnose_problem(self, problem_description: str) -> dict:
        """诊断技术问题"""
        result = self.execute(user_message=problem_description)
        return {
            "problem": problem_description,
            "diagnosis": result["reply"],
            "actions": result.get("actions", [])
        }