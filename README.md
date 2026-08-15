# MeetFlow：从会议录音到纪要与待办

当前目标是会议驱动的多智能体项目协作系统：结合录音、历史会议、需求文档与任务状态，完成调查、规划、风险核查、人工审核、任务执行与后续跟进。

**当前为 v0.1 本地单用户可运行版本。** 已实现 FastAPI、Vue 页面、真实 LangGraph 协作执行、持久审核、本地任务与 Markdown 导出。默认离线模式是明确标记的规则模拟；真实模型接口已有模拟传输测试，尚未实测真实模型效果。音频需另行配置本地 ASR，尚未完成说话人分离。

[开发进度](PROGRESS.md) · [变更记录](CHANGELOG.md) · [实际实现范围](docs/12-implementation-status.md) · [提交日期说明](docs/COMMIT_METADATA.md)

## 快速运行

需要 Python 3.12+、Node.js 22.12+（推荐 24）。离线体验无需 Docker、Redis 或模型密钥。

Windows PowerShell，在仓库目录执行：

```powershell
.\scripts\start.ps1
```

首次创建虚拟环境、按锁文件安装依赖、构建页面并启动服务。打开 **http://127.0.0.1:8765**。后续依赖/前端没有变化时使用 `.\scripts\start.ps1 -SkipInstall`。前台终端按 Ctrl+C 停止服务。

macOS/Linux 或手动启动：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
cd frontend
npm ci
npm run build
cd ..
python -m uvicorn meetflow.api:app --host 127.0.0.1 --port 8765
```

Windows 手动运行时使用 `.venv\Scripts\python.exe`。本机原有 `.venv-framework-lab` 不是他人运行的前提。

## 使用路径

1. 点击“体验示例”，或新建会议粘贴文字/读取 `.txt`、`.md`。
2. 确认实际会议时间；未知可留空，相对日期保持待确认。
3. 查看纪要，点击原文核对；在任务变化中修改标题、负责人、日期。
4. 集中回答疑点，确认明确范围的任务变化，再进入看板跟进。
5. 下载含候选、实际确认字段和来源依据的 Markdown。
6. 项目资料一次导入后复用；已确认任务形成历史资料，未完成任务可生成下次议程。

示例是合成文字资料，没有真实录音。离线提供者使用规则模拟工具选择与结果生成，通过同一个 LangGraph 和审核链路；它不是 LLM，不能用于证明语义准确率。fixture 的 expected_findings 不传给 Agent。

## 真实模型配置

复制 `.env.example` 为 `.env`，填写服务端模型地址、密钥和名称：

```dotenv
MEETFLOW_MODEL_BASE_URL=https://your-provider.example/v1
MEETFLOW_MODEL_API_KEY=your-key
MEETFLOW_MODEL_NAME=your-tool-capable-model
```

重启后可选择“真实模型”。当前适配 **Chat Completions function tools**，模型需支持工具调用、`max_completion_tokens` 和工具消息往返；供应商不一定兼容，尚未支持 Responses 等其他协议。未配置或失败时明确报错，不自动转演示结果。

密钥仅在服务端读取、不返回浏览器、不提交仓库。真实模式使用所选服务的额度；有调用/迭代/时间上限，但调用次数不是精确费用上限。参考 [OpenAI function calling 官方文档](https://developers.openai.com/api/docs/guides/function-calling)。

累计调用数持久保存；时间限制按本地 runner 每次活跃执行计算，人工审核等待不计入。重建进程会开启新执行时间窗，尚无跨进程累计时间配额。

## 可选录音转写

```bash
python -m pip install -e ".[audio]"
```

在 `.env` 设置 `MEETFLOW_ASR_MODEL` 为 faster-whisper 本地模型目录或支持的模型名。模型名可能首次下载权重；默认不配置、不自动下载。CPU 默认 int8，其他设备按实际环境验证。

启用后支持录音上传、片段时间戳与证据播放。当前未实现自动区分说话人，姓名和“我负责”需要人工核对。音频管线仅用测试适配器验证，尚未测量真实准确率和速度。

## 验证

```bash
python -m pytest -q
python -m ruff check backend tests scripts/smoke_browser.py
python labs/framework_spike/verify_runtime.py
cd frontend
npm run build
```

前后端分别开发时，后端保持 8765 端口，在 frontend 运行 `npm run dev`，访问 5173，由 Vite 代理 API。

运行服务后，已安装 Playwright 和 Edge 时可执行：

```bash
python scripts/smoke_browser.py --channel msedge
```

也可选择 Chrome，或 `python -m playwright install chromium` 后使用 `--channel chromium`。浏览器脚本会创建明确标记的示例数据，验证证据、澄清、审核、导出、看板和手机布局，结果写入忽略的 test-results。GitHub Actions 包含后端、构建、框架实验和浏览器检查，无需模型密钥；远端结果以实际工作流为准。

当前代码在 backend/meetflow、frontend、tests、scripts；本地数据库/录音在忽略的 data 目录。完整目标中的多用户、PG/Redis/Celery、外部同步、向量检索、自动事件重分析、技能复用和质量评测仍待实现，见 [能力边界](docs/12-implementation-status.md)。

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
