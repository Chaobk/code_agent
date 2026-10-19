# Jev 本地部署调研

核对日期：2026-10-16。以下是公开一手资料核对，未在本机下载模型或实测性能。

## TypeSafe 官方 Jev 的开放状态

官方快速开始提供 SDK、HTTP API、Agent Skill 和 Playground，示例调用 `https://api.typesafe.ai/v1/systemone`。本次查阅官方文档索引及快速开始，**未找到可下载权重、官方本地推理包或本地部署硬件要求**；不能据此断言不存在其他商务部署方式。SDK 安装到本机不代表模型在本机运行。[官方快速开始](https://docs.typesafe.ai/introduction/quickstart)、[官方文档索引](https://docs.typesafe.ai/llms.txt)

## 独立替代项目：feder-cr/jev / jevos-v4

该项目自称 Jev 的开源替代方案，不能视为 TypeSafe 官方模型。它提供 `noul` 是非判断概率、`choice` 选择和 `score` 评分，不生成聊天回复。README 标注 `score` 仍属早期能力。[项目 README](https://github.com/feder-cr/jev)

| 项目 | 作者公开资料 |
| --- | --- |
| 推理路径 | OpenVINO INT8，CPU；无需独立显卡或 Python |
| 平台 | Windows x64、Linux x64（glibc 2.35+）、Apple silicon macOS 有预编译包 |
| 内存 | README 约 1 GB，文本缓存满约 1.4 GB；这是应用占用，不是整机最低 RAM |
| 上下文 | 每题最多 8,192 tokens，包含文本和问题 |
| 下载 | 平台运行包加 `jevos-v4-openvino-int8.zip`；另有 GGUF Q4_K_M、Q8_0 |
| 架构 | v4 发布说明称沿用 17 层模型；本次未核实可靠的精确参数总数，不应从文件大小直接倒推 |

来源：[README](https://github.com/feder-cr/jev)、[v4 发布说明及下载](https://github.com/feder-cr/jev/releases/tag/jevos-v4)、[作者 CPU 部署指南](https://github.com/feder-cr/jev/wiki/run-an-llm-locally-without-a-gpu)。

作者性能数字约短输入 28 ms、191-token 输入 130 ms，测于 Intel Core Ultra 7 255H 笔记本、16 线程；README 说明是 10 次请求中位数，输入文本每次重新读取。这不是任意 CPU 的速度保证。作者未测旧款/低功耗 x64、服务器 CPU 或 ARM 开发板，多客户端吞吐也未测。没有 Linux ARM64 发布包，不能直接据此推荐树莓派。[基准方法](https://github.com/feder-cr/jev#benchmarks)、[作者明确列出的测试边界](https://github.com/feder-cr/jev/wiki/edge-ai-decisions-on-a-cpu)

v4 发布说明报告：821 道新是非题准确率 87.0%，999 道手写题 78.9%，后者低于 v3 的 80.8%。这些是作者评测，不是独立保证；README 还披露六组主要比较集中的五组参与 checkpoint 选择。因此应以实际中文路由/记忆判断样例复测。[v4 发布说明](https://github.com/feder-cr/jev/releases/tag/jevos-v4)、[基准披露](https://github.com/feder-cr/jev#benchmarks)

## 其他本地实现及硬件选择

下面将项目公开数字与部署建议分开；建议仅针对单用户、短输入试验，不是作者承诺的最低配置。

| 实现 | 公开资源数据 | 建议预留配置（估算） |
| --- | --- | --- |
| jevos-v4 / OpenVINO INT8 | 进程约 1–1.4 GB RAM | 现代四核 CPU、整机 8 GB RAM 起，无需独显；16 GB 更方便同时开发 |
| JevEmbed-Qwen3-Embedding-0.6B | 0.6B 规模；默认配置设备和 dtype 为 auto，输入截断至 1,024 tokens | 整机 8 GB RAM 起；可先试 CPU，GPU 可从 4 GB 显存尝试。按 0.6B 参数估算，纯权重 FP32 约 2.4 GB、FP16 约 1.2 GB，实际还要加运行开销；不是实测值 |
| amithgc/local-jev + Qwen3.5-4B | 作者报告下载 9.32 GB、运行内存约 8.5 GB；支持 Linux、Apple Silicon 和 Windows WSL2 | 整机 16 GB RAM 起；若全模型放 CUDA GPU，短输入建议 12 GB 显存起。CPU 可运行但作者提示慢 |
| hanzhi227/local-jev + Qwen3.5-4B / MLX | 作者报告 8-bit 运行约 4.5 GB，4-bit 约 2.4 GB，但加载均约需 9 GB；首次下载约 8.7 GB | Apple Silicon 16 GB 统一内存起更合适；不能用 2.4 GB 运行数字判断 8 GB Mac 一定能加载 |

来源：[JevEmbed README](https://github.com/HITsz-TMG/JevEmbed)、[0.6B 默认配置](https://github.com/HITsz-TMG/JevEmbed/blob/main/configs/jevembed-qwen3-embedding-0.6b.yaml)、[amithgc/local-jev README](https://github.com/amithgc/local-jev)、[hanzhi227/local-jev README](https://github.com/hanzhi227/local-jev)。

hanzhi227 的实现还支持 macOS/Linux 上的 llama.cpp GGUF；其链接的 Qwen3.5-4B Q4_K_S 文件约 2.85 GB，Q4_K_M 约 3.01 GB。这些是模型文件大小，不含缓存和运行时，不能直接当作显存需求。[量化文件发布者的模型卡](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF)

## 选择建议与边界

普通 Windows 无独显电脑可先试 jevos 的原生包；想研究 embedding 如何转换为决策，可试 JevEmbed 0.6B；有更多内存或 GPU 时，再比较 Qwen 4B 实现。以上均为社区替代方案，API 形状接近不代表拥有 TypeSafe Jev 的准确率或概率校准能力。

jevos 模型卡标注 English；中文任务应另测。JevEmbed 0.6B 默认配置明确写有 `calibration_status: uncalibrated`，不能把 0.9 概率直接视为已验证的 90% 正确率。amithgc/local-jev 的公开性能测于 Apple M4 Max，不能直接套用到 Windows CPU；其 README 披露提示布局曾在同一公开评测集上选择。长输入、更大批量和并发会改变资源需求。[jevos 模型卡](https://huggingface.co/feder-cr/jev)、[JevEmbed 配置](https://github.com/HITsz-TMG/JevEmbed/blob/main/configs/jevembed-qwen3-embedding-0.6b.yaml)、[local-jev 评测说明](https://github.com/amithgc/local-jev#how-it-compares)
