# 转写候选C2：先比较，再决定部署

2026-11-14，实施前登记。CPU Whisper small在24段/8场公开远场会议的诊断CER约47%，更多Agent不能修复原文错误。继续查原始实现并选一个可用同CPU/同协议测量的转写候选，避免只优化提示词。

依据：本项目固定faster-whisper 1.2.1的[模型映射源码](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/utils.py)支持large-v3-turbo；其映射原mobiuslabsgmbh仓库目前重定向至[转换权重发布页](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo)。该页只证明CTranslate2格式可加载，不证明在本项目更准确/更快。

冻结转换权重revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`；model.bin 1617884929字节，LFS SHA256 `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`。本地单独目录，不替换small、不执行仓库代码；准备脚本核对内容hash和来源。

沿用原24片段与原参考文字，CPU int8、默认语言检测、VAD、word_timestamps、原归一化/编辑距离算法；不运行管理评分，不加入提示热词，不改金标准。对比加权CER、逐片段胜负、失败数、时间戳边界、耗时/RTF。24段相关于8场非销售录音，不是独立客户试验，也不是官方AliMeeting打分。先前small轮已保留，增加候选不改变原失败。

计时必须在9B与其他高负载测试结束后运行；小模型旧基线有背景负载，耗时是开发机诊断，不宣称算法净加速。只有全部片段产出、时间点合法、整体CER至少降低5个百分点且耗时不超过旧轮2倍，才考虑本机开发默认候选；这是本轮工程筛选门槛，没有统计显著性含义。通过后仍须跑真实TCP音频→多Agent→导出，不能用ASR单测替代。未通过则保留现配置并记录原因。


可复现命令（只写本机忽略目录，不改主配置）：

```powershell
.venv\Scripts\python.exe scripts/prepare_asr_turbo.py
.venv\Scripts\python.exe scripts/compare_speech.py --backend whisper --asr-model data/benchmarks/models/faster-whisper-large-v3-turbo
```

本轮脚本补齐每段实际音频SHA、Whisper实际权重SHA与时间戳范围检查；旧C1脚本manifest列的是SenseVoice下载assets，即使该轮跑Whisper，不能把旧assets列表当成Whisper权重证明。旧source_bundle已含audio.py和Whisper本地配置，下载源见data/benchmarks/downloads.json，本次额外逐文件校验，不回填旧manifest。


## C2完整结果与配置决定

24/24均转写完成、时间戳全部合法，同参考JSON hash。加权诊断CER：small 47.4119% → Turbo 44.0160%，降低3.3958个百分点；累计耗时569.05 → 1211.05秒，约2.13倍。没有满足事先登记的至少5个百分点改善及最多2倍耗时两项门槛，因此本机开发默认仍用small。Turbo源码/权重/24条原始转写全保留，可显式配置作后续研究，不删除有改善的局部结果。

原始目录 `test-results/speech-comparison/20261114T040458064185Z`；公开逐例数值见[续验汇总](../labs/backend_agent_audit/results/continuation_summary.json)。这不是对所有硬件/真实销售录音的模型排名，更不能把约44%的字符差错说成达到商用准确率。没有独立业务录音标注和生产容量实测，当前只完成受限诊断与配置筛选。因为未采用新转写模型，不把单项测试冒充新模型端到端通过；接下来用既定small配置复验实际业务功能修复。
