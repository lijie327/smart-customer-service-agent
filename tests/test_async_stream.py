"""
异步 + 真流式 SSE 离线冒烟测试（不依赖网络 / 阿里云 / FAISS）。

用 FakeLLM 替代 QwenLLM，验证：
1. BaseAgent.aexecute —— 真·异步 ReAct 工具调用循环
2. BaseAgent.astream —— 最终回答走 LLM 真流式逐 token 输出
3. RouterAgent.aroute / aclassify_intent —— 异步路由（关键词快路径 + LLM 分类）
4. TechAgent.astream —— 高置信 FAQ 直出 / 低置信走 LLM 真流式
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents.base_agent import BaseAgent
from backend.agents.router_agent import RouterAgent


class FakeTool:
    """模拟 LangChain 工具：有 name 与同步 invoke。"""
    def __init__(self, name, ret):
        self.name = name
        self._ret = ret

    def invoke(self, params):
        return self._ret


class FakeLLM:
    """模拟 QwenLLM 的异步接口。mode='tool' 返回工具调用，mode='final' 返回最终回答。"""
    def __init__(self, mode="final"):
        self.mode = mode

    async def ainvoke(self, messages, max_tokens=2048, temperature=0.7):
        if self.mode == "tool":
            return {
                "content": '[TOOL_CALL]fake_tool(query="x")[/TOOL_CALL]',
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        return {
            "content": "这是来自LLM的最终回答。",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    async def astream(self, messages, max_tokens=2048, temperature=0.7):
        text = "这是来自LLM的真流式回答，逐字吐出给前端。"
        for ch in text:
            yield ch


def test_base_aexecute_tool():
    llm = FakeLLM(mode="tool")
    tool = FakeTool("fake_tool", [{"answer": "工具返回的订单状态：已发货"}])
    agent = BaseAgent(name="Test", tools=[tool], system_prompt="x", llm=llm)
    result = asyncio.run(agent.aexecute([{"role": "user", "content": "查订单"}]))
    assert result["reply"] == "工具返回的订单状态：已发货", result
    assert result["agent"] == "Test"
    print("✓ BaseAgent.aexecute 异步工具调用路径 OK")


def test_base_astream_final():
    llm = FakeLLM(mode="final")
    agent = BaseAgent(name="Test", tools=[], system_prompt="x", llm=llm)
    chunks = []

    async def collect():
        async for c in agent.astream([{"role": "user", "content": "你好"}]):
            chunks.append(c)

    asyncio.run(collect())
    full = "".join(chunks)
    assert "真流式" in full, full
    assert len(chunks) > 1, "应当逐 token 输出而非整段返回"
    print(f"✓ BaseAgent.astream 真流式 OK（{len(chunks)} 个分片）")


def test_router_aroute_keyword():
    router = RouterAgent(FakeLLM())
    res = asyncio.run(router.aroute("我要退款"))
    assert res["agent_type"].value == "refund", res
    print("✓ RouterAgent.aroute 关键词快路径 OK")


def test_router_aroute_llm():
    class RouteLLM(FakeLLM):
        async def ainvoke(self, messages, max_tokens=2048, temperature=0.1):
            return {
                "content": '{"intent":"tech_support","confidence":0.9,"reason":"技术类"}',
                "usage": {},
            }

    router = RouterAgent(RouteLLM())
    res = asyncio.run(router.aroute("请问会员积分怎么兑换"))
    assert res["agent_type"].value == "tech_support", res
    print("✓ RouterAgent.aroute LLM 分类路径 OK")


def test_tech_astream():
    # 延迟导入，避免在缺少 faiss/numpy 的环境里 import 失败
    try:
        from backend.agents.tech_agent import TechAgent
    except ImportError as e:
        print(f"⚠ 跳过 TechAgent 测试（缺少依赖: {e}）")
        return

    class FakeRetriever:
        def search(self, q, k=3):
            return [{
                "question": "如何重置密码",
                "answer": "进入设置-账户-重置密码即可。",
                "confidence": 0.92,
                "score": 0.92,
            }]

    # 高置信 → 直接命中 FAQ（确定性文本逐字吐出，不调 LLM）
    agent_high = TechAgent(llm=FakeLLM(), retriever=FakeRetriever())
    chunks = []

    async def collect_high():
        async for c in agent_high.astream([{"role": "user", "content": "如何重置密码"}]):
            chunks.append(c)

    asyncio.run(collect_high())
    assert "重置密码" in "".join(chunks), chunks
    print(f"✓ TechAgent.astream 高置信 FAQ 直出 OK（{len(chunks)} 分片）")

    # 低置信（无命中）→ 走 LLM 真流式
    class EmptyRetriever:
        def search(self, q, k=3):
            return []

    agent_low = TechAgent(llm=FakeLLM(mode="final"), retriever=EmptyRetriever())
    chunks2 = []

    async def collect_low():
        async for c in agent_low.astream([{"role": "user", "content": "我的设备很奇怪"}]):
            chunks2.append(c)

    asyncio.run(collect_low())
    assert "真流式" in "".join(chunks2), chunks2
    print(f"✓ TechAgent.astream 低置信 LLM 真流式 OK（{len(chunks2)} 分片）")


if __name__ == "__main__":
    test_base_aexecute_tool()
    test_base_astream_final()
    test_router_aroute_keyword()
    test_router_aroute_llm()
    test_tech_astream()
    print("\n全部异步流式冒烟测试通过 ✓")
