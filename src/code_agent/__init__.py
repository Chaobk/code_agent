"""编程 Agent。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("code-agent")
except PackageNotFoundError:  # 未安装时(例如直接以源码运行)
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
