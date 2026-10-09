"""配置(J):集中管理运行期配置。

所有配置项都来自环境变量或项目根目录的 ``.env``,这样密钥既不会出现在代码里,
也不会被提交进仓库。读取优先级:环境变量 > .env > 默认值。
"""

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

    # --- A 模型层 ---
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
    max_steps: int = Field(default=30, ge=1, description="单次任务的最大步数(M1 起生效)")

    # --- J 工程 ---
    log_dir: Path = Field(default=Path(".code_agent/logs"), description="轨迹与日志目录")

    # --- F 执行环境 ---
    workspace_dir: Path = Field(
        default_factory=Path.cwd,
        description="工具执行的工作目录;默认使用启动时的当前目录,由核心规范化为绝对路径",
    )
    bash_path: str = Field(
        default="bash",
        min_length=1,
        description="Bash 可执行文件路径或 PATH 中的命令名;不会自动切换其他 shell",
    )


def load_settings() -> Settings:
    """加载配置。"""
    return Settings()
