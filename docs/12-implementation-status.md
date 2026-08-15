# v0.1 已实现范围与恢复开发入口

本文件按阶段维护，当前实现不等同于 BLUEPRINT.md 的全部目标。提交日期说明见 [COMMIT_METADATA.md](COMMIT_METADATA.md)。

## 已实现

- FastAPI＋SQLAlchemy 本地持久化；项目、会议、来源版本、运行快照、产物、报告、任务、审批、事件与审计。
- 真实 LangGraph 执行：协调规划、DAG 验证、动态派发、私有专家子图、受限工具循环、结果汇聚、审查及定向返工。
- 两种明确区分的提供者：离线规则模拟，以及支持 function tools 的真实模型 HTTP 适配器。后者已有模拟传输测试，尚无真实供应商调用效果测试。
- 原文引用校验、相对日期工具、调用预算、取消、有限迭代、失败恢复与应用重建后的审批恢复。
- 版本/内容 hash 绑定审核、明确范围的批准、幂等键、本地任务原子写入、旧任务版本冲突回滚、用户变更审计。
- Vue＋TypeScript 页面：会议导入、真实阶段进度、示例、证据侧栏、集中澄清、编辑/批准任务、看板、项目资料与开发追踪。
- Markdown 导出包含候选、实际已批准字段与来源依据；草稿和规则模式明确标记。
- 可选 faster-whisper 适配器、音频上传/持久化/播放、片段时间戳；未配置明确拒绝，无静默演示回退。
- 项目任务事件保存、历史已确认任务资料、手动重新分析、基于未完成任务的下次议程。

## 尚未完成或未验证

- 真实模型端到端效果与费用、真实录音质量、说话人分离/姓名映射界面、Word/PDF 导出。
- PostgreSQL checkpointer、Redis/Celery、多实例租约/fencing、集群故障接管；本版为单 driver 本地运行。
- 外部任务平台投递/未知结果对账；当前正式执行对象仅本地任务。
- 自动事件触发的跨会议局部重分析、完整时间关系图、混合/向量检索、版本化技能及其消融。
- 多用户登录、项目成员授权和共享；当前只监听本地回环地址，按单用户开发版使用。
- 渐进式草稿流式输出、用户修改摘要的版本合并；当前完整报告生成后再审核，任务标题/负责人/日期可编辑。
- 完整质量数据集、架构基线、人工评测及性能容量承诺。

## 当前代码入口

| 内容 | 文件 |
|---|---|
| API 与应用生命周期 | backend/meetflow/api.py |
| 父图、DAG、恢复与 runner | backend/meetflow/orchestration.py |
| 专家子图、角色与结构验证 | backend/meetflow/agents.py |
| 规则演示和真实传输 | backend/meetflow/providers.py |
| 只读工具与日期 | backend/meetflow/tools.py |
| 业务持久化与运行快照 | backend/meetflow/database.py、store.py |
| 审核、任务提交和导出 | backend/meetflow/reviews.py |
| 本地音频适配 | backend/meetflow/audio.py |
| 用户工作台 | frontend/src/App.vue |
| 行为与协议测试 | tests/ |
| 浏览器实际操作 | scripts/smoke_browser.py |

## 后续优先级

1. 在用户配置模型后，以真实工具调用验证提示、复杂跨源语义与审查质量，完善供应商兼容。
2. 增加真实录音与说话人映射、渐进式草稿和摘要编辑。
3. 完成事件驱动重分析、检索与版本化项目记忆；建立固定场景评测。
4. 引入 PG/队列、外部任务适配及多用户权限，按故障清单逐项验收。

继续开发先看 PROGRESS.md、CHANGELOG.md 和 AGENTS.md。每批修改运行对应行为检查、追加记录，并使用 scripts/commit.ps1 提交，之后推送 origin。
