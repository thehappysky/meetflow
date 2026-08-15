# 协作契约：让任务、证据和分歧可以由代码管理

本文配合 [主方案](../BLUEPRINT.md)，描述完整领域契约和调度目标。已有 v0.1 实现部分数据库、API、协作与审批；完整字段/伪代码不能直接当作全部已实现能力，以 [实际实现范围](12-implementation-status.md)为准。

## 1. 领域对象及不变量

| 对象 | 核心字段 | 必须由代码验证的规则 |
|---|---|---|
| SourceVersion | source_id、version、content_hash、kind、project_id、captured_at、valid_time、access_scope | 内容版本不可变；读取前授权；删除/撤权有明确策略 |
| SourceManifest | manifest_id、sources、created_at | 运行输入可复现，刷新形成新观察，不覆盖旧版本 |
| EvidenceRef | source_id/version、span_id、起止位置、quote_hash、relation | 引用存在、版本一致、位置合法；存在不等于语义支持 |
| Statement | statement_id、kind、text、modality、evidence、status | 显式区分事实陈述、决定、建议、承诺；候选不自动成为批准事实 |
| Event | entity_id、event_type、payload、effective_at、recorded_at、evidence | 日期和状态变化有依据；不是最后出现的字符串覆盖一切 |
| Relation | from_id、to_id、type、evidence、status | supports/contradicts/supersedes/depends_on 有明确方向和依据 |
| TaskSpec | task_id、generation、role、goal、input_refs、depends_on、budget、schema | 角色、依赖、输入版本和范围合法，无环且预算足够 |
| AgentAttempt | attempt_id、task_id、input_hash、role/model/skill versions、usage、status | 执行尝试独立；重试不静默覆盖历史 |
| AgentResult | attempt_id、status、artifact_refs、open_questions、requests、usage | succeeded/partial/failed 可区分；产物可回源 |
| Challenge | challenge_id、target_revision、type、support/refutation_refs、requested_check | 争议指向具体版本与字段，说明问题而非笼统否定 |
| Resolution | challenge_id、disposition、evidence、responsible_actor、new_revision | 处理不能凭投票隐去反证；人工决定保留主体和依据 |
| OperationPlan | plan_revision、operations、preconditions、expected_versions、hash | 只描述建议；每个操作有目标、字段差异和前置条件 |
| Approval | report_revision、operation_set_hash、actor、scope、expected_versions | 审核后修改内容必须重新匹配批准范围 |
| Delivery | operation_id、adapter、idempotency_key、status、remote_id、receipt | 区分成功、失败和结果未知；状态更新幂等 |

ID 采用 UUID 等稳定标识。内容 hash 使用规范化序列化后计算，不能依赖 dict 展示顺序。不可变 artifact 的键与业务实体 ID 分开：同一任务可以有多个候选版本，但一份不可变产物不能被悄悄改写。

## 2. 输入与输出的关键字段

行动项保存：实体候选、标题、负责人候选及已确认身份、原始日期表达、归一化日期、日期来源、状态、执行条件、验收要求、证据和修订关系。

`date_source` 可为 explicit/inferred/policy/unknown。相对日期计算必须使用会议实际时间与时区。inferred 只表示建议，不能显示为会中承诺。人员映射可以 unknown/ambiguous/confirmed；speaker_id 与 member_id 是不同字段。

条件保存两部分：可审计原话和规范化谓词。例如发布前置条件为 AND(load_test_passed, security_review_passed)，每个谓词有数据源、实体映射、观察值和观察时间；不能把自然语言解析正确等同于环境状态已经满足。

## 3. 工具协议

所有工具具有明确输入输出 Schema、角色权限、超时、最大返回体、来源版本和错误类型。建议提供这些工具族：

| 工具族 | 例子 | 实现边界 |
|---|---|---|
| 原文 | search_transcript、read_neighbors、get_span | 只读当前允许版本，返回原文及定位 |
| 历史 | search_project_history、get_decision_chain | 返回来源，不只返回压缩总结 |
| 项目 | read_task、list_task_changes、read_requirement | 暴露任务快照/文档版本，不开放任意 SQL |
| 身份与时间 | lookup_member_candidates、normalize_relative_date | 候选不自动确认身份；解析不等于承诺 |
| 图检查 | get_dependencies、check_dependency_cycle | 关系范围固定；确定性结果与模型解释区分 |
| 协作 | request_information、submit_artifact、raise_challenge | 经过 schema 与 generation 校验，再写工作区 |

项目、用户和可见来源从可信 RuntimeContext 注入，不由模型自选。工具输出即使包含“忽略规则”等文字，也只是外部数据。写入正式任务、发送通知的工具不提供给调查专家；通过批准后执行服务处理。

工具错误采用类型：not_found、unavailable、permission_denied、invalid_args、stale_version、budget_exhausted。超时不能伪装成“未发现风险”。

## 4. 父图与子图的状态范围

父图保存 run_id、manifest_id、plan_generation、TaskSpec 引用、本轮预期任务 ID、不可变结果引用、未决争议引用、report_revision 和运行状态。模型客户端、连接池、原音频和整份检索文档不放检查点；图 state 的 JSON 也不是权限边界。

专家子图保存当前 TaskSpec、局部消息/观察、检索游标、读取过的证据索引和局部预算摘要。完整调用回执与用量另有可审计表；子图只向父图交付验证后的结果引用，不暴露所有局部历史。

工作区与父图状态不是两份可随意互改的业务真值：工作区拥有业务产物，父图拥有执行位置与引用。先提交幂等产物，再提交父图引用；恢复时核对二者。

## 5. 调度伪代码

```text
load current plan generation
validate role, dependency DAG, source scope and reserved budget
ready = tasks whose dependencies are valid and terminal as required
if ready is nonempty:
    persist expected_task_ids for this wave
    dispatch validated TaskSpec through Send
    each worker runs its private specialist subgraph
    each worker commits immutable result and returns only its reference
    collect only current-generation results for expected_task_ids
    wait/check every expected task has an explicit terminal status
    release unused reservations, record failures, evaluate next dependencies
elif open challenges have a new evidence path and budget remains:
    coordinator proposes bounded revision tasks
    validate and advance generation; invalidate affected downstream artifacts
elif a business ambiguity or critical unsupported operation remains:
    persist report candidate and interrupt for human input
else:
    create report candidate and request version-bound approval
```

这是应用任务 DAG 映射到固定 LangGraph 拓扑的协议，不执行 LLM 生成的 Python 图代码。每个图 thread 由单一持有有效租约的 runner 推进，禁止两个请求直接同时 resume 同一个 thread。

需要自动故障接管时，默认 saver 不能仅靠业务层 fencing 获得保护。计划提供受所有权检查约束的 checkpoint 写入集成：在同一数据库事务中锁运行记录、验证 fencing token，再写 checkpoint/intermediate writes；没有完成并验证这项集成前使用单 driver 串行运行，不开放抢占式接管。本文没有实现该 saver。

## 6. 分歧处理矩阵

| 争议 | 自动处理方法 | 何时交人工 |
|---|---|---|
| 未读到后续改期 | 定向检索及新版本修订，保留原证据 | 指代仍不明确 |
| “老王”可能是两人 | 查询成员与已确认映射，保留候选 | 没有唯一证据 |
| 会议说完成，任务状态说未完成 | 区分来源时间和完成口径，补读验收记录 | 实际状态仍无法确定 |
| 多条日期对应不同任务 | 核对任务身份、上下文及修订关系 | 存在多种合理关联 |
| 两条真实决定互相冲突 | 检查是否明确替代、是否作用域不同 | 需要业务选择 |
| 建议超出会议承诺 | 降为建议，审核界面明确展示 | 用户选择是否新增任务 |
| 重复审查没有新证据 | 终止补查，说明剩余问题 | 保留待确认；不继续多数投票 |

## 7. 版本失效与局部重算

| 变化 | 直接失效内容 | 通常不必重跑 |
|---|---|---|
| 转写原文修正 | 对应 statement、依赖它的计划与审查 | 音频解码与其他独立议题 |
| 姓名映射修正 | 相关负责人归属、操作建议和审核 | 无关议题抽取 |
| 源任务变化 | 基于旧 expected_version 的操作 | 已完成的原文理解 |
| 需求文档新版本 | 依赖旧规则的建议与风险 | 录音转写 |
| 技能或模型升级 | 新分析可以重跑；旧报告保留版本 | 已批准业务事实不会自动被新模型重写 |
| 新条件事件 | 等待该条件的任务与风险观察 | 全场语音处理 |

跨来源关系有不确定性时，失效范围可以保守扩大；不能为了少计算而保留明显过期审批。

## 8. 执行操作状态机

`proposed → approved → queued → sending → succeeded / failed / unknown`。unknown 进入 reconciling：能证明已执行则成功，能证明未执行且允许重试则回队列，无法判断则人工处理。源对象版本变化使相关操作 stale，不能直接发出。

一批操作部分成功时，保存每个操作回执并继续处理依赖；不要把整批重发。补偿只能按明确规则操作，不能撤销用户之后的修改。每次正式写入保存批准引用和 fencing token 校验结果。

## 9. 建议代码组织与接口

```text
backend/app/
  api/                 # meetings, runs, reviews, projects, events
  domain/              # sources, statements, tasks, approval, operations
  agents/              # coordinator, meeting, context, planner, risk, auditor
  orchestration/       # parent_graph, specialist_graph, scheduler, reducers
  tools/               # registry, permissions, read adapters, usage
  memory/              # retrieval, event relations, source manifests
  services/            # audio, report rendering, approval, execution
  persistence/         # business repositories, artifact store, outbox
  workers/             # audio, run driver, delivery, event scheduler
  evaluation/          # cases, annotations, metrics, runner
frontend/
skills/
tests/
```

上面是计划目录，目前未创建完整工程。API 建议围绕 `/projects`、`/meetings`、`/runs`、`/reports/{revision}`、`/reviews`、`/operation-plans`、`/tasks`、`/runs/{id}/events` 设计。业务实体 ID 与 graph thread_id 内部映射，客户端不能凭一个 checkpoint ID 绕过项目授权。
