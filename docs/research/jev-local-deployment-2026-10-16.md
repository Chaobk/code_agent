# Jev 本地部署调研

初次核对：2026-10-16；资料范围修订：2026-10-28。未在本机下载模型或实测性能。本文保留 jevos-v4 的历史比较；当前 main README 已转向 v5，不能把移动 README 的新数字与 v4 发布说明拼成同一次评测。

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

v4 标签下的 README 同时保留了较早模型版本的延迟图；其中 28/130 ms 的对照标为 v3，并非 v4 在本机的新测量。作者方法使用指定笔记本 CPU、固定线程数和预热后的多次请求中位数，不能推广为任意 CPU 的速度保证。不同版本的图表、文本缓存与冷启动结果应分别记录。[v4 固定标签 README](https://github.com/feder-cr/jev/blob/jevos-v4/README.md)

v4 发布说明报告 821 道新是非题准确率 87.0%，999 道手写题 78.9%，后者低于 v3 的 80.8%。这些是作者评测，需核对样本来源与 checkpoint 选择是否重叠。当前 main README 的评测披露已有变化，不能拿它解释旧版全部数据；采用时应保存模型版本与相应评测材料，再用实际中文任务复测。[v4 发布说明](https://github.com/feder-cr/jev/releases/tag/jevos-v4)、[当前评测披露](https://github.com/feder-cr/jev#benchmarks)

## 其他本地实现及硬件选择

下面保留原调研读取的作者资源数据与复测重点。没有同版本、本机的加载和峰值测量，不能据此给出整机或显存的最低配置。

| 实现 | 原调研中的公开资源数据 | 采用前的验证重点 |
| --- | --- | --- |
| jevos / OpenVINO INT8 | 作者描述进程约 1–1.4 GB RAM | 记录模型版本、CPU 指令支持、线程数、首次加载与文本缓存开销 |
| JevEmbed-Qwen3-Embedding-0.6B | 默认 device/dtype 为 auto，输入截断至 1,024 tokens | 检查自动选中的设备和精度，测加载及批量峰值；超长输入截断会改变决策依据 |
| amithgc/local-jev + Qwen3.5-4B | 作者报告下载 9.32 GB、运行内存约 8.5 GB | 区分下载体积、CPU RAM、GPU 显存与不同后端；该动态 README 未在此次完整复测 |
| hanzhi227/local-jev + Qwen3.5-4B / MLX | 作者分别报告量化运行占用与更高加载峰值 | 加载峰值和稳定运行占用分别测量，不能只用量化文件大小判断能否加载 |

来源：[JevEmbed README](https://github.com/HITsz-TMG/JevEmbed)、[0.6B 默认配置](https://github.com/HITsz-TMG/JevEmbed/blob/main/configs/jevembed-qwen3-embedding-0.6b.yaml)、[amithgc/local-jev README](https://github.com/amithgc/local-jev)、[hanzhi227/local-jev README](https://github.com/hanzhi227/local-jev)。

hanzhi227 的实现还支持 macOS/Linux 上的 llama.cpp GGUF；其链接的 Qwen3.5-4B Q4_K_S 文件约 2.85 GB，Q4_K_M 约 3.01 GB。这些是模型文件大小，不含缓存和运行时，不能直接当作显存需求。[量化文件发布者的模型卡](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF)

## 选择建议与边界

这些实现可作为决策模型与 embedding 评分的探索对象。比较时固定输入和判据，先测响应含义、截断、错误与校准，再测延迟和资源占用。社区实现 API 形状接近，不代表拥有 TypeSafe Jev 的准确率或概率校准能力；当前项目也未接入它们作为权限裁判。

jevos 模型卡标注 English；中文任务应另测。JevEmbed 0.6B 默认配置明确写有 `calibration_status: uncalibrated`，不能把 0.9 概率直接视为已验证的 90% 正确率。amithgc/local-jev 的公开性能测于 Apple M4 Max，不能直接套用到 Windows CPU；其 README 披露提示布局曾在同一公开评测集上选择。长输入、更大批量和并发会改变资源需求。[jevos 模型卡](https://huggingface.co/feder-cr/jev)、[JevEmbed 配置](https://github.com/HITsz-TMG/JevEmbed/blob/main/configs/jevembed-qwen3-embedding-0.6b.yaml)、[local-jev 评测说明](https://github.com/amithgc/local-jev#how-it-compares)
