# 用户提供的七篇文献：检索状态与项目用途

检索日期：2026-10-23。本记录区分题录核对、作者介绍、摘要阅读和全文阅读。本次未取得前六篇完整论文，不据此复述其具体架构、数据集或实验数字；第七篇已查看作者公开预印本的摘要。

## 1. 检索状态

| 编号 | 用户给出的论文 | 本次核对情况 | 阅读边界 |
|---|---|---|---|
| 1 | Multi-Agent LLM System for Automatic Meeting Information Processing | 检索到 NOMS 2025 官方会程中的题名和作者；DOI 线索为 10.1109/NOMS57970.2025.11073611 | IEEE 页面有访问验证，尚未取得全文 |
| 2 | A Production Grade Hybrid Summarization System for Meetings: Combining T5 and BART for Improved Fluency and Contextual Accuracy | 校方原始简报记录了该题名及作者参与；检索到 DOI 线索 10.1109/ICCMC65190.2025.11140906 | 尚未从全文核对方法、性能或“production grade”的具体含义 |
| 3 | Design and Implementation of a Multi-Agent Collaborative Framework for the Office Domain | Crossref 题录匹配，作者、会议、页码 991–995 对应；DOI 为 10.1109/ICAICE68195.2025.11382306 | 已核对题录，尚未读到方法和实验正文 |
| 4 | Intelligent Meeting Insights Pipeline: A Multi-Modal AI System for Automated Analysis | 检索到匹配题录及 DOI 线索 10.1109/IC3ET64989.2026.11467472 | 出版方全文未取得；多模态具体输入、融合方式及指标未确认 |
| 5 | AI Based Meeting Summarizer using Large Language Models | 检索到匹配题录和作者公开介绍，作者介绍包含转写、摘要及行动项处理 | 作者介绍不替代论文全文，具体实验和数据范围仍待阅读 |
| 6 | An artificial intelligence approach to automatically generate Cantonese meeting minutes for e-government | Crossref 题录匹配，作者、会议、页码 7053–7059 对应；DOI 为 10.1109/SMC58881.2025.11342794 | 已核对题录，粤语模型、数据集及评估方法未从正文确认 |
| 7 | A Communication Consistent Approach to Signal Temporal Logic Task Decomposition in Multiagent Systems | 读到作者公开的 arXiv:2410.12563 摘要，内容为通信受限条件下的 STL 任务分解与多智能体控制 | 摘要可确认研究范围；用户给出的期刊卷期页码仍需出版记录复核 |

Crossref 查询部分返回限流，其余题录匹配不等于已经阅读论文。IEEE 访问验证没有通过绕过手段处理。

## 2. 各篇可以怎样服务本项目

以下为阅读与设计映射建议，除明确标注的公开摘要内容外，不是论文实验结论。

- **[1] 优先阅读全文**：题目与本项目最接近，重点查角色划分、协作顺序、上下文传递、会议输入与评价方式。是否使用五个角色以及具体框架，需要看正文。
- **[2] 摘要质量与对比方法参考**：题名明确涉及 T5/BART 混合摘要，适合用于整理摘要技术路线与可选基线，不意味着本项目必须训练或复现两种模型。
- **[3] 办公场景协作设计参考**：阅读时关注任务分派、结构化通信、模块边界与业务集成；不能仅凭“multi-agent”认定它实现了 Hermes 的特定协议。
- **[4] 系统流水线参考**：适合查阅其输入模态、处理步骤和性能评估。视频、表情等额外输入是否需要纳入 MeetFlow，应按用户任务范围决定。
- **[5] 工程流程参考**：作者公开介绍支持其关注转写、上下文摘要和行动项提取。可用于理解一体化会议应用的功能构成；模型细节与效果仍需原文。
- **[6] 语言与场景适配参考**：优先查术语、语言特性、语音错误向纪要传递的问题。它不构成 MeetFlow 必须支持粤语的依据。
- **[7] 协作理论背景**：公开摘要研究任务依赖与通信连接的一致性，通过 STL 和分布式优化处理控制问题。它与 LLM 的语义推理、提示词、Profile 和 Skills 是不同层次；可作宽泛协作背景，不宜当成本项目的核心算法或 Hermes 原理依据。

## 3. Hermes 原理资料需要单独补齐

已查看 NousResearch/hermes-agent 官方仓库及 Profile、delegation、memory、Skills 文档，确认这些概念有官方说明。

仍未核实任务材料里的“Rust 核心与 Uplink 协议”对应的具体 Hermes 实现。七篇参考文献没有提供指定框架仓库的答案，不能据此拼接框架架构。后续先锁定仓库与提交/版本，再将论文相关技术章节对齐实际实现。

## 4. 可用的一手入口

- [NOMS 2025 官方会程](https://noms2025.ieee-noms.org/sites/noms2025.ieee-noms.org/files/NOMS2025_Final_Program%20%281%29.pdf)：搜索索引命中 [1]；本次直接打开时未成功加载，不能作为已读全文记录。
- [校方院系简报](https://svecw.edu.in/wp-content/uploads/2026/03/2025-Issue-2.pdf)：包含 [2] 的题名和作者参与信息。
- [文献 3 DOI](https://doi.org/10.1109/ICAICE68195.2025.11382306)、[文献 6 DOI](https://doi.org/10.1109/SMC58881.2025.11342794)：本次题录通过 Crossref 查询核对。
- [文献 5 作者介绍](https://www.linkedin.com/posts/kavananselm_ai-based-meeting-summarizer-using-large-language-activity-7472596360127614976-y1tY)：作者对工程功能的说明。
- [文献 7 作者公开预印本](https://arxiv.org/abs/2410.12563)：本次已阅读摘要，正文入口可继续阅读。
- [Hermes 官方仓库](https://github.com/NousResearch/hermes-agent)、[Profile](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)、[Delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation)、[Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)、[Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)。

## 5. 达到至少十二篇阅读要求的后续安排

现有七篇之外，优先补充以下直接相关主题，至少再选五篇：通用 ASR、说话人分离、LLM 工具调用与协作、长期记忆/上下文策略、会议数据集与摘要/行动项评价。

为每篇保存正式题录、可访问全文、研究问题、方法、数据集、指标、局限和本项目借鉴点。仅有检索命中、题名或作者简介的条目，不标成“已阅读全文”。
