# 后端与 Agent 开发实验

先看 [预登记](PLAN.md)、[架构审计](../../docs/25-backend-architecture-audit.md)、[实测结论与限制](../../docs/26-quantitative-experiments.md)。结果汇总在 [results/summary.json](results/summary.json)，原始记录位于忽略目录 `test-results/backend-agent-audit/`，不把本机数据库、音频、私密请求或密钥提交。

用户已固定最终采用多Agent，单Agent仅实验参照。内部设计、门控和对应结果见[三种多Agent对照](../../docs/27-multi-agent-design-comparison.md)，不据不利开发结果擅自取消多Agent产品。

## 文件与复算

| 文件 | 用途 |
|---|---|
| cases.py | 12 个有明确 gold 的中文合成开发场景；不是独立业务标注集 |
| agent_experiment.py | 本地真实模型、三种调用策略、原始请求响应、逐例失败与指标 |
| topology_experiment.py | 三种多Agent交接／审查机制；逐例至少两个Agent，记录未审查同错与净修复 |
| test_topologies.py | 交接上下文隔离、争议门控、无效产物与一致但错误的计量 |
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
.venv\Scripts\python.exe -m pytest tests labs/backend_agent_audit/test_measurement.py labs/backend_agent_audit/test_topologies.py -q
.venv\Scripts\python.exe labs/backend_agent_audit/query_experiment.py --sizes 10 100 500 --repeats 7
.venv\Scripts\python.exe labs/backend_agent_audit/agent_experiment.py --model qwen3:4b --repeats 2
.venv\Scripts\python.exe labs/backend_agent_audit/topology_experiment.py --model qwen3:4b --repeats 2
.venv\Scripts\python.exe labs/backend_agent_audit/current_graph_probe.py --model qwen3:4b --profile ollama
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
