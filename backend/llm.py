"""
阿里云百炼 LLM 和 Embedding 封装模块
DashScope SDK 兼容层
"""
import asyncio
import dashscope
from dashscope import Generation, TextEmbedding
from typing import List, Dict, Any, Generator


class QwenLLM:
    """通义千问大语言模型封装"""

    def __init__(self, api_key: str, model: str = "qwen-max"):
        """
        初始化 QwenLLM

        Args:
            api_key: DashScope API 密钥
            model: 模型名称，默认 qwen-max
        """
        dashscope.api_key = api_key
        self.model = model

    def invoke(
            self,
            messages: List[Dict[str, str]],
            max_tokens: int = 2048,
            temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        同步调用 LLM 生成回复

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            max_tokens: 最大 token 数
            temperature: 温度参数

        Returns:
            {"content": str, "usage": {...}}
        """
        try:
            response = Generation.call(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                result_format="message",
            )

            if response.status_code == 200:
                return {
                    "content": response.output.choices[0].message.content,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                }
            else:
                raise Exception(f"LLM调用失败: {response.code} - {response.message}")

        except Exception as e:
            raise Exception(f"LLM调用异常: {str(e)}")

    async def ainvoke(
            self,
            messages: List[Dict[str, str]],
            max_tokens: int = 2048,
            temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        异步调用 LLM（不阻塞 event loop）

        将同步 LLM 调用放到线程池中执行，避免阻塞 FastAPI event loop。
        """
        return await asyncio.to_thread(self.invoke, messages, max_tokens, temperature)

    def stream(
            self,
            messages: List[Dict[str, str]],
            max_tokens: int = 2048,
            temperature: float = 0.7,
    ) -> Generator[str, None, None]:
        """
        流式调用 LLM 生成回复

        Args:
            messages: 消息列表
            max_tokens: 最大 token 数
            temperature: 温度参数

        Yields:
            文本片段
        """
        try:
            responses = Generation.call(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                result_format="message",
                stream=True,
                incremental_output=True,
            )

            for response in responses:
                if response.status_code == 200:
                    chunk = response.output.choices[0].message.content
                    if chunk:
                        yield chunk
                else:
                    raise Exception(f"流式调用失败: {response.code} - {response.message}")

        except Exception as e:
            raise Exception(f"流式调用异常: {str(e)}")


class QwenEmbeddings:
    """通义千问文本向量化封装"""

    def __init__(self, api_key: str, model: str = "text-embedding-v2"):
        """
        初始化 QwenEmbeddings

        Args:
            api_key: DashScope API 密钥
            model: 嵌入模型名称
        """
        dashscope.api_key = api_key
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量文本转向量

        Args:
            texts: 文本列表

        Returns:
            向量列表
        """
        try:
            response = TextEmbedding.call(
                model=self.model,
                input=texts,
            )

            if response.status_code == 200:
                output = response.output

                # 兼容字典和对象两种格式
                if isinstance(output, dict):
                    embeddings_data = output.get('embeddings', [])
                else:
                    embeddings_data = output.embeddings

                # 提取向量
                result = []
                for item in embeddings_data:
                    if isinstance(item, dict):
                        result.append(item.get('embedding', []))
                    else:
                        result.append(item.embedding)
                return result
            else:
                raise Exception(f"Embedding失败: {response.code} - {response.message}")

        except Exception as e:
            raise Exception(f"Embedding调用异常: {str(e)}")

    def embed_query(self, text: str) -> List[float]:
        """
        单文本转向量

        Args:
            text: 查询文本

        Returns:
            向量
        """
        try:
            embeddings = self.embed_documents([text])
            return embeddings[0]
        except Exception as e:
            raise Exception(f"Embedding调用异常: {str(e)}")