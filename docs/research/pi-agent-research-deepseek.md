# Pi Coding Agent 架构研究笔记

> 研究目的：为 `code_agent` 项目的后续演进提供参考，重点覆盖 Agent 循环、记忆与上下文、工具与执行模式、权限门控、多 Agent 协同、评测与可观测性六个维度。
> 适用场景：当项目遇到"权限怎么加""上下文怎么压""多 Agent 怎么接""怎么评测"等问题时，回到本文对照参考实现与迁移建议。
> 说明：本文区分 **Pi 核心**（`badlogic/pi-mono` monorepo 内的包）与 **Pi 生态扩展**（社区/官方扩展，如 gatekeeper、router、harness）。扩展部分的细节以各仓库 README 为准，本文给出的是设计模式而非逐行实现。

---

## 目录

1. 总体定位与架构
2. Agent 循环与工具执行
3. 记忆与上下文管理
4. 权限门控（重点）
5. 多 Agent 协同
6. 评测与可观测性
7. 与 code_agent 的映射与补充清单
8. 参考来源与信息可靠性

---

## 1. 总体定位与架构

### 1.1 设计哲学

Pi 不是一个"巨型 CLI"，而是一个**可组合的 Agent 底座**。它的核心取舍是：

- **内核极简**：默认只给模型四个工具——`read`、`write`、`edit`、`bash`。系统提示词加工具定义不到 1000 tokens。
- **能力外置**：安全、沙箱、审批、子代理、评测等能力由**扩展层**承载，而不是塞进主循环。
- **协议统一**：`pi-ai` 统一多供应商的流式协议，上层不感知供应商差异。

这与 `code_agent` 当前"极简单循环、默认只暴露 `bash`"的定位高度一致。Pi 值得借鉴的不是它的规模，而是**"内核保持薄、复杂性用扩展隔离"** 的分层纪律。

### 1.2 Monorepo 分层

| 包 | 职责 |
|---|---|
| `pi-ai` | 统一多供应商 LLM 协议（流式、工具调用、用量统计） |
| `pi-agent-core` | 不依赖终端的 Agent Loop、Harness、工具调度 |
| `pi-coding-agent` | 组合层：会话、压缩、资源、信任、工具、扩展、运行模式 |
| `pi-tui` | 差分终端渲染框架 |
| `pi-telemetry` | 可观测性 |
| `pi-protocol` / `pi-client` / `pi-server` | 远程契约与远程状态管理 |

**迁移启示**：`code_agent` 目前是单包结构。如果未来要拆分，建议按"协议 / 循环内核 / 组合层 / 界面"四层切分，而不是按功能模块切分。切分点应落在"是否依赖终端"和"是否依赖具体供应商"这两条边界上。

---

## 2. Agent 循环与工具执行

### 2.1 双层 Loop 结构

Pi 的 Agent Loop 处理：流式生成、工具调用、**Steering**（运行中插入用户指令）、**Follow-up**（追加消息）、取消、终止。

关键结构是 **"准备阶段串行、执行阶段并行"**：

```typescript
// 示意：agent-loop.ts 中的并行工具执行
const runningCalls = runnableCalls.map(prepared => ({
  prepared,
  execution: executePreparedToolCall(prepared, signal, emit),
}));

for (const running of runningCalls) {
  const executed = await running.execution;   // 按源顺序收集结果
  results.push(finalize(running.prepared, executed));
}
```

- **准备串行**：校验参数、执行 `beforeToolCall` hooks、做权限裁决。
- **执行并行**：多个工具调用同时进行，但结果按**源顺序**写回历史，保证上下文顺序确定。

**迁移建议**：`code_agent` 当前串行执行 `bash`，在单工具场景下完全够用。一旦引入多个只读工具（`grep`、`read`、`glob`），**并行执行是成本最低的吞吐提升**，且不需要改动主循环的决策逻辑——只在"工具调度"这一层做并发，结果按调用顺序收集即可。

### 2.2 可插拔 Operations 接口

每个工具暴露一个 `*Operations` 接口，把**工具逻辑**（参数解析、输出截断、格式化）与 **I/O 基底**（本地 FS、SSH、沙箱 API）分离：

```typescript
interface BashOperations {
  exec(
    command: string,
    cwd: string,
    opts: { onData; signal; timeout; env }
  ): Promise<{ exitCode: number }>;
}

interface ReadOperations {
  readFile(absolutePath: string): Promise<Buffer>;
  access(absolutePath: string): Promise<void>;
}
```

沙箱扩展正是通过**替换 `BashOperations` 实现**来工作的——把命令包裹进 OS 级沙箱，而 `bash` 工具本身完全不知道沙箱的存在。

**迁移建议**：这是 `code_agent` 权限/沙箱演进最有价值的一个模式。不要在 `bash.py` 里写 `if sandbox: ...`，而是定义协议与两个实现：

```python
class BashOperations(Protocol):
    def exec(self, command: str, cwd: str, opts: ExecOptions) -> ExecResult: ...

class LocalBashOperations:   # 直接 subprocess
    ...

class SandboxBashOperations: # 包装 landlock / sandbox-exec
    ...
```

权限层只负责**选择哪个 Operations 实现**，工具层对沙箱无感知。

### 2.3 Steering 与 Follow-up

Pi 支持在 Agent 运行过程中插入用户指令（Steering），以及在一次运行结束后追加消息（Follow-up）。实现上，Steering 消息在**下一次模型请求前**被注入到上下文，而不是打断当前流式生成。

**迁移建议**：`code_agent` 当前是"一问一答"的同步循环。Steering 的实用价值在于**长任务中途纠偏**（"别改那个文件，先跑测试"）。一个轻量实现：在每次工具结果写回后检查一个 `steering_queue`，非空则把消息追加到历史再进入下一轮。改动面小，但显著提升交互体验。

---

## 3. 记忆与上下文管理

### 3.1 会话树与分支

Pi 将会话存储为**可分支的 JSONL**。每条条目有唯一 id，分支通过引用父 id 形成树。上下文从**活动分支**重建。

关键点：**压缩不是删除旧消息，而是插入一个摘要条目**，在后续模型请求中替换较旧消息，但**原始条目仍保留在会话树中**。这意味着回放和审计永远能拿到完整原始轨迹，而模型只看到压缩后的视图。

### 3.2 自动压缩机制

触发条件：

```text
contextTokens > contextWindow - reserveTokens
```

默认 `reserveTokens = 16384`。触发时机在**工具结果追加之后、下一次助手响应之前**的 `prepareNextTurn` 中检查；此外在新用户提示前、底层 run 结束后也有检查点。

压缩算法核心逻辑（简化）：

```typescript
// 1. 从后往前累积 token，直到 keepRecentTokens（默认 20k）被满足，
//    找到 cut point（firstKeptEntryId）
// 2. 收集 cut point 之前的消息，调用 LLM 生成结构化摘要
// 3. 追加 CompactionEntry { summary, firstKeptEntryId }
// 4. 下次请求上下文 = summary + firstKeptEntryId 之后的消息
```

压缩前后上下文的重建关系：

```text
原始历史:  [m1] [m2] [m3] [m4] [m5] [m6] [m7] [m8]
压缩后:    [summary(m1..m4)] [m5] [m6] [m7] [m8]
                ↑ 模型看到的视图
会话树:    仍保留 m1..m4 的原始条目（可回放）
```

### 3.3 无损折叠与透明压缩（生态探索）

两个值得关注的方向：

- **pi-fold**：做"Agent 治理的无损上下文折叠"——让原始记录离开模型视野，但**不以有损摘要替代**，会话从折叠后的视图重启。
- **transparent-compact**：确定性方法，目标是**最大化 Agent 对自身压缩上下文的可见性**——让 Agent 知道"哪些是原始消息、哪些是摘要"。

**迁移建议**：压缩不只是"省 token"，更是**上下文状态的可解释性**问题。Agent 若不知道自己看到的是摘要，容易基于摘要做出错误假设。`code_agent` 的第一步可以先做**工具输出预算的事后压缩**：超长工具结果在写入历史前替换为"摘要 + 原文临时文件路径"。这与 Pi 的 `firstKeptEntryId` 思路同构，但改动面极小，且保留可追溯性。

---

## 4. 权限门控（重点）

> 本节直接对应 `code_agent` 当前最大的工程缺口：`bash` 工具无审批门控。

### 4.1 核心结论：策略层不是安全边界

业界共识是**分层防御（Defense-in-Depth）**，每一层只做它能可靠完成的事：

| 层级 | 机制 | 能解决 | 局限 |
|---|---|---|---|
| L1 策略层 | 正则/通配符 allow/deny | 高频安全命令免打扰、显式危险模式快速拦截 | **可被结构绕过**（`cat * > xxx`） |
| L2 结构层 | **AST 解析**（tree-sitter-bash） | 识别重定向、管道、命令替换等真实语义 | 解析失败需 fail-closed |
| L3 沙箱层 | OS 级隔离（Seatbelt/Landlock/容器） | **无论命令怎么写**，访问被内核限制 | 配置成本、跨平台差异 |
| L4 审批层 | 人工确认 / 自动模式 | 对未覆盖的边界情况最终裁决 | 审批疲劳，不能作唯一防线 |

**关键认知**：正则匹配适合做"快速预筛"，但不能作为白名单的最终裁决依据。`cat * > ~/.ssh/authorized_keys` 之所以危险，是因为它在**结构层面**已经不是读命令了。

### 4.2 pi-gatekeeper：默认拒绝 + AST 分类

`@eigenwert/pi-gatekeeper` 采用 **default-deny** 模型：`edit`/`write` 始终门控；`bash` 命令用 **tree-sitter-bash** 解析为 AST，然后逐命令检查安全 allowlist。

它的分类规则覆盖了所有结构性漏洞：

- **输出重定向**（`>`、`>>`）在 AST 中检测 redirect 节点，**不是字符串搜索**。
- **动态/不可解析构造**（命令替换、变量展开、命令位置的 ANSI-C 字符串）**始终门控**。
- **良性包装器解包**：`env`、`nice`、`nohup`、`timeout` 被剥掉，检查内部真实命令。
- **复合命令逐段检查**：`&&`、`||`、`;`、管道、循环、条件的每个子命令独立裁决。
- **解析错误触发门控**，fail-closed。

设计原则原文可概括为：

> 如果分析器无法证明命令是安全的，就门控。误报（不必要的提示）烦人但无害；漏报（遗漏的变更）是危险的。

allowlist 的保守程度值得直接参考：

```text
find      → 不允许 -delete / -exec
sed       → 不允许 -i
sort      → 不允许 -o
curl      → 不允许 -o / -O
解释器    → 只允许 --version 检查
重定向    → 只允许 /dev/null、2>&1、输入重定向
```

### 4.3 其他门控实现的关键变体

- **pi-permission-suite**：四种审批模式，tree-sitter 解析链式命令，有独立的 `bash-parser.ts`。
- **qings-pi-modes**：**CC 式五模式 + 三态边界沙箱 + 独立网络轴 + AI 审批模式**。Tier 1 白名单刻意保守：`find -execdir`、`sort -o`、`uniq 输入 输出`、`git --output=` 等**隐藏写操作一律不合格**；`sed` 整体不在白名单（因为 `e` 命令和 `s///e` 会执行 shell）。
- **pi-ast-guard**：DNA（Do Not Ask）模式定义 **工作区内/外 × 读/写** 四象限自动审批策略；`parseFailure` 默认 `block`；`maxViolations` 默认 3 后强制中断会话。
- **pi-access-guard**：在 bash 执行前**过滤环境变量**，只保留 `envPassthrough` allowlist。环境变量泄漏本身是敏感数据外泄通道，这个维度容易被忽略。

### 4.4 建议的 AST 门控骨架（Python 示意）

```python
import bashlex  # 或 tree-sitter-bash

READONLY_COMMANDS = {"cat", "head", "tail", "grep", "ls", "wc", "find", "git"}
SENSITIVE_PREFIXES = ("~/.ssh/", "~/.aws/", "/etc/", "/root/")

def analyze(command: str, workspace_root: str) -> str:
    """返回 'allow' | 'ask' | 'deny'。fail-closed。"""
    try:
        parts = bashlex.parse(command)
    except bashlex.errors.ParsingError:
        return "deny"           # 解析失败 = 门控

    if len(parts) != 1:
        return "deny"           # 多个顶层语句，过于复杂

    node = parts[0]
    if node.kind != "command":
        return "ask"            # Pipeline / CommandList 等，逐段递归处理

    # 1. 重定向：任何写重定向都不能静默放行
    redirects = [w for w in node.parts if w.kind == "redirect"]
    for r in redirects:
        target = r.output
        if target == "/dev/null":
            continue
        if is_sensitive(target) or not is_within(target, workspace_root):
            return "deny"
        return "ask"            # 有写重定向但非敏感路径 → 询问

    # 2. 命令名 + 动态构造
    cmd = node.parts[0].word if node.parts else ""
    if cmd not in READONLY_COMMANDS:
        return "ask"
    if contains_dynamic_expansion(node):   # $() / 反引号 / 未展开变量
        return "ask"
    return "allow"
```

核心顺序是：**先看结构（重定向、动态构造），再看命令名**。`cat * > ~/.ssh/authorized_keys` 会在第 1 步被捕获，连命令名检查的机会都没有。

### 4.5 复合命令的轻量拆解

即使不做完整 AST，也**必须按 `&&`、`||`、`;`、`|` 拆分**，对每段独立裁决：任一段 `deny` 则整体拒绝，任一段 `ask` 则整体询问。`ls && rm -rf /` 若整串匹配 `ls *` 白名单，会把后面的危险命令一起放行——这是已知的链式绕过漏洞。

### 4.6 审批交互的三种出口

`ask` 触发后，给用户三个选项：

```text
即将执行：git push --force origin main
[o] 仅本次允许   [a] 本会话允许此类操作   [r] 拒绝
```

- `o`：本次执行，不留记忆。
- `a`：将当前 `(command_prefix, pattern)` 写入会话级 `approved` 集合，后续同类免问。
- `r`：向模型返回结构化拒绝，例如 `{"error": "user_denied", "reason": "..."}`，让模型在下一轮调整策略，而不是让循环崩溃。

### 4.7 明确的非目标

- **不调用 LLM 做安全判断**：审批必须是确定性的。模型判断只作为未匹配时的 fallback。
- **审批不是沙箱**：审批是"人机边界"，沙箱是"系统边界"。沙箱可作为后续独立议题。
- **不覆盖所有 shell 语法**：`heredoc`、复杂子 shell 在拆解失败时**默认 `ask`**，宁可多问一次。

---

## 5. 多 Agent 协同

### 5.1 核心取舍：进程隔离 + 有界结果

Pi 核心**不内置 sub-agent**，参考扩展通过 **spawn `pi --mode json -p --no-session` 子进程**实现真正的进程级上下文隔离。子代理的会话通过 JSON 事件流捕获，父代理只接收**有界的最终结果**，而不是完整 transcript、会话上下文和工具历史。

`pi-agent-router` 的设计明确：`contextFrom` 用于从之前的委派会话交接**仅限有界的最终响应 / 验证过的 `submit_result` payload**，完整转录不会被注入。

### 5.2 三种编排模式

| 模式 | 行为 |
|---|---|
| `single` | 委派给单个子代理 |
| `parallel` | 并发委派，可配置上限（默认 4，范围 1–16） |
| `chain` | 严格顺序，每步接收前一步上下文 |

任务使用 `id` 作为稳定逻辑引用，支持 `retry` 和 `retryFrom` 恢复到之前的委派工作。

### 5.3 通信的务实取舍

Pi 的 Agent 通信是**约定式而非协议式**的：子进程 JSON 事件流、`{previous}` 文本链式传递、会话交接、扩展间事件总线 `pi.events`，但**没有类型化的 A2A 协议**。

这是一个值得参考的取舍：多 Agent 早期阶段，**文本/JSON 结果传递 + 进程隔离**已经足够，不必过早引入复杂通信协议。

**迁移建议**：`code_agent` 可以把"委派"实现为 `Registry` 中的一个**特殊工具**，子 Agent 的 `RunResult` 作为工具结果写回父历史。这样不侵入当前单循环内核，就能逐步长出多 Agent 能力。

---

## 6. 评测与可观测性

### 6.1 分层：运行与观察分离

Pi 核心提供会话存储和压缩，但**评测、回放、诊断、回归基线由运维层（如 Pi-Harness）承载**。

Pi-Harness 在原生 Pi 之上增加的能力：

```text
Runs         → 运行记录
Trace        → 完整执行轨迹
Replay       → 会话回放
Run Compare  → 运行对比
Evaluation   → 评测
Regression   → 回归基线
Diagnostics  → 诊断
Artifacts    → 产物管理
```

设计哲学：**原生 Pi 负责运行，Harness 负责观察、控制、恢复和评估**。

### 6.2 对 code_agent 的直接启示

**轨迹持久化不需要侵入 Agent 循环**，应作为旁路观察者。一个最小化的 JSONL 轨迹记录器即可：

```python
# 旁路记录，不改变控制流
def record(event: dict, path: Path):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

# 事件类型示例
# {"type": "user",        "content": "..."}
# {"type": "llm_request", "messages": [...]}
# {"type": "tool_call",   "name": "bash", "args": {...}}
# {"type": "tool_result", "output": "...", "exit_code": 0}
# {"type": "run_result",  "status": "completed", "steps": 5}
```

这份轨迹的直接回报：可以用真实轨迹做回归测试，也能在面试中展示"我如何定位一次失败的工具调用"。

### 6.3 已知的可靠性教训

社区对 Pi 的可靠性分析指出了几个可迁移的教训：

- **可重试性依赖对供应商错误字符串的正则匹配**——脆弱。
- **溢出恢复是一次性的，压缩是有损的**。

**迁移建议**：`code_agent` 在设计上下文压缩和错误恢复时，应避免把"重试"建立在字符串匹配上，而是用**结构化错误分类 + 有限的恢复预算**。

---

## 7. 与 code_agent 的映射与补充清单

### 7.1 当前缺口对照

| `code_agent` 现状 | Pi 参考 | 缺口性质 |
|---|---|---|
| `bash` 无审批门控 | pi-gatekeeper / qings-pi-modes | 安全底线缺失 |
| 历史仅内存，无持久化 | Pi-Harness Runs/Trace | 无法回放与回归测试 |
| 无上下文压缩 | `CompactionEntry` + `firstKeptEntryId` | 长会话瓶颈 |
| 工具串行执行 | `executeToolCallsParallel` | 多工具时吞吐受限 |
| `bash` I/O 硬编码 | `BashOperations` 接口 | 沙箱难以接入 |
| 单 Agent | subagent 扩展 | 远期能力 |

### 7.2 优先级与改动量

| 优先级 | 补充项 | 参考实现 | 改动量 |
|---|---|---|---|
| **P0** | AST 门控：检测重定向、动态构造、解包包装器、复合命令逐段裁决，fail-closed | pi-gatekeeper、qings-pi-modes | 新文件 150–200 行 |
| **P0** | 轨迹记录器：旁路 JSONL，记录完整运行证据 | Pi-Harness Runs/Trace | 新文件 50–80 行 |
| **P1** | 可插拔工具 Operations：`bash` 的 I/O 与工具逻辑分离 | `BashOperations` | 重构 `bash` handler |
| **P1** | 工具输出事后压缩：超长结果写历史前替换为摘要 + 原文引用 | `CompactionEntry` | 修改 `history` 写入路径 |
| **P2** | 并行工具执行：准备串行、执行并发、结果按序 | `executeToolCallsParallel` | 多工具场景再引入 |
| **P2** | 子代理作为工具：进程隔离 + 有界结果回传 | subagent / pi-agent-router | 单循环稳定后推进 |
| **P3** | OS 级沙箱：Landlock / Seatbelt 包装 | 各门控扩展的沙箱轴 | 独立议题 |

### 7.3 建议的落地顺序

1. **先做 P0 两项**：AST 门控 + 轨迹记录器。前者建立安全底线，后者建立调试闭环。两者互不依赖，可并行。
2. **再做 P1 两项**：Operations 接口抽象为沙箱铺路；输出压缩解决长会话瓶颈。
3. **P2 视需求推进**：多工具出现时上并行；需要任务分解时上子代理。
4. **P3 最后**：沙箱是"策略层失效时的硬边界"，在策略层稳定后再引入。

### 7.4 面试叙事模板

> "我最初用正则做白名单，后来意识到 `cat * > ~/.ssh/authorized_keys` 这类结构绕过，于是将决策从字符串层下沉到 AST 层。参考了 Pi 生态中 gatekeeper 的 fail-closed 分类和 Codex 的沙箱纵深设计，在保持极简内核的前提下，用 AST 裁决堵住了结构漏洞，并用旁路 JSONL 轨迹建立了可回放的调试闭环。OS 沙箱作为策略层失效时的硬边界预留。"

这个叙事展示的是对**策略层可靠性边界**的理解，比罗列规则更有说服力。

---

## 8. 参考来源与信息可靠性

### 8.1 来源

| 主题 | 来源 |
|---|---|
| Pi monorepo 分层 | `badlogic/pi-mono` README 与包结构 |
| Agent Loop / 并行执行 | `pi-agent-core` 的 `agent-loop.ts` 设计说明 |
| 压缩机制 | Pi 文档中 compaction 章节（`reserveTokens`、`keepRecentTokens`、`firstKeptEntryId`） |
| AST 门控 | `@eigenwert/pi-gatekeeper`、`pi-permission-suite`、`qings-pi-modes`、`pi-ast-guard` README |
| 子代理 | subagent 扩展、`pi-agent-router` README |
| 评测 | Pi-Harness README |

### 8.2 可靠性说明

- **Pi 核心**的结构（monorepo 分层、四个默认工具、会话树、压缩参数）是相对确定的。
- **生态扩展**（gatekeeper、router、harness 等）的具体规则细节以其仓库当前版本为准，本文给出的是**设计模式**而非逐行实现。在引用具体规则（如"`sed` 整体不在白名单"）前，建议回到对应仓库核对。
- 本文的**迁移建议与代码示意**是为 `code_agent` 项目做的适配，不是 Pi 的原始实现。`bashlex` 与 `tree-sitter-bash` 的选择需按项目依赖策略决定：前者纯 Python、零构建依赖；后者解析质量更高、需原生构建。

### 8.3 相关安全背景

- `cat * > file` 类结构绕过是字符串检查式 shell 防护的已知缺陷类别。
- 链式命令（`&&`、`;`、`|`）绕过 allowlist 是有公开案例的漏洞类型。
- 业界共识：**在原始命令字符串上做模式匹配，本质上是可绕过的**；AST 解析 + fail-closed + OS 沙箱是纵深防御的标准组合。

---

*文档版本：v1.0 ｜ 建议在 `code_agent` 每次做权限/上下文/多 Agent 相关决策前回顾对应章节。*