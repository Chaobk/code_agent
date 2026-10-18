"""运行配置，从环境变量和 .env 文件加载默认值。"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行期配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CODE_AGENT_",
        extra="ignore",
    )

    # 模型连接
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY"),
        description="DeepSeek API Key;留空则交给 litellm 自己从环境变量读取",
    )
    base_url: str = Field(default="https://api.deepseek.com", description="API base url")
    model: str = Field(
        default="deepseek/deepseek-chat",
        description="litellm 模型标识(带供应商前缀)",
    )
    max_steps: int = Field(default=30, ge=1, description="单次任务的最大模型请求轮数")

    # 日志
    log_dir: Path = Field(default=Path(".code_agent/logs"), description="轨迹与日志目录")

    # 工具执行环境
    workspace_dir: Path = Field(
        default_factory=Path.cwd,
        description="工具执行的工作目录;默认使用启动时的当前目录,由核心规范化为绝对路径",
    )
    bash_path: str = Field(
        default="bash",
        min_length=1,
        description="Bash 可执行文件路径或 PATH 中的命令名;不会自动切换其他 shell",
    )

    request_timeout_seconds: float = Field(
        default=60.0, gt=0, allow_inf_nan=False, description="模型请求的超时时间"
    )


def load_settings() -> Settings:
    """加载配置。"""
    return Settings()
