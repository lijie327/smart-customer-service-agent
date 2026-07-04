"""基础Agent类

所有专业Agent的基类
"""
import json
import re
from typing import List, Dict, Any, Optional, Callable
from backend.llm import QwenLLM


class BaseAgent:
    """基础Agent类"""

    def __init__(
        self,
        name: str,
        tools: List[Any],
        system_prompt: str,
        llm: QwenLLM = None
    ):
        self.name = name
        self.tools = {tool.name: tool for tool in tools}
        self.system_prompt = system_prompt
        self.llm = llm

    def _build_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        full_messages = [{"role": "system", "content": self.system_prompt}]
        full_messages.extend(messages)
        return full_messages

    def _parse_tool_call(self, content: str) -> Optional[Dict[str, Any]]:
        # 解析 JSON 格式（支持嵌套的简单对象）
        try:
            json_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\}))*"tool"(?:[^{}]|(?:\{[^{}]*\}))*\}'
            matches = re.findall(json_pattern, content, re.DOTALL)
            for match in matches:
                data = json.loads(match)
                if "tool" in data:
                    return {"tool_name": data["tool"], "params": data.get("params", {})}
        except Exception:
            pass

        # 解析标记格式 [TOOL_CALL]xxx[/TOOL_CALL]
        tool_call_pattern = r'\[TOOL_CALL\](.*?)\[/TOOL_CALL\]'
        match = re.search(tool_call_pattern, content, re.DOTALL)
        if match:
            tool_call_str = match.group(1).strip()
            func_pattern = r'(\w+)\((.*?)\)'
            func_match = re.match(func_pattern, tool_call_str, re.DOTALL)
            if func_match:
                tool_name = func_match.group(1)
                params_str = func_match.group(2)
                params = {}
                if params_str.strip():
                    param_pairs = re.findall(r'(\w+)\s*=\s*([^,]+)', params_str)
                    for key, value in param_pairs:
                        try:
                            params[key.strip()] = json.loads(value.strip())
                        except Exception:
                            params[key.strip()] = value.strip().strip('"\'')
                return {"tool_name": tool_name, "params": params}
        return None

    def _execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        if tool_name not in self.tools:
            return {"error": f"工具 {tool_name} 不存在"}
        try:
            tool = self.tools[tool_name]
            result = tool.invoke(params)
            return result
        except Exception as e:
            return {"error": f"工具执行失败: {str(e)}"}

    def _is_empty_tool_result(self, tool_result: Any) -> bool:
        """判断工具结果是否为空（FAQ搜索无结果、查询无数据等）"""
        if tool_result is None:
            return True
        if isinstance(tool_result, list) and len(tool_result) == 0:
            return True
        if isinstance(tool_result, dict):
            if tool_result.get("error"):
                return True
            # search_policy 返回 None 时已被转成空
        if isinstance(tool_result, str) and tool_result.strip() in ("", "[]", "{}", "null", "None"):
            return True
        return False

    def execute(self, messages: List[Dict[str, str]], max_iterations: int = 3) -> Dict[str, Any]:
        full_messages = self._build_messages(messages)
        tool_calls_history = []
        iterations = 0

        while iterations < max_iterations:
            iterations += 1

            # 调用LLM
            response = self.llm.invoke(full_messages)
            content = response.get("content") or ""  # 防护：None / 空值统一转为空字符串

            # 检查是否有工具调用
            tool_call = self._parse_tool_call(content) if content else None

            if tool_call:
                tool_name = tool_call["tool_name"]
                params = tool_call["params"]
                tool_result = self._execute_tool(tool_name, params)
                tool_calls_history.append({
                    "tool": tool_name,
                    "params": params,
                    "result": tool_result
                })

                # 工具结果空 → 追加结果到消息，让 LLM 再回答一次（调用大模型兜底）
                if self._is_empty_tool_result(tool_result):
                    full_messages.append({"role": "assistant", "content": content})
                    full_messages.append({"role": "user", "content": (
                        f"工具 {tool_name} 返回了空结果。"
                        f"请根据你的知识直接回答用户的问题，不要再次调用工具。"
                        f"如果确实不知道答案，请诚实地告诉用户并建议转人工客服。"
                    )})
                    continue  # 回到 while 循环，让 LLM 再生成一次

                # 工具有结果 → 提取可读文本
                result_text = str(tool_result)
                # 如果是FAQ结果，取第一条答案
                if isinstance(tool_result, list) and len(tool_result) > 0:
                    first = tool_result[0]
                    if isinstance(first, dict):
                        result_text = first.get("answer", first.get("content", str(tool_result)))

                return {
                    "reply": result_text,
                    "agent": self.name,
                    "tool_calls": tool_calls_history,
                    "iterations": iterations,
                    "usage": response.get("usage", {})
                }
            else:
                # 无工具调用 → 清理标记后直接返回 LLM 回答
                clean_content = re.sub(r'\[TOOL_CALL\].*?\[/TOOL_CALL\]', '', content, flags=re.DOTALL).strip()
                if not clean_content:
                    # 兜底：内容全是工具调用标记，不返回原始标记给用户
                    clean_content = "抱歉，我暂时无法处理您的问题，请稍后重试。"
                return {
                    "reply": clean_content,
                    "agent": self.name,
                    "tool_calls": tool_calls_history,
                    "iterations": iterations,
                    "usage": response.get("usage", {})
                }

        return {
            "reply": "抱歉，我暂时无法处理您的问题，建议转接人工客服获得更准确的帮助。",
            "agent": self.name,
            "tool_calls": tool_calls_history,
            "iterations": iterations,
            "error": "超过最大迭代次数"
        }

    async def aexecute(self, messages: List[Dict[str, str]], max_iterations: int = 3) -> Dict[str, Any]:
        """
        异步执行 Agent（不阻塞 event loop）

        将同步 execute 放到线程池中执行。
        """
        import asyncio
        return await asyncio.to_thread(self.execute, messages, max_iterations)

    def stream(self, messages: List[Dict[str, str]]):
        """原始流式输出（不处理工具调用，仅透传 LLM 输出）"""
        full_messages = self._build_messages(messages)
        for chunk in self.llm.stream(full_messages):
            yield chunk

    def stream_with_tool_handling(self, messages: List[Dict[str, str]], max_iterations: int = 3):
        """
        带工具调用处理的流式输出

        先收集完整 LLM 响应 → 解析工具调用 → 执行工具 → 流式返回结果。
        与 execute() 逻辑一致，但以生成器形式返回，兼容 SSE 流式接口。

        Args:
            messages: 消息列表
            max_iterations: 最大工具调用迭代次数

        Yields:
            文本片段（模拟流式输出）
        """
        result = self.execute(messages, max_iterations)
        reply = result.get("reply", "") or ""
        # 兜底：如果 LLM 返回空回复，yield 兜底消息，避免上游收到空内容
        if not reply.strip():
            reply = "抱歉，我暂时无法处理您的问题，请稍后重试或转接人工客服。"
        # 模拟流式输出：每次输出若干字符
        chunk_size = max(1, len(reply) // 10) or 1
        for i in range(0, len(reply), chunk_size):
            yield reply[i:i + chunk_size]

    def get_tool_descriptions(self) -> List[Dict[str, str]]:
        return [
            {"name": name, "description": getattr(tool, 'description', str(tool))}
            for name, tool in self.tools.items()
        ]