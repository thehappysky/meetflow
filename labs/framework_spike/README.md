# LangGraph 运行时机制验证

2026-10-24 在 Python 3.13.9、LangGraph 1.2.16、SQLite checkpointer 3.1.2 下运行，六项检查通过。完整依赖见 [requirements-lock.txt](requirements-lock.txt)。

实验程序：[verify_runtime.py](verify_runtime.py)。节点由脚本驱动，没有调用 LLM、ASR 或外部项目平台。这不是完成的 Agent 系统，也不是模型效果测试。

验证内容：

1. 动态 Send 派发，两次调用同一子图的局部状态隔离。
2. 没有 reducer 的并行字段写入触发 InvalidUpdateError。
3. 不可变产物 reducer 接受重复同值、拒绝同键异值。
4. 不同深度的固定分支通过显式 barrier 汇聚。
5. 关闭并重建 SQLite saver 和图后恢复中断，节点重新进入。
6. 应用层拒绝版本不匹配的审批。

第 5 项没有杀进程或模拟 PostgreSQL 故障；第 6 项只演示报告版本检查，生产还要检查操作集合 hash、权限、源任务版本和并发。

复现：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe verify_runtime.py
```

在这个目录运行。其他系统使用相应的虚拟环境 Python 路径。测试创建临时 SQLite 文件，测试结束自动清理；没有真实业务副作用。
