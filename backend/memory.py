"""上下文记忆模块

使用 Redis 存储和读取对话上下文，确保 Agent 能够记住多轮对话中的关键信息
"""
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Redis 重连间隔（秒）
_RECONNECT_INTERVAL = 30


class ConversationMemory:
    """对话记忆 - 基于 Redis 的会话上下文存储"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
    ):
        """
        初始化 Redis 连接

        Args:
            host: Redis 主机地址
            port: Redis 端口
            db: Redis 数据库编号
            password: Redis 密码（可选）
        """
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._pool: Optional[redis.ConnectionPool] = None
        self._client: Optional[redis.Redis] = None
        self.use_redis = False
        self._last_redis_failure: float = 0.0
        # 内存回退缓存
        self._cache: Dict[str, Dict[str, Any]] = {}

    async def _ensure_connection(self) -> bool:
        """
        确保 Redis 连接可用，如果断开则尝试重连

        Returns:
            连接是否可用
        """
        if self._client is not None and self.use_redis:
            try:
                await self._client.ping()
                return True
            except Exception:
                logger.warning("Redis ping 失败，尝试重新连接...")
                self.use_redis = False
                self._client = None

        # 尝试建立连接
        try:
            if self._pool is None:
                self._pool = redis.ConnectionPool(
                    host=self._host,
                    port=self._port,
                    db=self._db,
                    password=self._password,
                    decode_responses=True,
                    max_connections=10,
                    socket_connect_timeout=5,
                    socket_keepalive=True,
                    health_check_interval=30,
                )
            self._client = redis.Redis(connection_pool=self._pool)
            await self._client.ping()
            self.use_redis = True
            logger.info(
                "Redis 连接成功: %s:%s (db=%s)", self._host, self._port, self._db
            )
            return True
        except Exception as e:
            self.use_redis = False
            self._client = None
            logger.warning(
                "Redis 连接失败 (%s:%s db=%s): %s，将使用内存缓存",
                self._host,
                self._port,
                self._db,
                e,
            )
            return False

    async def _try_reconnect(self) -> bool:
        """
        如果距离上次失败已超过重连间隔，尝试重新连接 Redis。
        避免在每次操作失败时都重试（防止雪崩），而是定时重试。

        Returns:
            重连是否成功
        """
        now = time.time()
        if now - self._last_redis_failure < _RECONNECT_INTERVAL:
            return False
        self._last_redis_failure = now
        logger.info("尝试重新连接 Redis（距上次失败已过 %d 秒）...", _RECONNECT_INTERVAL)
        return await self._ensure_connection()

    def _mark_redis_failed(self):
        """标记 Redis 不可用，记录失败时间"""
        self.use_redis = False
        self._client = None
        self._last_redis_failure = time.time()

    def _get_session_key(self, session_id: str) -> str:
        """获取会话的 Redis key"""
        return f"conversation:{session_id}"

    def _get_context_key(self, session_id: str, context_type: str = "context") -> str:
        """获取特定类型上下文的 key"""
        return f"conversation:{session_id}:{context_type}"

    # ==================== 消息存储 ====================

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        保存单条消息到会话历史

        Args:
            session_id: 会话 ID
            role: 角色 (user/assistant)
            content: 消息内容
            metadata: 附加元数据（如 agent_used、confidence 等）
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **(metadata or {}),
        }

        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                if self._client is None:
                    self._save_to_cache(session_id, message)
                    return
                key = f"{self._get_session_key(session_id)}:messages"
                await self._client.rpush(key, json.dumps(message, ensure_ascii=False))
                await self._client.expire(key, 7 * 24 * 3600)
                await self._client.hset(
                    f"session:meta:{session_id}",
                    "last_active",
                    datetime.now().isoformat(),
                )
            except Exception as e:
                logger.error("保存消息到 Redis 失败: %s，回退到内存缓存", e)
                self._mark_redis_failed()
                self._save_to_cache(session_id, message)
        else:
            # 尝试重连 Redis
            if await self._try_reconnect():
                # 重连成功，递归调用自身重新尝试 Redis 写入
                await self.save_message(session_id, role, content, metadata)
                return
            self._save_to_cache(session_id, message)

    def _save_to_cache(self, session_id: str, message: Dict[str, Any]) -> None:
        """保存消息到内存缓存"""
        if session_id not in self._cache:
            self._cache[session_id] = {"messages": [], "meta": {}}
        self._cache[session_id]["messages"].append(message)

    async def get_conversation_history(
        self,
        session_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        获取会话历史记录

        Args:
            session_id: 会话 ID
            limit: 最多返回的消息数

        Returns:
            消息列表
        """
        messages: List[Dict[str, Any]] = []

        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                if self._client is None:
                    return self._get_cache_messages(session_id, limit)
                key = f"{self._get_session_key(session_id)}:messages"
                raw_messages = await self._client.lrange(key, 0, limit * 2)
                for msg_json in reversed(raw_messages[:limit]):
                    try:
                        messages.append(json.loads(msg_json))
                    except json.JSONDecodeError:
                        logger.warning("解析消息 JSON 失败: %s", msg_json[:100])
            except Exception as e:
                logger.error("从 Redis 获取历史消息失败: %s，回退到内存缓存", e)
                self._mark_redis_failed()
                messages = self._get_cache_messages(session_id, limit)
        else:
            if await self._try_reconnect():
                return await self.get_conversation_history(session_id, limit)
            messages = self._get_cache_messages(session_id, limit)

        return [m for m in messages if isinstance(m, dict)]

    def _get_cache_messages(
        self, session_id: str, limit: int
    ) -> List[Dict[str, Any]]:
        """从内存缓存获取消息"""
        if session_id in self._cache:
            return self._cache[session_id]["messages"][-limit:]
        return []

    async def get_last_user_message(self, session_id: str) -> Optional[str]:
        """获取最后一个用户消息"""
        messages = await self.get_conversation_history(session_id, limit=1)
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content")
        return None

    # ==================== Agent 上下文 ====================

    async def set_agent_context(
        self, session_id: str, agent_type: str, context_data: Dict[str, Any]
    ) -> None:
        """
        设置当前 Agent 的上下文数据（如订单号、退货政策信息等）

        Args:
            session_id: 会话 ID
            agent_type: Agent 类型（refund/tech_support 等）
            context_data: 上下文数据字典
        """
        data = {
            "agent_type": agent_type,
            "updated_at": datetime.now().isoformat(),
            **context_data,
        }

        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                if self._client is None:
                    self._cache_set_context(session_id, agent_type, data)
                    return
                key = self._get_context_key(session_id, "context")
                await self._client.setex(
                    key, 3600, json.dumps(data, ensure_ascii=False)
                )
            except Exception as e:
                logger.error("保存 Agent 上下文到 Redis 失败: %s", e)
                self._mark_redis_failed()
                self._cache_set_context(session_id, agent_type, data)
        else:
            if await self._try_reconnect():
                await self.set_agent_context(session_id, agent_type, context_data)
                return
            self._cache_set_context(session_id, agent_type, data)

    def _cache_set_context(
        self, session_id: str, agent_type: str, data: Dict[str, Any]
    ) -> None:
        """保存 Agent 上下文到内存缓存"""
        if session_id not in self._cache:
            self._cache[session_id] = {"messages": [], "meta": {}, "contexts": {}}
        self._cache[session_id]["contexts"][agent_type] = data

    async def get_agent_context(
        self, session_id: str, agent_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取指定 Agent 类型的上下文数据

        Args:
            session_id: 会话 ID
            agent_type: Agent 类型

        Returns:
            上下文数据或 None
        """
        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                if self._client is None:
                    return self._cache_get_context(session_id, agent_type)
                key = self._get_context_key(session_id, "context")
                data = await self._client.get(key)
                if data:
                    result = json.loads(data)
                    if agent_type == "refund":
                        order_key = self._get_context_key(session_id, "order_info")
                        order_data = await self._client.get(order_key)
                        if order_data:
                            result.update(json.loads(order_data))
                    return result
            except Exception as e:
                logger.error("从 Redis 获取 Agent 上下文失败: %s", e)
                self._mark_redis_failed()
                return self._cache_get_context(session_id, agent_type)
        else:
            if await self._try_reconnect():
                return await self.get_agent_context(session_id, agent_type)
            return self._cache_get_context(session_id, agent_type)

    def _cache_get_context(
        self, session_id: str, agent_type: str
    ) -> Optional[Dict[str, Any]]:
        """从内存缓存获取 Agent 上下文"""
        if session_id in self._cache:
            contexts = self._cache[session_id].get("contexts", {})
            if agent_type in contexts:
                return contexts[agent_type]
            if agent_type == "refund" and "order_info" in contexts:
                return contexts["order_info"]
        return None

    # ==================== 订单信息 ====================

    async def set_order_info(
        self, session_id: str, order_id: str, order_details: Dict[str, Any]
    ) -> None:
        """
        保存订单信息到上下文

        Args:
            session_id: 会话 ID
            order_id: 订单 ID
            order_details: 订单详细信息
        """
        data = {
            "order_id": order_id,
            "order_details": order_details,
            "retrieved_at": datetime.now().isoformat(),
        }

        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                if self._client is None:
                    self._cache_set_order(session_id, data)
                    return
                key = self._get_context_key(session_id, "order_info")
                await self._client.setex(
                    key, 3600, json.dumps(data, ensure_ascii=False)
                )
            except Exception as e:
                logger.error("保存订单信息到 Redis 失败: %s", e)
                self._mark_redis_failed()
                self._cache_set_order(session_id, data)
        else:
            if await self._try_reconnect():
                await self.set_order_info(session_id, order_id, order_details)
                return
            self._cache_set_order(session_id, data)

    def _cache_set_order(self, session_id: str, data: Dict[str, Any]) -> None:
        """保存订单信息到内存缓存"""
        if session_id not in self._cache:
            self._cache[session_id] = {"messages": [], "meta": {}, "contexts": {}}
        self._cache[session_id]["contexts"]["order_info"] = data

    async def get_order_info(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取订单信息

        Args:
            session_id: 会话 ID

        Returns:
            订单信息或 None
        """
        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                if self._client is None:
                    return self._cache_get_order(session_id)
                key = self._get_context_key(session_id, "order_info")
                data = await self._client.get(key)
                if data:
                    return json.loads(data)
            except Exception as e:
                logger.error("从 Redis 获取订单信息失败: %s", e)
                self._mark_redis_failed()
                return self._cache_get_order(session_id)
        else:
            if await self._try_reconnect():
                return await self.get_order_info(session_id)
            return self._cache_get_order(session_id)

    def _cache_get_order(self, session_id: str) -> Optional[Dict[str, Any]]:
        """从内存缓存获取订单信息"""
        if session_id in self._cache:
            contexts = self._cache[session_id].get("contexts", {})
            if "order_info" in contexts:
                return contexts["order_info"]
        return None

    # ==================== 会话管理 ====================

    async def is_recent_refund_session(
        self, session_id: str, window: int = 3
    ) -> bool:
        """
        判断是否是最近的退货流程会话（在 window 轮内聊过退货）

        Args:
            session_id: 会话 ID
            window: 检查的轮数

        Returns:
            如果是最近的退货会话则返回 True
        """
        messages = await self.get_conversation_history(session_id, limit=window * 2)
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("agent_used") == "refund":
                return True
        return False

    async def clear_session(self, session_id: str) -> None:
        """清空会话的所有数据"""
        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                if self._client is None:
                    self._cache_clear(session_id)
                    return
                # 使用 SCAN 代替 KEYS，避免阻塞
                pattern = f"conversation:{session_id}:*"
                async for key in self._client.scan_iter(match=pattern):
                    await self._client.delete(key)
                await self._client.delete(f"session:meta:{session_id}")
                logger.info("已清空会话: %s", session_id)
            except Exception as e:
                logger.error("清空 Redis 会话失败: %s", e)
                self._mark_redis_failed()
                self._cache_clear(session_id)
        else:
            if await self._try_reconnect():
                await self.clear_session(session_id)
                return
            self._cache_clear(session_id)

    def _cache_clear(self, session_id: str) -> None:
        """清空内存缓存中的会话"""
        if session_id in self._cache:
            del self._cache[session_id]

    async def delete_session(self, session_id: str) -> None:
        """彻底删除会话"""
        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                if self._client is None:
                    self._cache_clear(session_id)
                    return
                # 使用 SCAN 代替 KEYS，避免阻塞
                pattern = f"conversation:{session_id}:*"
                async for key in self._client.scan_iter(match=pattern):
                    await self._client.delete(key)
                await self._client.delete(f"session:meta:{session_id}")
                logger.info("已删除会话: %s", session_id)
            except Exception as e:
                logger.error("删除 Redis 会话失败: %s", e)
                self._mark_redis_failed()
                self._cache_clear(session_id)
        else:
            if await self._try_reconnect():
                await self.delete_session(session_id)
                return
            self._cache_clear(session_id)

    # ==================== 统计与健康检查 ====================

    async def get_stats(self) -> Dict[str, Any]:
        """获取内存使用统计"""
        if self.use_redis:
            try:
                if self._client is None:
                    await self._ensure_connection()
                info = await self._client.info() if self._client else {}
                return {
                    "use_redis": True,
                    "connected": bool(info),
                    "used_memory": info.get("used_memory_human", "unknown"),
                    "connected_clients": info.get("connected_clients", 0),
                }
            except Exception as e:
                logger.warning("获取 Redis 统计信息失败: %s", e)
                self._client = None
                return {"use_redis": True, "connected": False}
        else:
            active_sessions = len(self._cache)
            total_messages = sum(
                len(c.get("messages", [])) for c in self._cache.values()
            )
            return {
                "use_redis": False,
                "active_sessions": active_sessions,
                "total_messages_stored": total_messages,
            }

    async def health_check(self) -> bool:
        """
        执行健康检查，如果 Redis 从不可用恢复则自动重连

        Returns:
            Redis 当前是否可用
        """
        if not self.use_redis:
            # 之前连接失败，尝试重连
            logger.info("尝试重新连接 Redis...")
            return await self._ensure_connection()
        return await self._ensure_connection()

    async def close(self) -> None:
        """关闭 Redis 连接池"""
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
        self._client = None
        self.use_redis = False
        logger.info("Redis 连接已关闭")
