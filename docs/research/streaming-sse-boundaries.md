# 为什么一次网络读取不等于一个流式事件

研究与核查日期：2026-10-13。本文整理学习 Chat Completions 流式响应时提出的问题，范围包括响应体分块、SSE 事件解析、JSON 提取和工具调用组装。以 Python、HTTPX 和 OpenAI 兼容 Chat Completions 为示例；不把这一接口的字段推广到所有模型 API。

本文是学习笔记，示例没有接入应用，也不要求项目改用原始 HTTP。当前项目继续采用 LiteLLM 薄封装，M2b 的核心实现由用户手写；有关提交、执行与取消边界，以 [PLAN.md](../../PLAN.md) 为准。

## 1. 原问题与直接结论

用户原问题：

> 一次网络读取不一定恰好拿到一个事件，这句话是什么意思，详细说明一下，有没有参考文档？

这个问题来自更前面的疑问：流式响应是不是 JSON？不用 LiteLLM 时，应该怎样提取新增文本和方法调用，是否需要正则？

**直接结论：读取数据时得到的块，是传输或客户端缓冲产生的结果；SSE 事件的边界则由事件协议中的空行决定。两个边界没有一一对应的保证。**因此一次读取可以只包含半个事件、多个事件，或者一个完整事件加下一个事件的一部分。

这里的“读取”指未经 SSE 解析的字节块或响应体块。SDK 已解析出的模型 `chunk` 是另一个层次：它可能对应一个完整 JSON 事件，但其中的文本和工具参数仍只是本轮生成的增量。不要把它与 socket 读取或 HTTPX 的字节块混为一谈。[TCP 分段说明](https://www.rfc-editor.org/rfc/rfc9293.html#section-3.7)、[SSE 解释规则](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation)

**术语澄清：本文说的“半个 JSON”只指底层输入尚未收齐时的缓存内容。对于 DeepSeek Chat Completions 的正常响应，一个完整 SSE 事件的 data 是完整的模型 JSON 或独立 `[DONE]`，不能把未经解析的半个网络数据块称为已提取的事件 data。另一个不同情况是：完整外层 JSON 中的 arguments 字符串可以只有部分工具参数，详见第 5 节。**

处理顺序应是：

```text
响应体字节 → 增量解码与切行 → SSE 事件组装 → JSON 解析
    → 提取文本/工具增量 → 组装本轮消息 → 校验与执行
```

使用成熟 SDK 时，前面的几个步骤通常由 SDK 完成；自行请求 HTTP 时，需要明确自己负责哪一层。

## 2. 五种边界要分别理解

### 2.1 TCP 的发送和读取边界

TCP 向应用提供可靠、有序的字节流。假设发送端连续写入两段数据，接收端不一定通过两次读取拿到相同的两段。RFC 9293 §3.7 明确指出，应用的写入缓冲、TCP 分段、接收端读取缓冲的边界不存在必然对应关系。[TCP 规范](https://www.rfc-editor.org/rfc/rfc9293.html#section-3.7)

可以把它想成一条持续流动的字节带：发送端放进去的内容与顺序很重要，但接收端每次取出多少，不能当成业务消息边界。

HTTP/1.1、HTTP/2 的常见部署使用 TCP；不能据此声称所有 HTTP 都基于 TCP。HTTP/3 使用 QUIC，仍应按照 HTTP 与应用协议解析业务边界。[HTTP/2 连接](https://www.rfc-editor.org/rfc/rfc9113.html#section-3)、[HTTP/3 概述](https://www.rfc-editor.org/rfc/rfc9114.html#section-2)

### 2.2 HTTP 消息分帧

HTTP/1.1 的 chunked transfer coding 在传输层面给响应体分块，HTTP/2 使用 DATA 帧。它们解决 HTTP 如何传输消息内容，不能被当成一个 SSE 事件、一个 JSON 或一个工具调用。[HTTP/1.1 chunked 编码](https://www.rfc-editor.org/rfc/rfc9112.html#section-7.1)、[HTTP/2 DATA 帧](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.1)

通过 HTTPX 读取响应体时，HTTP 库已经处理相应的 HTTP 分帧。`aiter_bytes()` 给出响应体字节块，通常还经过内容解码；它不是抓包接口，不暴露原始 TCP 段或完整 HTTP 帧。`aiter_raw()` 中的 raw 也指未经内容解码的响应体，不代表原始网络包。[HTTPX 流式接口](https://www.python-httpx.org/async/#streaming-responses)

### 2.3 SSE 事件边界

SSE 是响应体内部的文本事件协议。一个事件由若干字段行组成，在空行处结束；`data:` 中装什么，由上层应用决定。SSE 可以承载 JSON，也可以承载普通文本。[SSE 标准](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream)

### 2.4 模型 API 的增量对象

Chat Completions 流式协议通常把一个增量 JSON 放进一个 SSE 事件。JSON 中的 `choices[].delta` 提供新增正文或工具调用片段。这是 API 定义的数据结构，与 HTTP/1.1 的 chunked 编码名字相近，但职责完全不同。[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[OpenAI 流式工具调用](https://developers.openai.com/api/docs/guides/function-calling#streaming)

### 2.5 模型 token

模型 token 属于模型输入输出的编码单位。不要给业务代码设定“一块网络数据 = 一个 SSE 事件 = 一个模型 token”的前提。处理 `delta.content` 时，把它当作字符串增量即可；不依赖它恰好对应一个汉字、一个单词或一个 token。这是按 API 增量字段处理得到的实现原则，不是对服务端内部生成批次的推断。

| 名称 | 在哪一层 | 应用需要识别什么 |
| --- | --- | --- |
| TCP 段 / socket 读取 | 传输层与系统调用 | 字节顺序，不把读取次数当消息次数 |
| HTTP chunk / DATA frame | HTTP 分帧 | 交给 HTTP 客户端处理 |
| HTTPX byte block | 客户端迭代接口 | 可能含事件片段或多个事件 |
| SSE event | 响应体中的事件协议 | 字段行、空行、data 数据 |
| Chat Completion chunk | 模型 API | choices、delta、finish_reason |
| token | 模型编码 | 不能用来划定 SSE 或工具执行边界 |

## 3. 四个具体例子

以下示例将 API 数据简化成 `{"content":"A"}`，用于观察边界；这不是完整 Chat Completions schema。

服务器要发送的逻辑内容是：

```text
data: {"content":"A"}

data: {"content":"B"}

```

把换行显式写出来，响应体为：

```python
b'data: {"content":"A"}\n\ndata: {"content":"B"}\n\n'
```

下面是同一响应体可能产生的不同读取切分。它们改变的是读取块，不改变事件内容。

### 3.1 一个事件分几次读取

```python
# 读取 1
b'data: {"cont'

# 读取 2
b'ent":"A"}\n\n'
```

读取 1 后 JSON 不完整，SSE 事件也不完整。此时应该缓存，继续读；不能立刻 `json.loads()`，更不能把解析失败误判为模型输出错误。

### 3.2 一次读取包含多个事件

```python
# 一次读取
b'data: {"content":"A"}\n\ndata: {"content":"B"}\n\n'
```

应派发两个事件，分别解析两段 JSON。整个块既含 `data:` 前缀，又含两个 JSON 文档，不能作为一个 JSON 整体解析。

### 3.3 一个完整事件加半个下一事件

```python
# 读取 1
b'data: {"con'

# 读取 2
b'tent":"A"}\n\ndata: {"content":"B"}\n'

# 读取 3
b'\n'
```

| 收到的数据 | 第一事件 | 第二事件 |
| --- | --- | --- |
| 读取 1 后 | 尚未完整，缓存 | 尚未开始 |
| 读取 2 后 | 已有结束空行，可派发 | JSON 已完整，但事件未结束，继续缓存 |
| 读取 3 后 | 已处理 | 收到结束空行，可派发 |

第二事件在读取 2 后“看起来已经是完整 JSON”，也不能绕过 SSE 事件规则自行派发。若流此时断掉，标准要求丢弃尚未结束的事件。[SSE EOF 行为](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation)

### 3.4 UTF-8 字符本身也可能跨块

汉字“你”的 UTF-8 编码包含三个字节：

```python
"你".encode("utf-8")  # b'\xe4\xbd\xa0'
```

这些字节可能被切成 `b'\xe4'` 和 `b'\xbd\xa0'`。逐块执行独立的 `block.decode("utf-8")` 会遇到不完整字符；使用忽略错误又会丢失原文。需要跨读取保留状态的增量解码器，或者由 HTTP 客户端的文本/行接口处理。[Python 增量解码器](https://docs.python.org/3.12/library/codecs.html#incrementaldecoder-objects)

因此，在 JSON 之前已经有两个需要缓存的环节：字节解码和事件组装。

可以单独验证字符解码状态：

```python
import codecs

payload = "你".encode("utf-8")
# payload[:1].decode("utf-8") 会抛 UnicodeDecodeError。
decoder = codecs.getincrementaldecoder("utf-8")()
parts = [
    decoder.decode(payload[:1]),
    decoder.decode(payload[1:2]),
    decoder.decode(payload[2:], final=True),
]
assert parts == ["", "", "你"]
```

前两次暂不产生文字，第三次收齐后才输出字符；这段实验也已在本仓库环境核验。[Python 3.12 增量编码与解码](https://docs.python.org/3.12/library/codecs.html#incremental-encoding-and-decoding)

## 4. SSE 解析规则及容易漏掉的细节

SSE 的规范来源是 WHATWG HTML Standard，不是某家模型服务文档。下表只整理解析相关的规则，没有复制浏览器 EventSource 的全部连接生命周期。[SSE 语法](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream)、[SSE 解析算法](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation)

| 项目 | 应怎样处理 |
| --- | --- |
| 编码 | UTF-8，忽略流开头的一个 BOM |
| 行结束符 | 接受 LF、CRLF 和单独 CR |
| 空行 | 结束当前事件，按累积的字段派发 |
| `: ping` | 冒号开头是注释，不是模型数据 |
| `data:` | 收集数据；冒号后的一个可选空格被移除 |
| 多个 `data:` | 同一事件内使用换行连接，不直接无分隔拼接 |
| 没有冒号的字段行 | 整行是字段名，值为空；例如 `data` |
| `event` / `id` / `retry` | 有各自标准语义；完整客户端应处理相应行为 |
| 未知字段 | 忽略 |
| EOF 前缺最后空行 | 不派发未完成事件 |

示例：

```text
: keepalive
data: hello
data: world

```

事件的数据是 `"hello\nworld"`。注释不参与数据，两个 data 行之间保留换行。**这种 data 不一定是 JSON**；应用是否执行 `json.loads()`，由上层协议决定。

不要对行执行 `.strip()` 再解析：这可能误删数据中的有效空白。也不要简单对每块数据 `split("\n\n")`：分隔符可能跨块，CRLF/CR 也需要处理。限定只使用 LF 的输入可以设计缓存分隔方案，但不能把它宣称为通用 SSE 解析器。

## 5. JSON 有两层：事件 JSON 与工具参数字符串

### 5.1 第一层：完整事件的数据解析成模型 chunk

拿到完整 SSE 数据后，先识别接口定义的特殊值，再解析 JSON：

```python
if payload == "[DONE]":
    # Chat Completions 约定的结束标记；不是 JSON，也不是 SSE 通用规则
    ...
else:
    chunk = json.loads(payload)
```

普通事件的简化 payload 可以是：

```json
{
  "choices": [
    {
      "index": 0,
      "delta": {
        "content": "我先查看代码",
        "tool_calls": null
      },
      "finish_reason": null
    }
  ]
}
```

解析后 `chunk` 是 Python 字典，正文可以通过：

```python
choices = chunk.get("choices") or []
if choices:
    delta = choices[0].get("delta") or {}
    text = delta.get("content")
```

若使用 LiteLLM/服务商 SDK，通常已经得到解析后的对象，对应写法是 `chunk.choices[0].delta.content`。属性读取与字典读取的差别，来自 SDK 类型包装，不来自模型输出文字本身。

空 `choices` 是 OpenAI 兼容实现或 SDK 中需要防御的情况，例如某些用量分片；不要把它说成所有供应商的统一尾片结构。本文不固定 DeepSeek 用量片的位置与 choices 形状，应按当前服务和 SDK 实际协议确认。

### 5.2 第二层：arguments 仍是需要拼接的 JSON 字符串

一个完整事件的外层 JSON 可以合法，但其中的工具参数片段尚不是合法 JSON。例如：

```json
{
  "choices": [
    {
      "delta": {
        "tool_calls": [
          {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {
              "name": "bash",
              "arguments": "{\"command\":"
            }
          }
        ]
      },
      "finish_reason": null
    }
  ]
}
```

第一次 `json.loads()` 完全可以成功。读取出的 `arguments` 是字符串 `'{"command":'`，它不是对象，也还不是完整参数。

下一事件可能提供：

```json
{
  "choices": [
    {
      "delta": {
        "tool_calls": [
          {
            "index": 0,
            "function": {"arguments": "\"pytest\"}"}
          }
        ]
      },
      "finish_reason": null
    }
  ]
}
```

按调用 index 拼接后得到：

```python
arguments_text = '{"command":' + '"pytest"}'
# '{"command":"pytest"}'
arguments = json.loads(arguments_text)
# {"command": "pytest"}
```

这是两次不同的解析：**外层解析还原一个模型事件，内层解析还原完整工具参数。**流式增量中的参数应先累积，待整轮被确认完整，再交给参数校验边界。[OpenAI 工具分片结构](https://developers.openai.com/api/docs/guides/function-calling#streaming)、[Python JSON 解析](https://docs.python.org/3.12/library/json.html#json.loads)

不能用正则提取 JSON 字段：转义引号、反斜杠、嵌套对象和换行都需要遵循 JSON 语法。即便某个正则通过一个简单示例，也不构成可用的通用解析方案。

## 6. 工具调用怎样按 index 归并

文本追加到本轮正文。工具分片则遍历 `delta.tool_calls`，按每个分片的 `index` 定位缓存。例如两个调用交错到达：

| 到达次序 | 调用 index | arguments 增量 |
| --- | --- | --- |
| 1 | 0 | `{"command":` |
| 2 | 1 | `{"command":` |
| 3 | 0 | `"pwd"}` |
| 4 | 1 | `"pytest"}` |

缓存形状可理解为：

```python
calls_by_index = {
    0: {"id": "call_1", "name": "bash", "arguments": '{"command":"pwd"}'},
    1: {"id": "call_2", "name": "bash", "arguments": '{"command":"pytest"}'},
}
```

这里省略了类型和正式消息封套。`index` 只用于这一轮的分片关联；新一轮重新创建缓存。最终回灌工具结果使用 `tool_call_id` 对应调用 `id`，不使用本轮的 index。[工具增量关联与结果协议](https://developers.openai.com/api/docs/guides/function-calling#streaming)

| 字段 | 组装职责 |
| --- | --- |
| index | 选择调用缓存；0 是有效值，不能用真假判断代替存在检查 |
| id | 保存完整调用标识，后续缺失保留已有值；重复与冲突检查是额外协议校验，本项目组装器当前按用户决定省略 |
| type | 保留和验证调用类型，当前讨论 function |
| name | 按供应商实际契约处理；可能只在首片设置，不应无条件反复拼接 |
| arguments | 把参数字符串增量按到达顺序追加；初值是空字符串，不是 `{}` |

**名称规则需要单独确认。**OpenAI 官方示例通常首片给出 id、name、type，后续只追加 arguments；示例在已有名称缺失时补值。此前对话给出的“名称按片拼接”可以作为某种供应商/归一化适配的契约，但不能推广为全部服务的默认行为。若服务确实把名称作为增量拆开，需要明确支持、测试并在适配层处理；若重复发送完整名称，盲目 `+=` 会把 `bash` 变成 `bashbash`。上述差异不改变 arguments 按增量拼接的原则。[官方字段与 JavaScript 组装示例](https://developers.openai.com/api/docs/guides/function-calling#streaming)

同一个 delta 可以含正文与调用片段，使用两个独立的处理分支，不写成互斥的 `if ... elif ...`。遍历全部工具片段，不只读取第一个调用。

## 7. 三种处理方案

### 方案 A：继续使用 SDK 或 LiteLLM

适合主要学习 Agent 业务编排的项目。SDK 负责 HTTP、响应解码、SSE 与 JSON 包装，核心负责累积正文、工具调用结构校验、历史提交和调用执行。

当前 code_agent 的 M2b 按这个方案推进。本文对底层解析的解释用于理解和实验，不增加更换模型接入库的任务。[项目当前模型边界](../../PLAN.md)

### 方案 B：HTTPX 配合 SSE 库

不使用模型 SDK、但需要直接控制 HTTP 请求时，可用 HTTPX 管理连接，再用 SSE 库获得完整事件。`httpx-sse` 作者维护的 README 提供 `aconnect_sse()`、`aiter_sse()`、`sse.data` 和 JSON 读取示例；它也处理事件的 id/retry 字段，但断线重试策略仍需要调用方明确。[httpx-sse 官方仓库](https://github.com/florimondmanca/httpx-sse)

选库时核对版本、测试与支持的 SSE 行为。事件库不替你决定“可以执行工具了”，也不替你管理 Agent 历史。不要把事件源的自动重连等同于安全地重跑有副作用的模型/工具流程。

### 方案 C：HTTPX aiter_lines 加受限的教学解析

适合观察分层行为。`aiter_lines()` 帮助跨字节块解码并提供行，调用方再缓存 data 字段，直到空行派发事件。[HTTPX 异步流式响应](https://www.python-httpx.org/async/#streaming-responses)、[HTTPX 文本编码配置](https://www.python-httpx.org/advanced/text-encodings/)

下一节示例采用此方案，仅解析 SSE 的 data 数据，不保存 event/id/retry，也没有 EventSource 自动重连生命周期。HTTPX 的行接口是通用文本接口；本实验验证了所列输入，不据此宣称手写 helper 覆盖标准中的全部边角、异常编码与所有服务商变体。实际生产客户端优先使用维护中的 SDK/协议库。

## 8. 可运行的无网络实验

### 8.1 实验目标与运行方式

用 `httpx.AsyncByteStream` 模拟同一 HTTP 响应体以不同块大小到达，验证事件组装结果相同。**它不是抓包，也不是实际 TCP 分段测试**；它直接证明消费者能应对响应体任意切分。

环境：本仓库现有 `.venv`，HTTPX 0.28.1；本次无需安装依赖、请求网络或使用 API 密钥。将下面代码保存到临时目录的 `sse_boundary_demo.py` 后，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe 路径\sse_boundary_demo.py
```

教学临时脚本不纳入提交。示例中的 `example.invalid` 仅用于构造 Request 元数据；代码没有发出 HTTP 请求。

### 8.2 完整代码

```python
import asyncio
from collections.abc import AsyncIterator

import httpx


class Blocks(httpx.AsyncByteStream):
    def __init__(self, blocks: list[bytes]) -> None:
        self.blocks = blocks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for block in self.blocks:
            yield block


async def event_data(response: httpx.Response) -> AsyncIterator[str]:
    """教学用 data 事件解析；没有 EventSource 生命周期或重连功能。"""
    response.raise_for_status()
    media_type = response.headers.get("content-type", "").split(";", 1)[0]
    if media_type.strip().lower() != "text/event-stream":
        raise RuntimeError("预期 text/event-stream 响应")

    # SSE 固定 UTF-8，必须在开始文本读取之前设置。
    response.encoding = "utf-8"
    data_lines: list[str] = []
    first_line = True

    async for line in response.aiter_lines():
        if first_line:
            line = line.removeprefix("\ufeff")
            first_line = False

        if line == "":
            if data_lines:
                data = "\n".join(data_lines)
                data_lines.clear()
                yield data
            continue

        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if field == "data":
            data_lines.append(value.removeprefix(" ") if separator else "")

    # 不在 EOF 时主动派发缓存：缺终止空行的事件不是完整事件。


async def collect(blocks: list[bytes]) -> list[str]:
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        request=httpx.Request("POST", "https://example.invalid/chat/completions"),
        stream=Blocks(blocks),
    )
    try:
        return [data async for data in event_data(response)]
    finally:
        await response.aclose()


def one_byte_blocks(payload: bytes) -> list[bytes]:
    return [payload[i:i + 1] for i in range(len(payload))]


async def main() -> None:
    payload = b'data: {"content":"A"}\n\ndata: {"content":"B"}\n\n'
    expected = ['{"content":"A"}', '{"content":"B"}']
    cases = {
        "merged": [payload],
        "mixed": [payload[:11], payload[11:-1], payload[-1:]],
        "one_byte": one_byte_blocks(payload),
    }

    for name, blocks in cases.items():
        actual = await collect(blocks)
        assert actual == expected, (name, actual)
        print(f"{name}: {len(blocks)} blocks -> {actual}")

    utf8_payload = 'data: {"content":"你"}\r\n\r\n'.encode("utf-8")
    actual = await collect(one_byte_blocks(utf8_payload))
    assert actual == ['{"content":"你"}'], actual
    print(f"utf8_one_byte: {actual}")

    incomplete = await collect([b'data: {"content":"A"}\n'])
    assert incomplete == [], incomplete
    print("incomplete: no event dispatched")

    for ending in ["\n", "\r", "\r\n"]:
        payload2 = (
            f"\ufeff: ping{ending}data: a{ending}data: b{ending}{ending}"
        ).encode("utf-8")
        actual = await collect(one_byte_blocks(payload2))
        assert actual == ["a\nb"], (repr(ending), actual)

    print("BOM/comments/multiline data/LF/CR/CRLF: passed")


if __name__ == "__main__":
    asyncio.run(main())
```

`data_lines` 存在但内容为空时，也应产生空字符串事件，因此代码检查列表是否存在，而不是检查拼接结果是否为真。协议层允许空 data；上层 Chat Completions 解析可再根据服务契约处理它。

### 8.3 本次核验结果

同一 46 字节响应体在以下三种切分下均得到两条相同数据：

```text
merged: 1 blocks -> ['{"content":"A"}', '{"content":"B"}']
mixed: 3 blocks -> ['{"content":"A"}', '{"content":"B"}']
one_byte: 46 blocks -> ['{"content":"A"}', '{"content":"B"}']
utf8_one_byte: ['{"content":"你"}']
incomplete: no event dispatched
BOM/comments/multiline data/LF/CR/CRLF: passed
```

这说明消费者没有依赖“每个输入块恰好是一个事件”。单字节切分同时覆盖分隔符跨块与汉字编码跨块。缺结束空行的输入没有产生事件，符合本实验的 EOF 目标。

未做的验证包括真实服务器、网络延迟、代理缓冲、TLS、真实模型和所有非法编码；这些不影响本次关于读取边界的演示结论，但不能据此声称完成生产网络客户端验收。

## 9. 从教学解析器读取真正的 Chat Completions 字段

拿到完整事件后，可继续使用下面的读取骨架。它展示提取位置，不包含工具组装器或 Agent 接线实现；`event_data()` 来自上一节。

```python
import json


async def read_chat_stream(response):
    seen_done = False
    finish_reason = None

    async for payload in event_data(response):
        if payload == "[DONE]":
            seen_done = True
            break

        chunk = json.loads(payload)
        if "error" in chunk:
            raise RuntimeError(str(chunk["error"]))

        choices = chunk.get("choices") or []
        if not choices:
            continue

        # 教学假设只请求一个候选；多个候选必须按 choice.index 分开处理。
        choice = choices[0]
        delta = choice.get("delta") or {}

        text = delta.get("content")
        if text:
            # 发送给显示层，同时累积本轮正文。
            print(text, end="", flush=True)

        for fragment in delta.get("tool_calls") or []:
            call_index = fragment["index"]
            call_id = fragment.get("id")
            function = fragment.get("function") or {}
            name_piece = function.get("name")
            arguments_piece = function.get("arguments")
            # 按 call_index 更新缓存，保留 call_id/name/arguments。
            # 本阶段不执行工具。

        if choice.get("finish_reason") is not None:
            finish_reason = choice["finish_reason"]

    if not seen_done:
        raise RuntimeError("响应流缺少 [DONE]")
    if finish_reason not in {"stop", "tool_calls"}:
        raise RuntimeError(f"响应未正常完成：{finish_reason!r}")

    # 此后还需由组装器检查整轮结构，才可提交消息与执行工具。
```

这里 `[DONE]` 的检查是**原始 Chat Completions 客户端的协议检查**，不意味着当前 LiteLLM 核心要读取字面字符串。SDK 可能消费该标记并直接结束迭代。另一种 API 也可能使用不同结束事件，不能照搬。

实际请求应通过 `async with client.stream(...)` 管理响应，先验证 HTTP 状态及 Content-Type，再开始数据解释。需要根据服务设置请求/读取超时，区分状态错误、读取中断、取消、SSE 格式错误与 JSON/工具参数错误。不要把返回的错误 HTML 页面送入 JSON 解析器后仅报告“模型 JSON 错误”。[HTTPX stream 与响应关闭](https://www.python-httpx.org/async/#streaming-responses)

## 10. 结束标记、响应完成与任务成功

| 信号 | 它能说明什么 | 它不能单独说明什么 |
| --- | --- | --- |
| 完整 SSE 事件 | 一个事件的数据已到齐 | 模型整轮已经完成 |
| 一个合法 JSON | 此事件数据可以解析 | 工具 arguments 完整/可执行 |
| arguments 已能解析 | 当前参数字符串符合 JSON 语法 | 后续没有增量、工具已经获准执行 |
| finish_reason | 模型这一轮的结束类别 | 传输已正常耗尽、用户任务已验收 |
| `[DONE]` | 该 API 定义的数据流终止标记 | finish_reason 正常、调用结构完整 |
| EOF | 响应体没有更多可读取内容 | 业务正常完成，未收到标记时尤其如此 |
| 工具 completed | 工具执行有已知结果 | 用户目标完成 |
| Agent 正常停止 | 循环按结束协议结束 | 代码正确、测试通过、目标达成 |

`[DONE]` 不是 SSE 标准关键字，而是上层 Chat Completions 的约定。`finish_reason` 则描述模型生成结果；长度耗尽等异常结束不能被当成正常完成的工具调用。具体字段应以服务商接口为准。[DeepSeek 接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)

本文对应的 code_agent 契约是：**流正常结束、允许的结束原因与调用结构检查全部通过，才提交一次完整 assistant 消息，再交给 Registry 校验参数和顺序执行。**正文可以先显示，但临时显示不意味着已经写入可续用历史。[M2b 结束与提交边界](../../PLAN.md)

断流、缺结束片、异常 finish_reason 或组装期取消时，不执行本轮未提交的调用。即使参数恰好已成为合法 JSON，也不能抢先行动。完整响应中的非法参数由 Registry 生成配对错误观察；它属于参数失败，不应被误判为网络仍没收完。

工具已经开始后取消，是另外的生命周期问题：按现有 M2a 契约保留已知观察，并将无法确认结果/尚未启动的调用分别结算为 unknown/not_started，再传播取消。不要自动重放结果未知的副作用命令。

## 11. 常见误区与修正

| 误区 | 为什么不成立 | 修正 |
| --- | --- | --- |
| 每次读取就是一个事件 | 读取边界独立于事件空行 | 缓存并按 SSE 解析 |
| 整个响应体就是一个 JSON | SSE 含字段行、多个事件和特殊标记 | 每个完整 data 分别处理 |
| 每块字节直接 decode | 多字节字符可能跨块 | 增量解码或成熟文本接口 |
| 对原始字节块直接 json.loads | JSON 可能尚未收齐，或包含多个事件 | 先完成事件解析 |
| 用正则取 content | 转义与嵌套需要 JSON 语法 | JSON 解析后按字段读取 |
| split 双 LF 就是通用 SSE | 分隔符跨块及 CRLF/CR 会遗漏 | 遵循行与事件规则 |
| `.strip()` 清洗所有数据 | 会改变有效空白 | 只按协议去掉规定的一个空格 |
| 收到 id/name 就执行工具 | arguments 可能还在生成 | 等整轮正常结束与校验 |
| 参数合法就说明模型结束 | 合法 JSON 不是结束信号 | 检查完成协议与调用结构 |
| 每个字段都 += | id 是标识，name 也可能是完整值 | 按字段及供应商契约更新 |
| DONE 等于用户任务成功 | 只标记该请求的流结束 | 另行验收测试、diff 与任务结果 |
| SDK chunk 就是网络包 | SDK 已完成协议解析 | 说明当前对象属于哪一层 |

## 12. 后续小实验建议

这些是理解问题的可选练习，不增加 M2b 验收前置条件：

1. 把同一 payload 在每个可能位置拆成两块，检查输出事件始终相同。
2. 加入转义引号、反斜杠和正文中的 JSON 换行转义，观察正则和 JSON 解析的区别。
3. 对完整 SSE 事件使用非法 JSON，确认错误属于外层数据解析；再对合法外层 JSON 使用不完整 arguments，确认参数仍应缓存。
4. 构造两个调用交错的增量，分别维护缓存；不要执行真实 Bash，只打印组装结果。
5. 已显示一段文本后模拟断流，检查显示内容、临时缓存与可续用历史三者的区别。
6. 对真正的流式响应观察 HTTP 状态、Content-Type 和事件数，记录供应商字段；不用真实凭据进入学习笔记或日志。

## 13. 参考文档与阅读顺序

以下链接是原始规范、官方文档或库作者维护的说明。规范用于确认协议规则，API 文档用于确认供应商数据结构，库文档用于确认使用方法，本文实验则只证明所列场景。所有资料于 2026-10-13 核查；在线 API/SDK 文档会变化。

| 顺序 | 资料 | 来源级别与用途 |
| --- | --- | --- |
| 1 | [HTTPX Streaming responses](https://www.python-httpx.org/async/#streaming-responses) | 官方库文档；先理解 byte/text/line 迭代及响应关闭 |
| 2 | [WHATWG SSE parsing](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream) 与 [interpretation](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation) | Web 标准；核对 UTF-8、换行、字段、空行和 EOF |
| 3 | [RFC 9293 §3.7](https://www.rfc-editor.org/rfc/rfc9293.html#section-3.7) | IETF TCP 标准；确认写入、分段和读取边界关系 |
| 4 | [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) | 服务商接口文档；确认目标服务的 delta、结束原因及流协议 |
| 5 | [OpenAI function calling streaming](https://developers.openai.com/api/docs/guides/function-calling#streaming) | 服务商官方指南；查看 index 和 arguments 组装示例 |
| 6 | [httpx-sse](https://github.com/florimondmanca/httpx-sse) | 库作者 README 与源码入口；对照现成事件消费方案 |
| 7 | [HTTPX text encodings](https://www.python-httpx.org/advanced/text-encodings/) | 官方库文档；理解显式 encoding 与解码层 |
| 8 | [Python json](https://docs.python.org/3.12/library/json.html#json.loads) 与 [incremental codecs](https://docs.python.org/3.12/library/codecs.html#incrementaldecoder-objects) | Python 官方文档；理解 JSON 解析和跨块字符处理 |
| 9 | [RFC 9112 §7.1](https://www.rfc-editor.org/rfc/rfc9112.html#section-7.1)、[RFC 9113 §6.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.1)、[RFC 9114 §2](https://www.rfc-editor.org/rfc/rfc9114.html#section-2) | IETF HTTP 标准；深入对照 HTTP 分帧和 HTTP/3 传输 |

来源限制：本次 DeepSeek 官方页面通过研究工具再次打开时出现内部错误；关于 Chat 流的 SSE/[DONE] 说明沿用本对话此前读取的官方接口内容，没有声称在本次重新取得最新完整 schema。尤其没有把某一种 usage 尾片结构推广到所有供应商。有关实现细节仍应结合当前服务实际样本核对。

项目侧的文本与工具分片组装已实现并提交（139ad20），下一步由用户将 `TurnAssembler` 接入 Agent 流式循环与终端事件，按 M2b 验证执行和取消边界。当前组装器采用完整名称与增量参数的契约，不额外检查调用 ID 重复或冲突；已有 History 配对约束保留。

## 14. 真实 DeepSeek 流式请求样本（2026-10-13）

为避免只凭示意图理解结束协议，本次直接通过 HTTPX 向官方 `https://api.deepseek.com/chat/completions` 发出一轮真实请求，没有经过 LiteLLM 的事件包装。问题是“1+1等于几？请只回复一个数字。”，请求设置 `stream=true`、`max_tokens=128`，没有工具定义或工具执行。

请求使用项目现有凭据，样本不保存 Authorization 请求头、API 密钥或完整环境配置。手动 smoke 产物保存在本机已忽略目录 `artifacts/deepseek-stream-capture/20261013-163056-610664/`。这些产物不随 Git 提交，也不是外部协议规范；本节保留可提交的实测摘要，新的仓库副本不依赖本机文件。

| 本机文件 | 内容 |
| --- | --- |
| `sample.md` | 样本说明与完整原始响应 |
| `request.json` | 请求参数，不含密钥 |
| `response.sse` | 原始 SSE 响应体 |
| `events.json` | 解析后的全部事件 |
| `read-boundaries.json` | 响应体读取块边界 |
| `metadata.json` | 状态码与采集时间 |

### 14.1 实际请求结果

| 项目 | 本次观察 |
| --- | --- |
| 采集开始时间 | 2026-10-13 16:30:56，Asia/Shanghai |
| HTTP 状态 | 200 |
| Content-Type | `text/event-stream; charset=utf-8` |
| 请求 model | `deepseek-chat` |
| 响应 model | `deepseek-flash`；仅记录本次返回值，不推断永久映射规则 |
| 正文 | `2` |
| HTTPX 响应体块数 | 3 |
| SSE 事件数 | 4，其中 3 个 JSON 事件和 1 个 `[DONE]` |
| 响应体大小 | 1,023 字节 |
| 用量 | prompt=16、completion=1、total=17 |

### 14.2 四个事件的顺序

以下是从实测 JSON 中提取的关键字段视图，省略了响应 ID、时间戳等公共字段；完整未裁剪响应见 `response.sse`：

```text
事件 1：delta={"role":"assistant","content":""}, finish_reason=null
事件 2：delta={"content":"2"}, finish_reason=null
事件 3：delta={"content":""}, finish_reason="stop", usage={...}
事件 4：data=[DONE]
```

**最后一个 SSE 的 data 是 `[DONE]`，不是 `finish_reason`。**结束原因是前一个 JSON 事件中 `choices[0].finish_reason` 的值。它们分别属于传输结束约定与模型结束原因，不能互相替代。

本次 usage 与结束原因在同一 JSON 事件中，该事件的 choices 非空。其他供应商、配置或 SDK 可能采用不同用量片结构，仍应按实际字段读取，不能把本次四片顺序固定为所有请求的唯一形态。

### 14.3 本次读取边界也没有与事件一一对应

HTTPX 三次迭代产生的响应体块大小为 291、273、459 字节。前两块各包含一个事件，第三块同时包含“结束原因与 usage”事件和 `[DONE]` 事件。因此实际观察也是 **3 个响应体块对应 4 个 SSE 事件**。

这是 HTTPX 提供的响应体块边界，不是 socket read 次数、TCP 段数或网络抓包结果。原始响应体文件保存 HTTP 内容解码后的 SSE 字节，不含 HTTP 头、TLS 数据或 TCP 包。

### 14.4 样本能证明与不能证明的内容

这次实测确认了目标接口返回 SSE、文本读取位置、正常 stop 的字段位置、独立 `[DONE]` 与读取块/事件数量不一致。所有事件的顺序和样本大小都来自实际返回，不是手写模拟。

本次没有工具调用，因此不能据此确认工具名称是否拆片、多调用交错方式、工具参数分片或错误/取消行为；这些仍需要工具样本和确定性测试分别验证。样本属于 M2b 学习证据，不等于流式 Agent 路径已经接线或验收。
