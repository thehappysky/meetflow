# 后端、前端与数据库：当前完成度与联调核验

后续真实模型工具链修复见[docs/31](31-live-workflow-repair.md)。本页保留44471e0之前核验的93项回归／原模型状态，不把后续结果混入当时批次。

核验日期按客户端为2026-11-13。当前代码基线9aa8c15，本轮无产品代码更改，只重新构建／核验并补充说明。原始结果在忽略目录`test-results/backend-status-20261113`；不上传凭证、数据库、原始录音或完整接口业务响应。

## 1. 能否跑通，必须分四层回答

| 层次 | 结论 | 证据范围 |
|---|---|---|
| 业务应用 | 主要链路已实现、前后端接通、本地可演示 | 账号、个人区／中心、客户跟进、报告、质检、维护；当前提取与评分是规则模式 |
| 真实ASR文件链路 | 已有真实录音上传、转写、时间戳与回听成果 | 原24段修复后走通；本轮读取已有29段样本及音频，未重新运行ASR |
| 真实LLM完整协作 | 尚未通过真实模型端到端验收 | 默认模型未配置；既有低预算完整图／单角色诊断失败不隐去，不扩展为所有正常预算都必失败 |
| 企业上线 | 尚未完成 | 没有生产负载、跨进程领取、正式DB迁移／备份恢复、部署治理及业务质量验收 |

因此可以展示“真实账号与持久数据驱动的业务系统”，不能展示为“已经可稳定收费使用的完整AI系统”。单家公司20–100销售的目标负载仍需验证，页面与CRUD完成不替代容量和质量结果。

## 2. 后端实际是什么

- Python3.12、FastAPI、Pydantic、HTTPX；SQLAlchemy＋SQLite WAL。
- LangGraph＋SQLite检查点，单进程两个本地执行器，会谈／质检各1 worker；图内有限并发与多会议吞吐分开。
- faster-whisper／CTranslate2／PyAV，本机ASR可用；说话人自动分离未完成，未知身份不自动认定为销售。
- Vue前端构建到frontend/dist，由Uvicorn同源提供页面／API／下载；账号页面轮询状态。
- python-docx和ReportLab按已确认内容生成Word/PDF。

不是已部署PostgreSQL、Redis、Celery、对象存储或WebSocket。框架、模型适配存在也不意味着模型业务输出达标。

部署图及页面/API/模块/表的对应矩阵已直接放在[README](../README.md)。后台接口实现在[api.py](../backend/meetflow/api.py)注册；默认账号页入口[WorkspaceApp.vue](../frontend/src/WorkspaceApp.vue)，统一请求[api.ts](../frontend/src/api.ts)。旧App／SalesWorkspace中仍保留历史代码，不把它当作默认账号销售质检入口。

## 3. 本轮重新验证了什么

### 自动化与构建

`python -m pytest tests -q`：**93项产品回归通过，112.11秒**，Junit在product-tests.xml。覆盖邀请码绑定／注册并发、独立会话、公司／团队／本人隔离、个人修改不覆盖中心、版本更正、报告确认及导出、维护恢复、质检权限与披露。部分测试使用规则Provider或注入适配器，不把93项称为真实模型／ASR质量成绩。

前端执行`npm run build`，vue-tsc与Vite生产构建通过，1955模块。产物是index.html、index-DKb8_Mny.css、index-JhEJ58tG.js；构建通过不单独证明全部交互已验收，下面另核实实际页面及服务。

README现有7张Mermaid图均通过11.12.0语法解析；本轮新增部署／数据关联2图，未另做新图视觉渲染验收。首次纯Node解析因缺DOM失败，初始结果保留；仅在忽略目录临时补jsdom后通过，未新增产品依赖。图与文字中的本地代码链接另做存在性检查，历史折叠材料保留原文。

### 正在运行的服务与32项HTTP读取核验

- health为ok，auth_enabled=true，audio_available=true，live_available=false；根页面HTTP200，实际Vue静态产物存在。
- 用已有合成销售／主管／老板账号临时登录，仅做业务GET并退出临时会话；未重置账号或改变原浏览器会话。
- 每角色读取个人列表、中心、更正、岗位摘要、商机、跟进、回收站，以及质检／个人报告的允许或拒绝响应。销售质检403、主管／老板个人报告403，都是符合角色边界的预期结果。
- 主管、老板直接访问销售个人录音均404；主管质检所有subject.team_id匹配其团队，评分主体均有关联账号。主管5场、老板6场是合成公司当前可见质检范围，不是生产样本量。
- 已有真实音频及29段原文读取成功，本轮没有把GET音频当作重新完成ASR。
- 已确认报告导出docx和pdf均200；Word包有word/document.xml，PDF有有效文件头，两种响应X-Report-Content-ID一致。文件保存在本地existing-report.docx／pdf；本轮未重做内容盲评或Word逐页视觉验收。

32是记录下来的列表／权限／音频／导出检查项，包含预期403／404；不是32条新业务数据，也不是32个真实会议质量样本。原始脱敏状态在runtime-verification.json，凭证与令牌没有打印或存入该摘要。

### 用户本地Chrome三身份

使用用户已有三个Chrome标签页，刷新构建后核验，无内置浏览器替代：

| 身份 | 实际查看 | 观察结果 |
|---|---|---|
| 二组销售乙 | 个人列表、公开录音全文、已确认周报 | 6场个人会谈；全文29段，归属／版本／回听入口；报告有固定内容ID与Word／PDF按钮，无质检入口 |
| 二组主管 | 团队工作台、质检列表／卡片／原因／证据 | 5场范围，负责销售明确；50分条目可依次展开原因和原文依据，不自动提交改判 |
| 测试老板 | 公司工作简报／此前质检概览 | 公司工作台有两团队已上报摘要，质检6场；没有主管改判按钮 |

截图ui-sales.png／ui-manager.png／ui-boss.png和ui-errors.json留本机；采集期三页error日志均空。经理证据展开是只读，未提交评分、报告新版本或上报。当前截图是已有合成数据／规则演示，不冒充真实业务质量。

本轮不是全浏览器端到端重跑：没有新建邀请码／注册账号、重新上传音频、更正／删除或重新确认报告。写入与并发流程由隔离93项回归覆盖，之前浏览器实操见docs/18—24；把这几类证据分别说明，避免夸大“全都重测过”。

## 4. 数据库确实落盘了吗，如何关联

本轮以SQLite只读连接检查实际配置的主库：

| 项目 | 实际结果 |
|---|---|
| 主库 | data/meetflow.db，SQLite |
| 持久日志模式 | WAL |
| 非内部业务表 | 34，与SQLAlchemy模型表集合一致 |
| 数据库外键 | 70 |
| 模型业务唯一约束 | 17，不把所有主键索引都算进去 |
| PRAGMA quick_check | ok |
| PRAGMA foreign_key_check | 0条违例 |

schema见[database.py](../backend/meetflow/database.py)。应用创建连接时启用外键；本轮只读完整性检查不等于测过所有并发写入或跨公司组合约束。

| 数据族 | 主要表／文件 | 关联与保存策略 |
|---|---|---|
| 组织和登录 | access_companies、access_teams、access_accounts、access_sessions、access_invitations | 账号绑定公司与团队，邀请绑定团队、有效期和使用状态；密码哈希、登录令牌摘要 |
| 个人会谈 | workspace_recordings、runs、sources、reports | 录音绑定上报销售账号；Run冻结输入与来源版本；Source保留片段ID；音频另存受保护文件 |
| 中心与更正 | workspace_submissions、workspace_corrections | recording_id唯一上报、revision、snapshot及history；主管确认新版本，旧版不被个人修改覆盖 |
| 客户推进 | workspace_customers、workspace_opportunities、workspace_followups | 稳定客户商机ID，来源和版本；部分ID保存在JSON／多态字段，由应用检查 |
| 个人报告 | workspace_sales_reports | 草稿、父版本、确认时间、固定内容及来源指纹；确认后导出同一内容ID |
| 管理质检 | workspace_quality_cases、sales_analyses、workspace_quality_reviews | 来源ID／版本、上报销售、分析快照、主管actor_id与复核版本分开，复核不改被评人 |
| 恢复与追踪 | audit_log、run_events；两份checkpoints数据库 | 保存操作／调用、父图检查点和完整产物；不声称角色每个工具步骤可恢复 |

README另有简化业务关联图。实线和虚线都不能被误读成全量物理外键承诺；特别是QualityCase.source_id、Followup.source_id及JSON内opportunity_id等，需要应用级来源、版本与范围检查。冻结全文有重复副本，尚未迁移为统一不可变来源引用。

### 历史失败为何仍在数据库

本轮主库运行状态快照：5条completed、37条waiting_review、24条failed，均为demo规则模式。质检分析为3条reviewed、17条waiting_review，同样是demo。waiting_review通常等待人工确认，不等于执行失败。

24条failed的运行ID与data/benchmarks/results.initial-av19.json逐项完全匹配，错误均是PyAV19与faster-whisper的metadata_errors参数不兼容；修复后results.json中另一批24条运行均为waiting_review／completed，当前安装av16.1.0。详见[首轮音频测试](15-real-audio-benchmark.md)。旧失败没有删除、改成成功或自动重复消费预算；不把它们当作当前用户6场个人会谈全部故障，也不隐去主库仍有失败的事实。

### 数据库尚未完成的工程工作

- 目前主要create_all增表，未建立完整Alembic迁移链；不代表旧库任意字段变更可无损自动升级。
- 尚未做正式备份恢复演练；应用回收站／版本恢复不是数据库级备份。
- 未实测PostgreSQL与分布式任务领取，不把本地WAL探针当作20–100销售容量保证。
- 生产仍需验证队列租约、重启重放、重复领取、磁盘与音频保留、长会谈以及模型／ASR质量。

## 5. 现在怎么演示、什么还不能演示

启动仍为根目录 `.\scripts\start.ps1 -SkipInstall`，访问http://127.0.0.1:8765。保留原库和账号；首次初始化方法在README，不清库重建。现有三个Chrome页分别停在销售已确认报告、主管质检证据、老板公司工作简报。

可演示账号与团队范围、会谈全文与版本、中心证据、客户跟进、规则质检渐进披露、已确认报告导出。若展示自动提取／分数，要明确规则模式；真实ASR有误识别，人员仍可能未知。不能演示为真实模型稳定分析、成熟销售评分、自动说话人识别或已完成企业上线。
