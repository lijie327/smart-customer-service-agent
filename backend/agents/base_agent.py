"""基础 Agent 类 —— 基于 LangChain bind_tools 原生 function calling 构建。

核心变化（相比旧手写实现）：
- 工具调度不再用手写正则解析 `[TOOL_CALL]...[/TOOL_CALL]`，而是用 LangChain 原生
  `bind_tools` + `AIMessage.tool_calls` 的 function calling 协议；
- ReAct 循环（决策 → 调工具 → 观察 → 再决策）保留，但工具调用解析改为读取模型返回的
  结构化 tool_calls，删除了脆弱的正则解析代码；
- `astream` 通过真实 `llm.astream` 逐 token 输出最终回答，并保持对工具调用链的追踪。

对外契约保持不变：`execute`/`aexecute` 返回
`{"reply", "agent", "tool_calls", "iterations", "usage"}`；`astream` 是逐字符串的
异步生成器，供 SSE 直接消费。
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.llm import QwenLLM

logger = logging.getLogger(__name__)


class BaseAgent:
    """基础 Agent：bind_tools 原生 function calling + ReAct 工具循环。"""

    def __init__(
        self,
        name: str,
        tools: List[Any],
        system_prompt: str,
        llm: QwenLLM = None,
        max_iterations: int = 3,
    ):
        self.name = name
        self.tools = {tool.name: tool for tool in tools}
        self.system_prompt = system_prompt
        self.llm = llm
        self.max_iterations = max_iterations
        # 绑定工具，使模型可以返回原生 tool_calls
        self.llm_with_tools = llm.bind_tools(tools) if llm is not None else None

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _to_lc_messages(messages) -> List[Any]:
        """把 [{"role", "content"}] 或混合消息列表转成 LangChain 消息对象。"""
        if not messages:
            return []
        lc: List[Any] = []
        for m in messages:
            if isinstance(m, (HumanMessage, AIMessage, SystemMessage, ToolMessage)):
                lc.append(m)
                continue
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                lc.append(HumanMessage(content=content))
            elif role == "assistant":
                lc.append(AIMessage(content=content))
            elif role == "system":
                lc.append(SystemMessage(content=content))
        return lc

    @staticmethod
    def _extract_user_text(messages) -> str:
        if isinstance(messages, str):
            return messages
        if isinstance(messages, list):
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    return m.get("content", "")
                if isinstance(m, HumanMessage):
                    return m.content
        return ""

    def _build_messages(self, messages) -> List[Any]:
        full: List[Any] = [SystemMessage(content=self.system_prompt)]
        full.extend(self._to_lc_messages(messages))
        return full

    @staticmethod
    def _build_tool_calls_history(calls) -> List[Dict[str, Any]]:
        return [
            {
                "tool": c.get("tool", ""),
                "params": c.get("params", {}) or {},
                "result": c.get("result"),
            }
            for c in calls
        ]

    # ------------------------------------------------------------------
    # 工具执行
    # ------------------------------------------------------------------
    async def _execute_tool(self, tool_name: str, args: Dict[str, Any], tool_call_id: str):
        """执行单个工具，返回 (observation, error?)。"""
        tool = self.tools.get(tool_name)
        try:
            if tool is None:
                return {"error": f"工具 {tool_name} 不存在"}, True
            result = await asyncio.to_thread(tool.invoke, args or {})
            return result, False
        except Exception as e:  # noqa: BLE001
            logger.error("工具 %s 执行失败: %s", tool_name, e)
            return {"error": f"工具执行失败: {str(e)}"}, True

    # ------------------------------------------------------------------
    # 执行入口
    # ------------------------------------------------------------------
    def execute(
        self,
        messages=None,
        user_message: Optional[str] = None,
        max_iterations: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """同步执行（工具调用经线程池异步化，不阻塞事件循环）。

        兼容在同步上下文或事件循环内调用：若当前已有运行中的循环，则委托给独立线程运行。
        """
        coro = self.aexecute(messages, user_message, max_iterations, **kwargs)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return asyncio.run(coro)
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(coro)).result()
        return loop.run_until_complete(coro)

    async def aexecute(
        self,
        messages=None,
        user_message: Optional[str] = None,
        max_iterations: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """异步 ReAct 循环：原生 function calling 驱动工具调度。"""
        if user_message is None:
            user_message = self._extract_user_text(messages)
        full_messages = self._build_messages(messages)
        max_iter = max_iterations or self.max_iterations
        tool_calls: List[Dict[str, Any]] = []
        iterations = 0

        while iterations < max_iter:
            iterations += 1
            response = await self.llm_with_tools.ainvoke(full_messages)

            # 无工具调用 → 最终回答
            if not getattr(response, "tool_calls", None):
                reply = response.content or ""
                return {
                    "reply": reply,
                    "agent": self.name,
                    "tool_calls": self._build_tool_calls_history(tool_calls),
                    "iterations": iterations,
                    "usage": {},
                }

            # 有工具调用 → 逐个执行并回填 ToolMessage
            full_messages.append(
                AIMessage(content=response.content or "", tool_calls=response.tool_calls)
            )
            for tc in response.tool_calls:
                name = tc.get("name")
                args = tc.get("args", {}) or {}
                call_id = tc.get("id") or f"call_{name}"
                observation, _ = await self._execute_tool(name, args, call_id)
                tool_calls.append(
                    {"tool": name, "params": args, "result": observation}
                )
                full_messages.append(
                    ToolMessage(content=str(observation), name=name, tool_call_id=call_id)
                )

        return {
            "reply": "抱歉，我暂时无法处理您的问题，建议转接人工客服获得更准确的帮助。",
            "agent": self.name,
            "tool_calls": self._build_tool_calls_history(tool_calls),
            "iterations": iterations,
            "error": "超过最大迭代次数",
        }

    async def astream(self, messages, max_iterations: Optional[int] = None):
        """真·异步流式：先 ReAct 决策（ainvoke），最终回答走 llm.astream 逐 token 输出。

        工具调用阶段不产生文本，仅最终回答阶段流式吐字；同时记录工具调用链。
        """
        if self.llm_with_tools is None:
            return
        full_messages = self._build_messages(messages)
        max_iter = max_iterations or self.max_iterations
        iterations = 0

        while iterations < max_iter:
            iterations += 1
            response = await self.llm_with_tools.ainvoke(full_messages)

            if not getattr(response, "tool_calls", None):
                # 最终回答：真实逐 token 流式
                async for chunk in self.llm.astream(full_messages):
                    yield chunk
                return

            full_messages.append(
                AIMessage(content=response.content or "", tool_calls=response.tool_calls)
            )
            for tc in response.tool_calls:
                name = tc.get("name")
                args = tc.get("args", {}) or {}
                call_id = tc.get("id") or f"call_{name}"
                observation, _ = await self._execute_tool(name, args, call_id)
                full_messages.append(
                    ToolMessage(content=str(observation), name=name, tool_call_id=call_id)
                )

        yield "抱歉，我暂时无法处理您的问题，建议转接人工客服获得更准确的帮助。"
