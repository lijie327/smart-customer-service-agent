import os
from typing import Optional

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

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000

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

    # 上传目录
    UPLOAD_DIR: str = "./uploads"

    @classmethod
    def reload(cls):
        """重新加载 .env 配置（用于运行时更新配置）"""
        global _loaded
        _loaded = False
        _ensure_loaded()


# 导出配置实例
config = Config()
