"""阿里云百炼（通义千问）LLM / Embedding 封装 —— 基于 LangChain 标准组件。

- QwenLLM 继承 langchain_core BaseChatModel，成为标准 Runnable：
  invoke / ainvoke / stream / astream 统一接口，并通过 bind_tools 提供原生 function calling。
- QwenEmbeddings 继承 langchain_core Embeddings，可直接用于 FAISS / EnsembleRetriever 等检索器。

这样整个项目对 LLM 的调用都走 LangChain 的 Runnable 协议，工具调度由 AgentExecutor 托管，
而不是手写正则解析。
"""
import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any, Dict, List, Optional

import dashscope
from dashscope import Generation, TextEmbedding

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.base import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

logger = logging.getLogger(__name__)


def _lc_to_dashscope_messages(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    """将 LangChain 消息列表转换为 DashScope 消息格式（含 tool_calls / tool 回传）。"""
    out: List[Dict[str, Any]] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, SystemMessage):
            out.append({"role": "system", "content": m.content})
        elif isinstance(m, AIMessage):
            item: Dict[str, Any] = {"role": "assistant", "content": m.content or ""}
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ]
            out.append(item)
        elif isinstance(m, ToolMessage):
            out.append({"role": "tool", "name": m.name or "", "content": m.content})
        elif isinstance(m, ChatMessage):
            out.append({"role": m.role, "content": m.content})
        else:
            out.append({"role": "user", "content": str(m.content)})
    return out


class QwenLLM(BaseChatModel):
    """通义千问大语言模型（LangChain Runnable 封装）。

    支持标准 invoke / ainvoke / stream / astream，并通过 bind_tools 提供原生 function calling。
    同步调用经线程池桥接，不阻塞 FastAPI 事件循环。
    """

    # LangChain Runnable 字段（pydantic v2）
    api_key: Optional[str] = None
    model: str = "qwen-max"

    class Config:
        arbitrary_types_allowed = True

    # ---- 工具绑定：原生 function calling ----
    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ):
        """把 tools 绑定到模型，供 AgentExecutor / create_tool_calling_agent 使用。

        bind 会把 tools/tool_choice 透传进 _generate 的 kwargs，由 DashScope 原生 tool calling 处理。
        保留 _ChatModelBinding 类型，从而 astream_events 仍可用（真·流式所需）。
        """
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    # ---- 基类要求的标识 ----
    @property
    def _llm_type(self) -> str:
        return "qwen"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {"model": self.model}

    @property
    def _temperature(self) -> float:
        t = getattr(self, "temperature", None)
        return t if t is not None else 0.7

    @property
    def _max_tokens(self) -> int:
        mt = getattr(self, "max_tokens", None)
        return mt if mt is not None else 2048

    # ---- 工具 schema 转换 ----
    def _bind_tools_to_dashscope(self, tools: Any) -> List[Dict[str, Any]]:
        """把 bind_tools 传入的 tools（OpenAI schema 或 Tool 对象）转为 DashScope 格式。"""
        converted: List[Dict[str, Any]] = []
        for t in tools or []:
            if isinstance(t, dict) and ("function" in t or "type" in t):
                converted.append(t)
            else:
                try:
                    from langchain_core.utils.function_calling import (
                        convert_to_openai_tool,
                    )

                    converted.append(convert_to_openai_tool(t))
                except Exception:
                    logger.warning("无法转换工具 schema: %s", t)
        return converted

    # ---- 同步生成 ----
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.api_key:
            dashscope.api_key = self.api_key

        ds_messages = _lc_to_dashscope_messages(messages)
        gen_kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": ds_messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
            "result_format": "message",
        }
        tools = kwargs.get("tools")
        if tools:
            gen_kwargs["tools"] = self._bind_tools_to_dashscope(tools)
            tc = kwargs.get("tool_choice")
            if tc:
                gen_kwargs["tool_choice"] = tc

        response = Generation.call(**gen_kwargs)
        if response.status_code != 200:
            raise RuntimeError(f"LLM调用失败: {response.code} - {response.message}")

        msg = response.output.choices[0].message
        content = getattr(msg, "content", "") or ""
        tool_calls = getattr(msg, "tool_calls", None)

        if tool_calls:
            lc_tool_calls = []
            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name")
                args_str = func.get("arguments", "{}") or "{}"
                try:
                    args = json.loads(args_str)
                except Exception:
                    args = {"__raw__": args_str}
                lc_tool_calls.append(
                    {
                        "name": name,
                        "args": args,
                        "id": tc.get("id") or f"call_{name}",
                    }
                )
            ai_message = AIMessage(content=content, tool_calls=lc_tool_calls)
        else:
            ai_message = AIMessage(content=content)

        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await asyncio.to_thread(self._generate, messages, stop, run_manager, **kwargs)

    # ---- 流式 ----
    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        if self.api_key:
            dashscope.api_key = self.api_key
        ds_messages = _lc_to_dashscope_messages(messages)
        gen_kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": ds_messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", self._temperature),
            "result_format": "message",
            "stream": True,
            "incremental_output": True,
        }
        tools = kwargs.get("tools")
        if tools:
            gen_kwargs["tools"] = self._bind_tools_to_dashscope(tools)
        responses = Generation.call(**gen_kwargs)
        for response in responses:
            if response.status_code != 200:
                raise RuntimeError(f"流式调用失败: {response.code} - {response.message}")
            chunk = response.output.choices[0].message.content
            if chunk:
                yield ChatGenerationChunk(message=AIMessageChunk(content=chunk))

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ):
        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue" = asyncio.Queue()
        _DONE = object()

        def _producer() -> None:
            try:
                for chunk in self._stream(messages, stop, run_manager, **kwargs):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        worker = threading.Thread(target=_producer, daemon=True)
        worker.start()
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    # ---- 公开 stream / astream：对外直接吐文本字符串（保持历史契约）----
    # 注意：AgentExecutor.astream_events 走的是内部 _astream（yield ChatGenerationChunk），不受影响。
    def stream(
        self,
        messages: LanguageModelInput,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        for chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk.message.content

    async def astream(
        self,
        messages: LanguageModelInput,
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        async for chunk in self._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk.message.content


class QwenEmbeddings(Embeddings):
    """通义千问文本向量化（LangChain Embeddings 封装）。

    可直接用于 FAISS / EnsembleRetriever 等需要 Embeddings 接口的检索器。

    注意：langchain_core 的 `Embeddings` 是普通 ABC（非 pydantic 模型），
    因此这里必须显式定义 `__init__` 来接收 api_key / model，不能像
    `QwenLLM(BaseChatModel)` 那样靠 pydantic 字段注解。
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-v2"):
        super().__init__()
        self.api_key = api_key
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量文本转向量。"""
        if self.api_key:
            dashscope.api_key = self.api_key
        try:
            response = TextEmbedding.call(model=self.model, input=texts)
            if response.status_code == 200:
                output = response.output
                if isinstance(output, dict):
                    embeddings_data = output.get("embeddings", [])
                else:
                    embeddings_data = output.embeddings
                result = []
                for item in embeddings_data:
                    if isinstance(item, dict):
                        result.append(item.get("embedding", []))
                    else:
                        result.append(item.embedding)
                return result
            raise RuntimeError(f"Embedding失败: {response.code} - {response.message}")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Embedding调用异常: {str(e)}")

    def embed_query(self, text: str) -> List[float]:
        """单文本转向量。"""
        if self.api_key:
            dashscope.api_key = self.api_key
        try:
            embeddings = self.embed_documents([text])
            return embeddings[0]
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Embedding调用异常: {str(e)}")
