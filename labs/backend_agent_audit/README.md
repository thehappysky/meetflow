# 后端与 Agent 开发实验

先看 [预登记](PLAN.md)、[架构审计](../../docs/25-backend-architecture-audit.md)、[实测结论与限制](../../docs/26-quantitative-experiments.md)。结果汇总在 [results/summary.json](results/summary.json)，原始记录位于忽略目录 `test-results/backend-agent-audit/`，不把本机数据库、音频、私密请求或密钥提交。

用户已固定最终采用多Agent，单Agent仅实验参照。内部设计、门控和对应结果见[三种多Agent对照](../../docs/27-multi-agent-design-comparison.md)，不据不利开发结果擅自取消多Agent产品。

最新后端真实运行见[docs/35](../../docs/35-backend-runtime-acceptance.md)及[运行汇总](results/runtime_summary.json)：50项音频/重启和30项主服务纪要/质检通过；[state_v2四/三角色](results/coverage_ablation.json)15例12/11通过，69/49调用，原[base_v1](results/coverage_ablation_base_v1.json)单独保留；[管理质检](results/quality_summary.json)F5三类开发检查通过。失败不删，不将这些开发场景写成独立准确率。

```powershell
.venv\Scripts\python.exe labs/backend_agent_audit/summarize_live.py
.venv\Scripts\python.exe labs/backend_agent_audit/summarize_quality.py
# 两个原始目录必须同模型、同案例、同产品代码；只切覆盖核查开关：
.venv\Scripts\python.exe labs/backend_agent_audit/summarize_coverage.py --with-coverage test-results/backend-agent-audit/live-workflow/20261113T151835039920Z --without-coverage test-results/backend-agent-audit/live-workflow/20261113T152515849112Z
```

此汇总保留原始角色适用误报，另计算有事项才要求归并/审查的适用检查，不修改负责人/日期/状态/条件等预期。R4两轮最初源码包未包含该适用性辅助脚本，已执行后另附annotation，后续已补入，不能把补记当作预先冻结证明。主服务/实验采样曾不一致并实际失败，P1已对齐，本轮所有失败仍在运行汇总中。

职责本身另做R1，见[划分依据与对照](../../docs/28-agent-role-partition-evidence.md)：三种职责重点在同串行图下各24条，关键槽位均16/24，含证据条件均10/24，没有职责赢家。拓扑比较不等于职责比较，固定模型调用微基准不等于完整自主工具Agent验证。

内部另做I1（20例×4协议单次）与实际meeting工具循环探针，见[内部构造与结果](../../docs/29-agent-internals-and-ablation.md)。枚举减少一次非法ID失败，去重只证明实验封装省token；实际角色Schema首请求1449→877 token，但两种Schema均6步超限。I1候选当批未改产品；后续L2将角色字段裁剪纳入产品，见[真实工具链修复](../../docs/31-live-workflow-repair.md)及独立live_workflow_summary.json，不用静态体积或小基准冒充全角色质量。

## 文件与复算

| 文件 | 用途 |
|---|---|
| cases.py | 12 个有明确 gold 的中文合成开发场景；不是独立业务标注集 |
| agent_experiment.py | 本地真实模型、三种调用策略、原始请求响应、逐例失败与指标 |
| topology_experiment.py | 三种多Agent交接／审查机制；逐例至少两个Agent，记录未审查同错与净修复 |
| role_partition_experiment.py | 同拓扑下三组职责提示R1对照；复用计量／校验，审查提示与交接结构相同 |
| internal_cases.py | 原12例不改，另加8个代理编写开发场景；仍非独立gold |
| internal_experiment.py | 固定外部分工，引用解码／有效上游去重2×2对照 |
| role_schema_probe.py | 历史共用／角色Schema显式对照；即使产品已裁剪，shared_schema仍强制原Schema，旧轮源码保留 |
| live_workflow_probe.py | L1／L2实际工具循环与完整动态图诊断；隔离账号库、冻结输入、源码／模型摘要和全失败 |
| summarize_live.py | 独立输出live_workflow_summary.json，不覆盖之前固定调用实验汇总 |
| test_live_measurement.py | 检查负责人／日期／证据错误、建议误作行动和缺报告均不能算质量通过 |
| test_internals.py／test_role_schemas.py | 去重不变性、非法产物保留、字段语义／Schema引用完整性及探针超时透传 |
| test_topologies.py | 交接上下文隔离、争议门控、无效产物与一致但错误的计量 |
| test_role_partitions.py | 职责对照的阶段顺序、相同审查和交接控制变量 |
| query_experiment.py | Git 基线与候选 SQL 查询、严格返回等价、交错计时 |
| current_graph_probe.py | 真实产品图的有预算集成诊断，不是架构质量排名 |
| protocol_probe.py | DemoProvider 协议路径／Schema 体积探针；不是推理准确率 |
| sqlite_writes.py | 隔离 WAL 写事务探针；不是全应用压力测试 |
| capacity.py | 时长、数量、RTF 的假设敏感性测算；不是测得容量 |
| test_measurement.py | 验证失败进入分母和证据集合 F1 的统计行为 |
| summarize.py | 从所有本地轮次重算汇总，不覆盖旧原始结果 |
| sources.json | 原论文、官方文档和 GitHub 的阅读范围及用途 |

在仓库根目录 PowerShell 中运行，使用已有 Python 3.12 虚拟环境及依赖。真实模型命令要求本机 Ollama 服务和模型；下载及运行会占用磁盘／显存。不会修改产品 `.env`，测试数据库使用临时目录。

```powershell
.venv\Scripts\python.exe -m pytest tests labs/backend_agent_audit -q
.venv\Scripts\python.exe labs/backend_agent_audit/query_experiment.py --sizes 10 100 500 --repeats 7
.venv\Scripts\python.exe labs/backend_agent_audit/agent_experiment.py --model qwen3:4b --repeats 2
.venv\Scripts\python.exe labs/backend_agent_audit/topology_experiment.py --model qwen3:4b --repeats 2
.venv\Scripts\python.exe labs/backend_agent_audit/role_partition_experiment.py --model qwen3:4b --repeats 2
.venv\Scripts\python.exe labs/backend_agent_audit/internal_experiment.py --model qwen3:4b --repeats 1
.venv\Scripts\python.exe labs/backend_agent_audit/role_schema_probe.py
.venv\Scripts\python.exe labs/backend_agent_audit/current_graph_probe.py --model qwen3:4b --profile ollama
.venv\Scripts\python.exe labs/backend_agent_audit/live_workflow_probe.py --scope graph --model qwen2.5:7b --profile ollama_json --steps 10 --output 1536 --calls 48 --seconds 600
.venv\Scripts\python.exe labs/backend_agent_audit/summarize_live.py
.venv\Scripts\python.exe labs/backend_agent_audit/sqlite_writes.py
.venv\Scripts\python.exe labs/backend_agent_audit/capacity.py
.venv\Scripts\python.exe labs/backend_agent_audit/summarize.py
```

新模型／查询／完整图／写入轮次使用 UTC 时间目录；若缺少某组原始目录，先运行对应实验，再汇总。`protocol_probe.py` 的首轮是固定输出路径，不要无备份重复运行覆盖原始记录。`capacity.py` 是确定性公式输出，可复算覆盖。

## 复现约束

- 最新脚本默认是 v2 字段契约、think=false；历史 v1／v3 的精确脚本与案例在对应轮次 `source_bundle`／`source` 中，按 manifest 校验 hash。不能用最新默认重跑后声称复现旧协议。
- v3 复算需 `--derive-presence --policies one_pass --repeats 2`，单独比较字段消融；不是新架构的公平胜利。
- Query 基线依赖本地 Git 的 `e0fa85a` 对象；它是本仓库可信代码，不从外部执行不明脚本。候选调用当前代码，改动后必须另开轮次，不延用旧成绩。
- seed、温度和模型 digest 已记录，仍可能受运行时／硬件非确定性影响。重复两次不是增加独立场景数量；本轮只有 12 个不同案例。
- 同一总生成上限并不等于相同实际 token 或相同费用。三调用阶段串行运行，不用于推断云端并行时延。完整产品图的兼容／原生接口诊断另有思考设置差异。
- 所有异常进入记录与分母，不跳过不利案例。没有实际价格、功耗和独立标注时，对应结果留空。汇总不含原始 trace；核验完整请求需本机原始目录。
