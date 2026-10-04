# Repository Guidelines

## 项目结构与模块组织

`code_agent` 是用于学习编程 Agent 原理的 Python 项目，目前处于 M0 阶段：已实现配置加载、模型流式调用和命令行入口，多数能力模块仍为占位。设计决策与里程碑验收标准见 `PLAN.md`。

- `src/code_agent/`：应用源码；`config.py` 加载配置，`events.py` 定义共享消息与事件模型。
- `llm/`：封装 LiteLLM；`ui/cli.py`：基于 Typer 的命令行入口。
- `core/`、`modes/`、`tools/builtin/`、`context/`、`memory/`、`safety/`、`tracing/`：各能力域的模块骨架。
- `tests/`：配置与命令行测试。
- `pyproject.toml`：依赖与工具配置；`uv.lock`：依赖版本锁定。

## 构建、测试与开发命令

在仓库根目录执行命令。项目要求 Python 3.11+，`.python-version` 指定 3.12。

- `uv sync`：安装运行与开发依赖。
- `Copy-Item .env.example .env`：在 PowerShell 中创建本地配置，然后填入 API 密钥。
- `uv run code-agent --version`：验证命令行入口。
- `uv run code-agent -p "你好"`：手动验证流式响应，需要模型服务凭据。
- `uv run pytest`：运行测试集。
- `uv run ruff check .`：检查代码规范；`uv run ruff format .`：格式化 Python 文件。
- `uv build`：使用 Hatchling 构建分发包。

## 编码风格与架构约定

使用四空格缩进和类型注解；函数、模块使用 `snake_case`，类使用 `PascalCase`。Ruff 的目标版本为 Python 3.11，行长上限为 100 字符。文档字符串沿用现有模块和函数的写法。

遵循 `PLAN.md` 的依赖边界：各能力包通过 `events.py` 共享契约，跨能力编排集中在 `core/agent.py`。模型接入层保持轻薄，工具函数应小而易测。未实现模块需注明所属里程碑。

## 测试规范

使用 pytest 与 pytest-asyncio，配置为 `asyncio_mode = "auto"`。测试文件命名为 `tests/test_*.py`，测试函数命名为 `test_*`。通过 `monkeypatch` 隔离环境变量，构造配置时使用 `_env_file=None`，避免本地 `.env` 干扰结果。

行为变更需补充回归测试。计划采用工具层单测、FakeLLM 驱动的循环测试和轻量 TUI 冒烟测试；真实模型仅做手动验证。目前未配置覆盖率数值门槛。

## 提交与拉取请求规范

现有提交使用 `chore: ...` 或 `M0: ...` 等里程碑前缀，描述使用中文。提交标题应简洁、范围明确，每个里程碑至少提交一次。

PR 应说明行为变化、关联适用的问题或里程碑，并列出验证结果；可见界面变更需附截图。里程碑实施遵循 `PLAN.md`：完成后展示验收结果，获得用户确认再进入下一阶段。

## 安全与配置

凭据通过环境变量或已忽略的 `.env` 文件管理。使用 `DEEPSEEK_API_KEY` 和 `CODE_AGENT_*` 配置项，禁止提交真实密钥或含凭据的日志。保留 `pyproject.toml` 中的依赖版本排除规则。
