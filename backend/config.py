import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

_loaded = False


def _ensure_loaded():
    """确保 .env 已加载（延迟加载，支持测试注入）"""
    global _loaded
    if not _loaded:
        load_dotenv()
        _loaded = True


class Config:
    """应用配置类"""

    def __init__(self):
        _ensure_loaded()

    @property
    def DASHSCOPE_API_KEY(self) -> str:
        _ensure_loaded()
        key = os.getenv("DASHSCOPE_API_KEY")
        if not key:
            raise ValueError(
                "DASHSCOPE_API_KEY is not set. Please configure it in .env file."
            )
        return key

    @property
    def LLM_MODEL(self) -> str:
        return os.getenv("LLM_MODEL", "qwen-max")

    # 服务器配置（可通过环境变量覆盖，详见 .env.example）
    @property
    def HOST(self) -> str:
        return os.getenv("HOST", "0.0.0.0")

    @property
    def PORT(self) -> int:
        return int(os.getenv("PORT", "8000"))

    @property
    def REDIS_HOST(self) -> str:
        return os.getenv("REDIS_HOST", "localhost")

    @property
    def REDIS_PORT(self) -> int:
        return int(os.getenv("REDIS_PORT", "6379"))

    @property
    def REDIS_DB(self) -> int:
        return int(os.getenv("REDIS_DB", "0"))

    @property
    def REDIS_PASSWORD(self) -> Optional[str]:
        return os.getenv("REDIS_PASSWORD", None) or None

    # 上传目录（可通过环境变量覆盖，便于 Docker 挂载卷）
    @property
    def UPLOAD_DIR(self) -> str:
        return os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads"))

    # FAISS 索引目录（可通过环境变量覆盖）
    @property
    def FAQ_INDEX_PATH(self) -> str:
        return os.getenv("FAQ_INDEX_PATH", str(DATA_DIR / "faq_index"))

    # CORS：允许的来源（逗号分隔；默认 *）。带凭据时不可为 *
    @property
    def ALLOWED_ORIGINS(self) -> List[str]:
        raw = os.getenv("ALLOWED_ORIGINS", "*")
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def ALLOW_CREDENTIALS(self) -> bool:
        return os.getenv("ALLOW_CREDENTIALS", "false").lower() == "true"

    # 数据层：SQLite 数据库文件路径（可通过 DB_PATH 环境变量覆盖，详见 .env.example）
    @property
    def DB_PATH(self) -> str:
        env = os.getenv("DB_PATH")
        if env:
            return env
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "data", "smart_cs.db")

    @classmethod
    def reload(cls):
        """重新加载 .env 配置（用于运行时更新配置）"""
        global _loaded
        _loaded = False
        _ensure_loaded()


# ===== 路径常量（单一事实来源，便于 Docker 挂载卷） =====
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))

# ===== 模块级常量（供 import 直接引用，单一事实来源） =====
# RAG 检索置信度阈值（用于"规则优先防幻觉"与"低置信转人工兜底"）
RAG_CONF_HIGH: float = 0.60   # 高于此值直接命中 FAQ，不再调用 LLM（规则优先）
RAG_CONF_LOW: float = 0.35    # 低于此值标记低置信，触发转人工兜底

# 转人工触发阈值（与 RAG_CONF_LOW 同源，作为独立语义常量便于调参）
ESCALATION_CONFIDENCE: float = 0.35

# 链路追踪环形缓冲容量（进程内演示用）
TRACE_BUFFER_SIZE: int = 200


# 导出配置实例
config = Config()
