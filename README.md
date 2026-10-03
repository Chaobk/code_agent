# code_agent

一个用于**学习 Agent 原理**的编程 Agent。目标不是做成产品,而是把 agent 的每一层都亲手实现一遍,
从而搞清楚"模型 → 工具 → 观察 → 再决策"这条循环究竟是怎么跑起来的。

完整的设计决策与里程碑见 [PLAN.md](PLAN.md)。

## 当前进度

M0:项目骨架与模型连通性。

## 快速开始

```bash
uv sync
cp .env.example .env   # 填入 DEEPSEEK_API_KEY
uv run code-agent --version
uv run code-agent -p "你好"
```

## 目录结构

按能力域分包,`core/` 只做编排,各能力子包之间互不 import(依赖方向见 `PLAN.md` §4)。

```
src/code_agent/
├── config.py       # J 配置
├── events.py       # 全系统共享的事件与消息模型
├── core/           # 编排器(唯一把各能力串起来的地方)
├── llm/            # A 模型层(litellm)
├── modes/          # E 模式:react / plan
├── tools/          # D 工具系统
├── context/        # B 上下文管理
├── memory/         # C 记忆管理
├── safety/         # G 安全与权限
├── tracing/        # I 轨迹与评测
└── ui/             # H 交互层(cli / tui)
```
