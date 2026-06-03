# 框架研究：机制、源码与项目中的使用方式

核对日期：2026-10-24。本文是选定机制的文档/源码研究，不是全面源码审计或框架性能排名。

## 1. 框架选择的结论

MeetFlow 选择 **LangGraph 作为唯一主 Agent 执行运行时**，用 Pydantic 建领域契约，用自己的业务服务实现证据、任务、审批和执行。不再叠加 CrewAI、Pydantic AI 或另一套高层 Agent runtime。

选择依据是本项目需要显式的多轮任务派发、独立专家上下文、并行结果汇聚、局部反馈与持久化中断，而不是“LangGraph 天生比其他框架更强”。真实模型的速度、质量与维护成本还没有测量。

研究代码来自官方仓库，固定提交 `eb066bd31a2bf01fc0cd58256df4800a3695c233`，提交时间 `2026-10-23T14:18:08Z`。该提交内 `libs/langgraph/pyproject.toml` 的包版本为 **1.2.16**；提交标题中的 CLI 版本与核心包版本不同。源码本地位于 `E:/chatgpt/jianli/_references/langgraph`。

独立环境安装 `langgraph==1.2.16`、`langgraph-checkpoint-sqlite==3.1.2`，完整实验依赖见 [requirements-lock.txt](../labs/framework_spike/requirements-lock.txt)。研究使用 Python 3.13.9，产品目标 Python 3.12 仍需完整依赖验证。生产 PG 检查点尚未运行验证。

## 2. 从执行语义理解框架

### 2.1 StateGraph 是声明，运行时是 Pregel

StateGraph 描述节点、状态通道和边；compile 产生可执行图。[官方运行时说明](https://docs.langchain.com/oss/python/langgraph/pregel)把它组织成分步执行：选择本轮任务、执行、合并写入，再推进下一步。

本项目应据此建立协作轮次。专家不能假设在同一步运行的另一专家刚写入的结果已经可见；需要通过下一步汇聚再消费。不同子图实例完成后，父图才处理其提交的结果。

实际查看：[StateGraph](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/graph/state.py#L131)、[compile](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/graph/state.py#L1177)、[PregelLoop.tick](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/pregel/_loop.py#L650)。只读这些实现不能声称掌握整个框架全部路径。

### 2.2 Reducer 是并行写入规则，不是业务裁判

普通状态字段默认采用 LastValue 通道。同一步多个任务写它会触发并发更新错误，不会自动选择“最合理”的答案。[LastValue.update](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/channels/last_value.py#L54)明确检查写入数量。

带 reducer 的字段由对应通道聚合；[BinaryOperatorAggregate](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/channels/binop.py#L66)保存并更新聚合值，[apply_writes](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/pregel/_algo.py#L233)按通道组织写入。

MeetFlow 只并行合并不可变产物引用：同键同值重复到达可忽略，同键异值报错。把“日期周三”和“日期周五”都保存在独立 claim 中，由争议流程裁决。不要通过 dict 的最后覆盖顺序解决语义冲突，也不要依赖 reducer 内调用模型或数据库。

### 2.3 Send 使动态任务数量与不同输入成为可能

[Send](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/types.py#L738)携带目标节点和任务参数，可以把同一角色应用于多个议题。MeetFlow 用验证后的 ready 任务生成 Send，参数只含该任务上下文引用与版本。

Send 不会自动提供业务任务持久化协议、预算、数据权限、跨机器投递或幂等外部操作。它也不能用一个固定节点名称自动表达任意任务 ID 的依赖图；动态依赖由本项目 TaskSpec 和轮次调度代码维护。

### 2.4 汇聚要区分固定分支与动态任务集合

固定分支可以用 `add_edge(["left", "right_done"], "join")` 等待两路。[add_edge](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/graph/state.py#L928)及 [attach_edge](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/graph/state.py#L1551)表明多起点依赖采用 barrier 通道，而分别添加两条单起点边不是同一声明。

动态 Send fan-out 的一个执行波次由 worker 返回结果后汇聚，再由应用检查本轮预期 task IDs 是否齐全、generation 是否有效、失败是否为允许降级。若引入独立跨进程作业，必须显式等待结果终态，不能把某个 worker 的一次完成事件当作整轮完成。

### 2.5 子图状态隔离与检查点作用域

[子图文档](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)区分每次调用、每线程和无检查点模式。专家调查默认采用每次调用隔离，继承父图的检查点能力；项目长期记忆另存业务资料，不依赖一个专家聊天记录永久增长。

MeetFlow 用可发现的包装节点调用专家子图，映射输入并只返回有效产物引用。不要把所有专家消息都并入父图；也不要在并行调用里复用同一个累积型子图 checkpoint namespace。涉及 API 细节时以锁定版本的测试为准。

### 2.6 Command 会增加动态路由，不会自动撤销静态边

[Command 源码](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/types.py#L833)定义 update、goto、resume 和 parent graph 等参数。[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)说明，节点同时保留静态边和 Command.goto 时，两条路径都可能执行。

本项目的路由节点要明确采用条件边还是 Command，不把两种路径无意叠加。子 Agent 不获得任意父图跳转权限，跨角色协作先提交请求，由协调器验证。

### 2.7 Interrupt 是可恢复中断，会重新进入节点

[interrupt 实现与说明](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/langgraph/langgraph/types.py#L903)明确说明恢复时从节点开头重新执行，而不是恢复任意 Python 指令栈位置。

审核节点在 interrupt 前只能做可重复的读取与幂等准备，不能先发送通知、创建远端任务再等待批准。恢复值只是输入，应用仍校验报告版本、操作 hash、审批用户和当前任务版本。图中断不会自动执行这些业务校验。

### 2.8 Checkpoint、业务事务与外部效果是三个问题

[PostgreSQL saver](https://github.com/langchain-ai/langgraph/blob/eb066bd31a2bf01fc0cd58256df4800a3695c233/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py#L45)保存 checkpoint、channel blobs 和 intermediate writes；这不等于把业务任务、审批与远端 API 一并变成同一事务。

MeetFlow 先以幂等键提交不可变产物，再让图保存引用；正式任务事务写 outbox，外部投递独立对账。不要宣称 checkpoint 提供端到端 exactly-once。重试只覆盖已定义可重复范围，LLM 重试也可能产生不同结果和额外费用。

### 2.9 Replay 不是撤销已经发生的业务操作

[Time-travel 文档](https://docs.langchain.com/oss/python/langgraph/use-time-travel)说明，选定 checkpoint 后的节点会重新执行，包括模型及 API 调用，结果可能变化。

实验 fork 使用新的 analysis run 和产物空间，禁止进入真实外部执行器。产品重分析产生新报告与新审批，不通过回放旧批准来执行新操作。

### 2.10 框架不会代替任务权限和全局预算

`recursion_limit` 约束图步数，不等于总 token、工具次数或费用。图的并发参数也不等于多 worker 的全局模型配额。MeetFlow 在工具网关和 runner 中实现权限、参数验证、超时、并发预留与用量结算。

LangGraph 核心能在本项目服务内使用；不要求额外采用托管 Agent Server 或 LangSmith 才能实现这些业务能力。追踪可以使用 OpenTelemetry，具体查看器独立选择。

## 3. 运行时实验及其证据范围

[verify_runtime.py](../labs/framework_spike/verify_runtime.py)使用真实框架、脚本节点和临时 SQLite，不接模型或真实音频。2026-10-24 六项实验通过：

| 实验 | 检查什么 | 能支持的结论 |
|---|---|---|
| 动态派发与子图 | 两个 Send 调用同一专家子图，各执行两次脚本观察 | 独立输入/局部状态可实现，私有字段未进入父图输出 |
| 无 reducer 并行写 | 两分支写同一普通字段 | 真实触发 InvalidUpdateError |
| 不可变结果合并 | 重复同值、交换次序、同键异值 | 所选 reducer 具备对应幂等和拒绝冲突行为 |
| 不同深度汇聚 | 左一路一步，右一路两步，显式 barrier | join 在两路完成后调用一次 |
| 重载并恢复 | 关闭 SQLite saver，重建图与 saver 后 resume | 检查点可重载，interrupt 节点重新进入 |
| 过期批准 | 当前 v4，却恢复 v3 的批准 | 应用校验拒绝；框架本身不提供该业务规则 |

这里的“重载”在同一测试进程中关闭并重建连接和图，不是断电、跨进程崩溃或 PostgreSQL 故障测试。当前没有验证集群租约、真实 token 预算、外部对账或真实 Agent 推理。

## 4. 其他框架：真实能力与选择理由

| 框架 | 官方资料 | 核对内容 | 为什么本项目没有叠加采用 |
|---|---|---|---|
| Microsoft Agent Framework | [概述](https://learn.microsoft.com/en-us/agent-framework/overview/)、[工作流](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/) | Agent、工作流、事件、人工输入与检查点；Python 功能式 API 有其状态说明 | 同样可实现本题；当前以 LangGraph 深入一套执行语义，避免两套调度器叠加 |
| CrewAI | [Flows](https://docs.crewai.com/en/concepts/flows)、[Processes](https://docs.crewai.com/en/concepts/processes) | 角色任务、层次过程、结构化 Flow 状态与持久化 | 角色抽象方便；本项目重点是显式产物、冲突与恢复，不再额外引入 crew 调度层 |
| Pydantic AI | [多 Agent](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)、[持久执行](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/) | 委派、程序性交接、图控制及 durable engine 集成 | 类型与注入能力很有价值；领域 Schema 使用 Pydantic，Agent runtime 保持一套 |
| AutoGen | [官方仓库](https://github.com/microsoft/autogen) | 当前仓库明确标记维护模式，并建议新项目考虑 Microsoft Agent Framework | 参考通信模式，不依据过时教程把它选为新项目默认框架 |

未对这些框架逐一安装、跑同一产品或核对全部源码，不能据此写性能排行榜。官方文档可能继续变化，迁移前重做依赖和行为测试。

## 5. 研究资料如何实际影响设计

| 资料 | 实际阅读范围 | 在本项目中采用的启发 |
|---|---|---|
| [Building effective agents](https://www.anthropic.com/research/building-effective-agents) | 网页工作流、并行、协调者和检查反馈段落 | 外层受控图与内部工具循环分开；协作不是无限群聊 |
| [Multi-agent research system](https://www.anthropic.com/research/multi-agent-research-system) | 网页上下文、独立探索及工程问题段落 | 专家独立检索后压缩交接；不迁移它的效果数字 |
| [MAST / Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/html/2503.13657v3) | 正文与分类、部分附录段落；非逐页完整审读 | 明确角色、停止条件、信息交接和验证，构建失败分类 |
| [ReAct](https://arxiv.org/abs/2210.03629) | 摘要 | 根据工具观察继续行动；本项目不要求记录模型私有思维 |
| [MemGPT](https://arxiv.org/abs/2310.08560) | 摘要 | 将工作上下文与外部长期资料区分；不声称复现其系统 |
| [Reflexion](https://arxiv.org/abs/2303.11366) | 摘要 | 反馈可作为下一次尝试的输入；不把文字反思称为权重训练 |
| [QMSum](https://aclanthology.org/2021.naacl-main.472/) | 题录与摘要 | 议题查询、相关发言定位的子任务评测 |
| [MeetingBank](https://aclanthology.org/2023.acl-long.906/) | 题录与摘要 | 长会议与纪要对齐研究；不冒充项目执行标注 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[pyannote](https://github.com/pyannote/pyannote-audio)、[WhisperX](https://github.com/m-bain/whisperX) | 官方 README | 音频适配器候选；未运行转写，不搬用主页性能 |
| [pgvector](https://github.com/pgvector/pgvector) | README 检索、过滤与 ANN 相关段落 | 授权范围与检索召回分开处理，过滤后不足时补足扫描或精确搜索 |
| [Celery tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html) | 确认、幂等、重投递与超时相关说明 | 粗粒度任务驱动，业务幂等由服务实现 |
| [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/) | 官方入口与能力说明 | 统一追踪接口；具体仪表盘实现待开发 |

这些资料是设计依据与待深入阅读清单，不构成“已完成若干篇文献的全文综述”。产品效果、研究贡献和工程改进必须由自己的实现与数据支持。
