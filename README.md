# Mini-Harness

Mini-Harness 是一个用 Python 实现的轻量级 Coding Agent 运行时与评测框架。它通过 DeepSeek 流式 API 驱动模型在代码仓库中搜索、编辑和验证，并记录每轮工具调用，便于复盘任务执行过程。

- **Agent 运行时**支持单任务执行和交互式会话，按轮次和时间预算管理工具调用。
- **工具调度**提供工作区路径检查、文件写入前的读取状态与内容哈希校验，以及 Shell 和 Docker 工具。
- **评测流程**支持导出 Git 补丁、保存执行轨迹，并复用已有补丁独立判分。

## 安装与运行

Python >= 3.12。使用 `uv sync` 安装项目；现有虚拟环境可直接运行：

```powershell
.venv/Scripts/python.exe -m mini_harness.main --help
.venv/Scripts/python.exe -m mini_harness.main --task "在 sandbox 中创建 hello.py"
.venv/Scripts/python.exe -m mini_harness.main
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

在 `.env` 配置 `DEEPSEEK_API_KEY`，可选 `MINI_HARNESS_MODEL`。离线测试和 `--help` 不需要 key。
默认 task 模式拒绝 shell、Docker、子 Agent；`--allow-risky` 明确开放这些工具。
交互模式逐次询问，`/exit` 退出，`--session NAME` 选择历史。
`--workspace PATH` 指定读取根目录，常规写入限制为该目录的 sandbox。
`--max-seconds` 指定运行预算，`--history-dir` 指定日志目录。
`--telemetry-out FILE` 另存结构化结果。任务模式每次使用独立 UUID。

## 本地测评

```powershell
.venv/Scripts/python.exe -m mini_harness.bench bench/tasks.json --output bench-results/demo
.venv/Scripts/python.exe -m mini_harness.bench bench/tasks.json --output bench-results/demo --resume
```

这些命令会调用真实模型并产生 API 费用；离线回归使用模拟模型，不消耗 API。
示例包含修复函数和创建文件两题。JSON 数组中每题包含：

- `id`：唯一英文/数字/下划线/连字符标识。
- `task`：模型任务描述。
- `files`：相对路径到初始文本的映射。
- `verify`：独立 Python 验证脚本，退出码 0 才算通过。
- `max_seconds` / `verify_seconds`：默认 120 / 30 秒。

测评工作区整棵树允许文件工具写入。验证脚本在工作区外保存，执行时 cwd 为该题工作区。
每题保存模型日志、验证日志、文本差异和结果；汇总为 `summary.json`、`results.json`。
运行完成不等于通过；Agent 错误状态与验证结果分别保存。
断点续跑核对 manifest、源码哈希与主要模型配置，复用已有最终结果。
中断的未完成题目使用新目录重跑，保留旧产物；重跑已完成失败题应使用新输出目录。

## SWE-bench Verified

`bench/sb_cli.py` 是正式数据集入口。Harbor 在单独的任务容器内安装本项目，
以 `/testbed` 为工作区运行 Agent，允许仓库内写入和容器内命令，并用临时 Git 索引导出补丁。
每题保存 `agent/prediction.json`、模型日志、遥测和轨迹。此流程先生成预测，云端判分由
`sb-cli` 执行。

从项目根目录运行。先预览单题配置，预览无需 API key 或容器：

```powershell
uv run --no-sync python -m bench.sb_cli generate --environment docker --job-dir bench-results/swe-smoke --instance-id django__django-11099 --max-seconds 300 --dry-run
```

实际运行需要安装 `bench/requirements-sb-cli.txt` 中锁定的评测依赖，在同一环境的
PATH 中提供 `harbor` 和 `sb-cli`，并配置可用的 Docker。也可使用已配置的 Modal 后端。
模型默认使用项目 `.env` 中的 `MINI_HARNESS_MODEL` 和 `DEEPSEEK_API_KEY`。
如需其他端点，用 `--model`、`--provider`、`--base-url` 和 `--api-key-env` 指定。

```powershell
uv pip install -r bench/requirements-sb-cli.txt
.venv/Scripts/Activate.ps1
python -m bench.sb_cli generate --environment docker --job-dir bench-results/swe-smoke --instance-id django__django-11099 --max-seconds 300
python -m bench.sb_cli export --job-dir bench-results/swe-smoke
python -m bench.sb_cli submit --predictions bench-results/swe-smoke/predictions.json --run-id swe-smoke-01 --dry-run
```

移除最后一条命令的 `--dry-run` 会向云端发送补丁并判分，需要配置 `SWEBENCH_API_KEY`。
`run` 子命令可串起生成、汇总和提交，建议先审阅单题预测文件。

### 批量评测记录

使用 DeepSeek V4 Flash 在固定的 **30 题 SWE-bench Verified 子集**上验证了批量流程。题单覆盖 Django、SymPy、pytest、Sphinx、scikit-learn 和 Matplotlib，每个仓库 5 题。每题 Agent 的预算为 **50 轮、300 秒**，同时运行两个任务。

评测分为补丁生成和本地判分两步。生成阶段保存 Git 补丁和运行遥测，判分阶段在独立任务容器中应用已有补丁并执行测试。判分可以单独重跑，复用补丁即可，无需再次调用模型。

本轮实验在固定的 30 题子集上成功导出 29 份预测，本地判分通过 28 题。按完整题单计算，观察到的通过比例为 **28/30（93.3%）**。这是一轮允许联网的子集实验结果。完整题单、统计口径和失败类别见 [评测结果](docs/benchmark_results.md)。

批量脚本默认从项目目录读取数据。使用 `python -m bench.batch30 status --root <数据目录>` 可以检查已有数据目录中的镜像准备进度。数据目录下需要有 `verification/verified30/manifest.json` 和对应的 `verification/verified30/tasks/`。首次生成题单还需要 `verification/verified-source/` 中的 Verified 任务，以及 `verification/swe-offline-uv/django__django-11099/environment/` 中预先准备好的 Dockerfile、Linux `uv`、`uvx` 和项目依赖配置。结果写到该目录下的 `bench-results/`。

## 实现边界

- 本地测评是受信任任务的目录隔离，不是操作系统安全沙箱；启用宿主 shell 后能访问宿主权限范围。
- 上下文裁剪保留首个目标、完整近期工具调用组和旧记录摘录；完整轨迹另存。摘录会丢失细节，必要时重新读文件。
- 总预算覆盖轮次、工具前检查、API timeout、重试等待、子 Agent；同步 API 的网络超时和清理可能导致小幅超预算，不是硬实时保证。
- token 数来自服务端 usage；连接中断且未返回 usage 的计费无法精确恢复。子 Agent token 计入总量，工具次数为主 Agent 层级。
- Docker 工具要求本地 Docker 和预拉取的 `python:3.12-slim`，Windows 使用容器 UID 1000；挂载权限需实际环境验证。
- 本地测评生成的 `changes.diff` 用于审阅。SWE-bench 使用独立导出的 `prediction.json`。

## 审核入口

`agent.py`：循环、预算、历史、上下文裁剪、token 统计。
`tool/tool_manager.py`：路径检查、读后写状态、审批策略、错误分类。
`process.py`：输出限额与超时清理。
`bench.py`：任务加载、独立判分、汇总与续跑。
`tests/test_runtime.py`：无需网络的回归验证。

## 代码来源

Agent 运行时和工具调度是本仓库的实现。`bench/adapter.py`、`bench/atif.py`、`bench/sb_cli.py` 和 `bench/sb_cli_adapter.py` 基于 [Mini-Harness 上游项目](https://github.com/mini-harness/mini-harness) 的评测代码适配。本仓库调整了容器安装、Agent 启动及轨迹读取，并增加了批量评测和补丁重放。
