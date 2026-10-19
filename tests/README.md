# Tests

测试目录与标签遵循 [testing.md](../docs/development/testing.md)（2026-09-28），并与 [layout.md](../docs/code/layout.md) 一致。

## 原则

- **镜像源码**：`tests/rpipe/` ↔ `src/rpipe/`。不设 `tests/unit/`、`tests/e2e/` 一级目录。
- **公共数据**：`tests/_data/` 放测试输入，不镜像源码。包含三份 Study 声明（`mnist_train_size`、`mnist_native_vs_hf`、`cifar_grid`），用例复制到临时目录再跑；`recovery_worker.py` 是跨新进程恢复的微小离线计算输入，不依赖正式 Study。
- **层级是标签**：每项测试恰好一个 `unit` / `integration` / `e2e`。
- **强制标签**：Level × Objective（`location`|`content`|`physical`）× Priority（`p1`|`p2`|`p3`）。
- **成本与结果**：恰好一个 `cost(cost_class=...)`，恰好一个 `result_type(..., detail=...)`。缺则收集失败。
- **e2e**：系统入口是 `flow/cli.py`，用例放在 `tests/rpipe/flow/`，标签为 `e2e`。集成和端到端用例的文档字符串写明起点、终点和预期。

## 排除不镜像项

`__pycache__/`、`.pytest_cache/`、`.egg-info/`、`.tmp/`、虚拟环境、Study 生成的 `runs/` / `shared/` / `scripts/`。

## 已登记 markers

强制：`unit`、`integration`、`e2e`、`location`、`content`、`physical`、`p1`、`p2`、`p3`。

元数据：`cost`（`c1`–`c4`）、`result_type`（`categorical`|`numeric`，`detail` 为 `summary`|`metrics`|`samples`）。

架构层：`structure_layer`、`flow_layer`。

模块：`module_api`、`module_control`、`module_data`、`module_model`、`module_algorithm`、`module_system`、`module_artifact`、`module_make`、`module_cli`、`module_runner`、`module_process`。

资源与执行特征：`runtime`、`memory`、`gpu`、`slow`、`external`、`flaky`、`quarantined`。

## 预算起点

C1：≤ 1 s 且 ≤ 256 MiB，无真实 GPU、无外网。C2：超出 C1，≤ 60 s 且 ≤ 2 GiB。C3：真实 GPU 或超出 C2。C4：批量基准。真实 GPU 同时标 `gpu`。

## 执行入口

```text
python tests/run.py --fast
python tests/run.py --core
python tests/run.py --all --cost-class c1 --cost-class c2 -- -m "(integration or e2e) and not external and not gpu and not slow"
python tests/run.py --level unit --priority p1 --cost-class c1
python tests/run.py --cost-class c3
python tests/run.py --all
```

每次运行写入 `.tmp/test-results/<run_id>/manifest.json`、`events.jsonl` 和 `report.md`。

事件保存 setup/call/teardown，以每次 setup 区分同一节点的尝试。最终筛选后的节点清单写入 manifest。报告按用例和尝试合并阶段：缺少 teardown 记 incomplete，完整尝试中任何阶段失败均覆盖 call 通过；未开始的选中节点记 not_run。跳过原因及所有失败诊断保留，数值型用例的 call 测量不会被成功清理覆盖。会话退出状态写入原 manifest。

统一入口默认整轮期限为 1800 秒（与现有 CPU CI 作业上限一致），可用 `--timeout <秒>` 指定有限正数。正常退出、超时或中断时均收尾本入口拥有的进程，防止主 pytest 已退出而 worker 继续持有输出管道。Windows 使用私有 Job Object：子进程先挂起，归属 Job 后恢复，禁止 breakaway；Linux 使用独立进程组与 /proc 活跃观测，等待时以 waitid/WNOWAIT 保留组长身份至收尾后再 reap，收尾范围为该组（主动另建会话的后代不在组内）。仅支持 Windows 与具备 /proc、waitid/WNOWAIT 的 Linux；其他平台在启动子进程前明确拒绝，不把组长僵尸误当仍活跃的 worker。清理后的实际退出码、活动进程数与确认状态保存到该轮 execution.json；清理未确认时保留错误，不声称资源已释放。整轮期限触发后清理另有有限的 10 秒阶段期限。私有实现见 [_process_tree.py](_process_tree.py)，不依赖模型或额外包。直接 `python -m pytest` 的期限仍由外层负责。

manifest 与 execution 使用同目录临时文件及原子替换；更新中断保留上一份完整记录与未完成临时文件。首次启动未取得 manifest 时，execution 明确记录 manifest_available=false。诊断输出失败也记录在 execution，不替代超时、中断或已有非零退出。

原进程结束后，可用 `python tests/run.py --report-only .tmp/test-results/<run_id>` 从原 manifest/events/execution 重建报告，不重新执行测试或改写事件；缺少会话退出状态时明确显示 unknown。旧事件没有完整 phase/收集清单时仅保留原结果，不补造完整性证据。

未显式传入 `--basetemp` 时，pytest 临时根使用本次结果目录下独立的 `pytest/`，不再共享 `.tmp/pytest`；显式路径保持，实际路径写入 manifest。并行调用因此不会清理另一调用的临时输入。

统一入口为 pytest 子进程显式传递当前环境和 stdout/stderr；调用方捕获入口输出时，失败诊断与退出码仍可取得。该合同由 [test_run_entry.py](test_run_entry.py) 的真实轻量子进程回归覆盖。

`--core` 选择 C1/C2 的 unit，排除 `external`、`gpu`、`slow`。新增流程命令选择 C1/C2 的 integration/e2e，并应用同样的排除项；覆盖本地 Flow 执行、错误结果、Study 汇总和版本隔离。参数分隔符 `--` 后的选项直接传给 pytest。

## CI 与验收范围

[Unit Tests](../.github/workflows/unit-tests.yml) 保留 core 门，并在 Ubuntu/Windows × Python 3.10/3.13 四个环境运行上述 CPU 流程门。每个提交是否通过以其对应 Actions 结果为准。当前两个 e2e 均含 `external`，因此这条门目前选择本地 integration；今后符合条件的 e2e 自动纳入。

CI 安装 `.[dev,tensorboard]` 与 workflow 固定的 SDK 测试版本，未安装完整 `hf` 可选组；HF 真实 Trainer 用例可能跳过。GPU、下载依赖与慢任务不属于此门。需要验证这些能力时，按对应 Study 计划单独执行；已有复现实验只证明各自声明的配方与预算。

[Package Check](../.github/workflows/package-check.yml) 在 Ubuntu/Windows 构建 wheel / sdist，再分别创建环境、正常安装依赖和生成包，从源码目录之外运行 [package_smoke.py](package_smoke.py)：模块入口与 console script、离线 Toy/Stub Study 的 make→launch→process→status/logs/report、四个 Run 与两个 Experiment、已成功 Run 的重复 launch 跳过。Stub 验证的是安装及产物合同，不代表真实数据训练精度。各提交的远端结果以对应 Actions 为准。

两种工作流均设置 `MPLBACKEND=Agg`，在无显示的 CI 环境中生成静态图，避免依赖运行器的 Tk / Tcl 安装；测试选择与绘图坐标、计数等断言保持不变。

手动验收时，用已安装分发包的 Python 运行 `tests/package_smoke.py --workspace <全新目录>`。程序拒绝从 `src/` 导入 rpipe，拒绝覆盖已有工作区，并保留每条命令的 stdout、stderr 与退出码。

测试收集成功、筛选批次通过、安装验收和科学结果判定分别记录。`.tmp/test-results/` 为本机证据，不随 Git clone 提供；收集阶段的 exit 0 不表示测试已执行，筛选批次通过也不表示全支持范围通过。


B143/#198 的 [test_origin_sdk.py](rpipe/structure/system/test_origin_sdk.py) 使用实际 HF snapshot/file/redirect/cache、datasets 原生对象和 requests/httpx/aiohttp 栈，HTTP 响应与媒体 backend 为合成，禁止真实模型/数据下载。CPU CI 安装本批已核对的可选 SDK 测试版本，确保回归实际执行；这些测试不以 mock 成功声称国内服务可用，Audio/Video/PDF 的文件协议检查不代表 C++/FFmpeg/pdfplumber 实际解码。spawn/default pool/线程与延迟消费所有权分别覆盖，临时失败和 XML 留 .tmp，不随 clone。
