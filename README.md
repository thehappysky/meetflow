# MeetFlow：从会议录音到纪要与待办

当前目标是会议驱动的多智能体项目协作系统：结合录音、历史会议、需求文档与任务状态，完成调查、规划、风险核查、人工审核、任务执行与后续跟进。

当前交付为完整方案、框架源码研究、合成场景及通过六项检查的运行时实验。尚未实现完整服务、真实模型协作或 ASR，尚无产品质量与性能实测结果。

## 从这里开始

**[当前主方案：会议驱动的多智能体项目协作系统](BLUEPRINT.md)**

该文档按以下顺序阅读：

1. 产品目标、需求和完成程度。
2. 用户输入、最终输出与贯穿案例。
3. 六类 Agent 的职责、动态委派及完整数据流。
4. 各环节的代码实现思路、候选技术与验证方式。
5. 协作协议、上下文隔离、分歧处理和执行恢复。
6. 项目记忆、检索、技能与事件驱动的跨会议闭环。
7. 框架、工程技术栈、评测、开发及完成后的简历模板。

本轮以有深度的多智能体协作为明确设计目标，独立推导需求与角色。采用 LangGraph 作为主运行时，保留强工作流与单 Agent 基线，用于理解协作收益及成本。此前“工作流为主、多 Agent 可选”的方案保留为讨论历史。

## 深入阅读与可运行验证

- [用户使用流程与便利性](docs/11-user-experience.md)：首次使用、日常路径、集中澄清、低打扰跟进与用户测试。
- [框架研究与固定版本源码](docs/08-framework-research.md)：执行语义、框架比较、源码位置与阅读范围。
- [协作契约与调度机制](docs/09-cooperation-contracts.md)：任务、证据、挑战、版本、工具和执行状态。
- [评测与交付计划](docs/10-evaluation-and-delivery.md)：架构对照、消融、故障、数据与开发阶段。
- [新合成项目评审场景](examples/project-review-scenario.json)：跨会议改期、完成口径冲突、条件发布、身份歧义与后续事件。
- [框架运行时实验](labs/framework_spike/README.md)：真实框架、脚本节点，六项检查已通过；无模型和音频效果结论。

## 辅助阅读

- [贯穿方案的合成文字样例](examples/meeting-fixture.json)：日期改口、条件发布、身份歧义与暂停通知；不用于 ASR 评测。
- [原 GitHub 仓库审阅](docs/01-source-review.md)：原项目思路、实现缺口与来源。
- [七篇参考文献的检索状态](docs/07-literature-check.md)：题录、摘要与全文阅读边界。

## 历史设计材料

以下保留用于追溯讨论，技术预选和角色划分以主方案为当前讨论基础：

- [此前沿数据流设计的方案](DESIGN.md)：2026-10-24 本轮独立设计之前的讨论快照。
- [此前完整设计](docs/02-project-design.md)
- [此前 Python 数据与接口契约](docs/03-python-contracts.md)
- [此前评测与实施路线](docs/04-evaluation-roadmap.md)
- [简历与面试材料](docs/05-resume-interview.md)，须根据实际完成的代码调整。
- [毕业设计参考要求对齐](docs/06-requirements-alignment.md)

早期讨论曾审阅 [bcefghj/multi-agent-meeting-assistant](https://github.com/bcefghj/multi-agent-meeting-assistant)，提交为 `53ccea64570980d2b7acca18ada91cf395079b8f`。当前主方案不以该仓库或原五角色架构为约束。主方案更新于 2026-10-24。
