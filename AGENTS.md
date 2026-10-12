# Repository Guidelines

## 项目结构与模块组织

`code_agent` 是用于学习编程 Agent 原理的 Python 项目。M0、M1 已完成，用户已确认独立靶子验收通过；当前处于 M2a，History 已接入 Agent 并补齐取消时的工具观察，“正常 stop 且无工具调用即结束”的协议已通过完整回归，下一步完成独立靶子非流式验收；`finish_task` 按需增强，流式事件在 M2b 设计。默认只提供 `bash`，不限制单轮工具调用数量。核心由用户手写，Codex 提供规格、接口、测试和评审。安排学习步骤或调整能力边界、里程碑前，先阅读 `PLAN.md` 的当前进度、设计决策、验收标准与协作分工；记忆系统、知识检索、多 Agent 协作与通信属于核心学习主线，章节对应和深化选题也以该计划为准。

- `src/code_agent/`：应用源码；`config.py` 加载配置，`events.py` 定义共享消息与事件模型。
- `llm/`：封装 LiteLLM；`ui/`：纯命令行交互，`cli.py` 为入口，`repl.py`、`render.py`、`prompts.py` 为后续会话、输出与确认模块。
- `core/`、`modes/`、`tools/builtin/`、`context/`、`memory/`、`safety/`、`tracing/`：各能力域的模块骨架。
- `tests/code_agent/`：按源码模块层级组织的单元测试，包括配置、模型层与命令行测试。
- `pyproject.toml`：依赖与工具配置；`uv.lock`：依赖版本锁定。

## 构建、测试与开发命令

在仓库根目录执行命令。项目要求 Python 3.11+，`.python-version` 指定 3.12。

- `uv sync`：安装运行与开发依赖。
- `Copy-Item .env.example .env`：在 PowerShell 中创建本地配置，然后填入 API 密钥。
- `uv run code-agent --version`：验证命令行入口。
- `uv run code-agent -p "你好"`：手动验证 Agent 单次响应，需要模型服务凭据；流式 Agent 路径在 M2b 接入。
- `uv run pytest`：运行测试集。
- `uv run ruff check .`：检查代码规范；`uv run ruff format .`：格式化 Python 文件。
- `uv build`：使用 Hatchling 构建分发包。

## 编码风格与架构约定

使用四空格缩进和类型注解；函数、模块使用 `snake_case`，类使用 `PascalCase`。Ruff 的目标版本为 Python 3.11，行长上限为 100 字符。文档字符串沿用现有模块和函数的写法。

遵循 `PLAN.md` 的依赖边界：业务编排和对象连接集中在 `core/`，UI 调用核心公开入口；能力模块通过共享契约和传入接口协作，不引入循环依赖。`config.py` 可供需要配置的模块使用，同包内部正常复用。模型接入层保持轻薄，工具函数应小而易测。未实现模块注明所属里程碑，接口按实际需求抽出。

## 测试规范

使用 pytest 与 pytest-asyncio，配置为 `asyncio_mode = "auto"`。测试目录 `tests/code_agent/` 镜像 `src/code_agent/`，文件命名为 `test_<模块名>.py`；例如 `src/code_agent/llm/client.py` 对应 `tests/code_agent/llm/test_client.py`。测试函数命名为 `test_*`。通过 `monkeypatch` 隔离环境变量，构造配置时使用 `_env_file=None`，避免本地 `.env` 干扰结果。

行为变更需补充回归测试。计划采用工具层单测、FakeLLM 驱动的循环测试和 CLI 集成测试；真实模型仅做手动验证。目前未配置覆盖率数值门槛。

## 提交与拉取请求规范

提交遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)。

- 标题格式为 `type(scope): 描述`，其中 `scope` 可省略；冒号后保留一个空格。类型使用小写英文，描述使用中文并概括本次变更。
- 新功能使用 `feat`，修复缺陷使用 `fix`；其他变更按用途选择 `docs`、`refactor`、`test`、`build`、`ci`、`style`、`perf` 或 `chore`。这些额外类型是本仓库约定，不是规范强制要求。
- 范围使用模块名称，例如 `cli`、`llm`、`tools`。每次提交围绕一个明确主题；不同主题尽量拆分。里程碑编号放在正文，每个里程碑至少提交一次。
- 正文和页脚可选，分别与前面的标题或正文空一行。正文解释变更原因；页脚可使用 `Refs: #123` 等格式关联问题。
- 破坏性变更在冒号前加 `!`，例如 `feat(cli)!: 修改命令行参数`，或添加 `BREAKING CHANGE: 说明` 页脚；使用 `!` 时，描述应说明破坏性变化。`BREAKING CHANGE` 必须大写。

示例：`chore(cli): 调整命令行交互计划与模块骨架`。

PR 应说明行为变化、关联适用的问题或里程碑，并列出验证结果；命令行交互变更需附输入与输出示例。里程碑实施遵循 `PLAN.md`：完成后展示验收结果，获得用户确认再进入下一阶段。

## 安全与配置

凭据通过环境变量或已忽略的 `.env` 文件管理。使用 `DEEPSEEK_API_KEY` 和 `CODE_AGENT_*` 配置项，禁止提交真实密钥或含凭据的日志。保留 `pyproject.toml` 中的依赖版本排除规则。

手动 smoke 与验收产物放独立目录；测试生成的学习文档和临时文件不纳入提交。最大轮数、执行超时与输出预算保留，可选增强不得自动成为里程碑验收前置条件。
