# RPipe 测试说明

[返回 README](../README.md#参与开发)

<a id="一测试范围与入口"></a>

## 测试范围与入口

### 本地按影响选择

本地预检读取工作树相对已确认基线的差异，包含已提交、暂存、未暂存和未跟踪文件。改名按删除与新增处理。先预览，再执行：

```powershell
python tests/impact.py --base origin/dev
python tests/impact.py --base origin/dev --execute
```

导出快照可重复提供 `--changed` 指定真实改动文件。完整提供差异后，选择才对应本次改动。结果和各测试进程临时目录默认放在系统临时目录，`--results-root` 可指定源码树外的独立根。复用符合要求的现有 Python 环境，原生库仍按环境要求配置。

| 改动范围 | 本地选择 |
| --- | --- |
| Markdown | 检查本地文件引用、编码；删除文档时检查现有入口的入站引用 |
| algorithm、data、model | 三类科学组件测试、Study 接入，以及架构、CLI、原生运行、产物与取消关键链路 |
| 已存在的单个测试文件 | 执行该文件的离线用例 |
| flow、artifact、control、system、Study、测试工具、构建、workflow 或未识别路径 | 完整离线回归，并提示另做产物验证 |

Markdown 预检覆盖文件引用，章节锚点和实际展示沿用文档交付检查。混合改动按包含的最大范围执行。选择与退出码进入 `selection.json`，逐用例 manifest、events 和报告由 `tests/run.py` 保存。

完整离线回归合并原 core 和离线 integration/e2e 的收集与启动，保留所有 c1/c2 的 unit、integration、e2e 用例及静态豁免规则：

```powershell
python tests/run.py --offline --timeout-seconds 1200
```

本地影响入口为小型 CPU 用例设置 OMP、MKL、OpenBLAS 各一个线程。直接调用 `tests/run.py` 采用调用环境。TensorBoard 真实事件读回带 external 标记，有依赖时按既有单项命令验证。发布、构建依赖或公共执行合同变化时，继续执行完整回归、实际 wheel/sdist 安装与严格来源验证。研究实验按研究计划安排。

<a id="自动-ci-本地改稿"></a>

### 自动 CI 选择与证据复用

**选择与证据复用已通过#269合入dev**。实际运行结果从[本轮记录](<../docs/运行记录 Run Record/2026-11-26 本地测试选择与耗时对比.md#真实-ci-与合并后复用>)核对。`tests/ci.py` 为两个工作流选择范围、核验已通过证据，并生成与实际文件树绑定的回执。文档路径只使用 Python 标准库，不安装 PyTorch，也不运行打包任务。

| 触发或改动 | 执行选择 |
| --- | --- |
| dev PR，仅 Markdown | 文档文件引用与编码检查；两个必需汇总门正常完成 |
| dev PR，algorithm、data、model 或已知测试文件 | 受影响离线用例，Ubuntu/Python3.10 与 Windows/Python3.13；改动 src 时另跑两平台严格打包验证 |
| dev PR，共享执行、配置、构建、workflow 或未知范围 | 四组完整离线 CPU 回归及两平台严格打包验证 |
| dev 合并 push，有匹配的成功回执 | 分别复用 CPU、打包的 PR 证据，仍运行文档检查和汇总门 |
| dev 直接 push，或证据、基线、文件树无法匹配 | 完整 CPU 与打包检查 |
| main PR、main push、手动触发 | 完整 CPU 与打包检查 |

dev 复用要求同时成立：本次提交关联已合并至 dev 的 PR；最新对应 PR 工作流成功；回执未过期，仓库、PR、head、目标基线、实际文件树、选择指纹、运行及重跑编号全部匹配。测试合并提交与实际合并提交可以 SHA 不同，但文件树必须相同。回执保留原测试提交与运行身份。API 不可用、回执缺失／过期或新基线不匹配时，恢复完整检查；最新失败或取消不能改用旧绿项。首次采用新工作流时若没有有效回执，同样完整执行。

`Unit tests`、`Build package` 名称保留，`Branch flow` 沿用原配置。工作流不采用 paths 过滤；汇总门使用 `always()`，检查计划成功及被选任务成功，合法未选任务才允许 skipped。失败、取消和意外跳过不能生成成功回执。此设计依据 GitHub 的[任务依赖行为](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs)，实际平台调度与阻断结果由本轮记录分别维护。保护规则保持原状。

完整 CPU 在一个离线批次中执行原 core 与 integration/e2e 集合，TensorBoard 真实读回仍单独运行。两平台 wheel／sdist 安装、严格来源哈希及 CLI 合同保留。发布路径始终完整执行。手动完整矩阵规则已定义，但[GitHub要求默认分支具有workflow_dispatch入口](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。本次不改main，其旧工作流尚未声明该事件，不宣称手动按钮已可用；本地完整入口和按规则选中的完整PR检查继续可用。

本地合同验证：

```powershell
python tests/run.py --offline -- tests/tests/test_ci.py tests/tests/test_impact.py tests/tests/test_run_entry.py
```

已有约27分钟是旧线上 PR 与 dev 两轮的基线；新稿尚无线上耗时实测，不承诺13分钟或文档1分钟。实际结果与采用后的验收项见[运行记录](<../docs/运行记录 Run Record/2026-11-26 本地测试选择与耗时对比.md#自动-ci-本地改稿验证>)。

采用[测试规范 代码](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf)。项目执行命令及资源边界从[README](../README.md)进入，当前整改与验收从[开发计划](<../docs/开发计划 Development Plan 研究流水线.md>)进入。这里维护实际镜像、目标映射与例外，不复制研发计划。

| **镜像位置** | **被测程序及落位** |
| --- | --- |
| rpipe/ | src/rpipe/同级骨架，unit对应源文件或对象，integration对应调用入口，e2e对应外部入口或程序根 |
| studies/main_exp、studies/main_probe | 研究接入源码镜像，数据、算法及阶段按实际目录落位 |
| tests/ | 独立测试程序自身的镜像，即仓库tests/run.py、impact.py、ci.py、conftest.py、package_smoke.py的合同；不将镜像中的用例再次递归镜像 |
| _data/ | 最小输入、子进程脚本和可选真实研究旅程的声明fixture；不是需要再镜像的生产程序 |

rpipe根下test_architecture按独立模块展开静态合同，test_main覆盖外部命令入口。可选MNIST旅程以公共CLI为入口，位于rpipe根，原声明留在_data/studies中，不再建立不存在的独立Study镜像。run_artifacts旅程由flow.run启动，readout_lifecycle以正式读端为验证对象；二者共用合成准备过程，按实际合同分别落位。

<a id="二骨架及排除登记"></a>

## 骨架及排除登记

- src布局外壳不是Python程序层级，镜像从rpipe包开始，包初始化与无独立行为的命名空间不要求空用例
- Study main_exp的structure/api、structure/model仅有初始化/注册接入，未建独立测试目录，实际构建与注册通过recipe及prepare旅程验证。main_probe的flow/collect、execute、summarize目前通过完整合成旅程及paired合同验证，尚无独立目录用例，此处登记骨架缺项，不称独立覆盖已完成
- studies/_template只有声明模板，按模板发布与配置旅程覆盖，不复制科学源码或空测试壳
- 排除__pycache__、build、dist、egg-info、.tmp、虚拟环境、外部依赖、生成输出/缓存、外部证据及旧Run。测试程序的镜像范围只含前述五个执行文件，共享conftest与fixture数据不递归建立镜像

<a id="三逐文件目标索引"></a>

## 逐文件目标索引

下表按实际导入和调用合同定位，模块参数化的每个目标产生独立nodeid。标记及预算沿pyproject.toml和统一执行器，不由文件名推断成本、层级或质量。

| **测试文件（相对tests）** | **主要目标/合同入口** |
| --- | --- |
| `rpipe/flow/test_cancellation.py` | flow.run/supervision合作取消及所列调用路径 |
| `rpipe/flow/test_cli.py` | flow.cli及所列调用路径 |
| `rpipe/flow/test_cli_batch.py` | flow.cli及所列调用路径 |
| `rpipe/flow/test_context.py` | flow.context及所列调用路径 |
| `rpipe/flow/test_exports.py` | flow.exports及所列调用路径 |
| `rpipe/flow/test_exports_curves.py` | flow.exports及所列调用路径 |
| `rpipe/flow/test_huggingface.py` | flow.run/stages及HF实际算法及所列调用路径 |
| `rpipe/flow/test_lifecycle.py` | flow.run/worker/stages及所列调用路径 |
| `rpipe/flow/test_lifecycle_results.py` | flow.run/worker/stages及所列调用路径 |
| `rpipe/flow/test_logging.py` | flow.run/worker与system.logger及所列调用路径 |
| `rpipe/flow/test_native.py` | flow.run/stages原生计算路径及所列调用路径 |
| `rpipe/flow/test_process.py` | flow.process及所列调用路径 |
| `rpipe/flow/test_run.py` | flow.run及所列调用路径 |
| `rpipe/flow/test_run_artifacts.py` | flow.run及所列调用路径 |
| `rpipe/flow/test_stages.py` | flow.stages及所列调用路径 |
| `rpipe/flow/test_study.py` | flow.study及所列调用路径 |
| `rpipe/flow/test_study_recipe.py` | flow.study及所列调用路径 |
| `rpipe/flow/test_supervision.py` | flow.supervision及所列调用路径 |
| `rpipe/flow/test_supervision_cli.py` | flow.supervision及所列调用路径 |
| `rpipe/flow/test_supervision_limits.py` | flow.supervision及所列调用路径 |
| `rpipe/flow/test_worker.py` | flow.worker及所列调用路径 |
| `rpipe/structure/algorithm/test_batch.py` | structure.algorithm.batch, structure.model.architectures |
| `rpipe/structure/algorithm/test_evaluation_support.py` | structure.algorithm.evaluation, structure.algorithm.evaluation_support, structure.algorithm.factory, structure.algorithm.metrics, structure.algorithm.progress, structure.algorithm.tracker, structure.algorithm.train, structure.control |
| `rpipe/structure/algorithm/test_factory_algorithm.py` | structure.algorithm.factory, structure.algorithm.tracker, structure.algorithm.train, structure.control |
| `rpipe/structure/algorithm/test_hf_data_port.py` | structure.algorithm.transformers_trainer, structure.data, structure.data.factory |
| `rpipe/structure/algorithm/test_hf_map.py` | structure.algorithm.hf_map, structure.algorithm.kwargs, structure.algorithm.optim, structure.control |
| `rpipe/structure/algorithm/test_hf_snapshot.py` | structure.algorithm, structure.algorithm.tracker, structure.artifact, structure.control, structure.data.factory |
| `rpipe/structure/algorithm/test_metrics.py` | structure.algorithm.metrics |
| `rpipe/structure/algorithm/test_optim.py` | structure.algorithm.optim, structure.control |
| `rpipe/structure/algorithm/test_progress.py` | structure.algorithm.progress, structure.control |
| `rpipe/structure/algorithm/test_resume.py` | structure.algorithm.evaluation, structure.algorithm.resume, structure.algorithm.tracker, structure.algorithm.train, structure.artifact, structure.artifact.comparison, structure.control, structure.data.factory, structure.system.factory, structure.system.runtime |
| `rpipe/structure/algorithm/test_tracker.py` | structure.algorithm.metrics, structure.algorithm.tracker, structure.artifact |
| `rpipe/structure/algorithm/test_train.py` | structure.algorithm.factory, structure.algorithm.metrics, structure.algorithm.optim, structure.algorithm.tracker, structure.algorithm.train, structure.artifact, structure.artifact.checkpoint, structure.control, structure.data.factory, structure.system.factory |
| `rpipe/structure/algorithm/test_train_checkpoint.py` | structure.algorithm.evaluation, structure.algorithm.tracker, structure.algorithm.train, structure.artifact, structure.artifact.readout, structure.control, structure.data.factory, structure.model.factory, structure.system.factory |
| `rpipe/structure/algorithm/test_train_exact_resume.py` | structure.artifact.checkpoint |
| `rpipe/structure/algorithm/test_transformers_trainer.py` | structure.algorithm.metrics, structure.algorithm.tracker, structure.algorithm.transformers_trainer, structure.artifact, structure.control, structure.system.factory |
| `rpipe/structure/artifact/test_checkpoint.py` | structure.artifact, structure.artifact.checkpoint |
| `rpipe/structure/artifact/test_comparison.py` | structure.artifact, structure.artifact.comparison, structure.control |
| `rpipe/structure/artifact/test_imports.py` | rpipe/structure/artifact对应源码，含隔离子进程导入 |
| `rpipe/structure/artifact/test_io.py` | structure.artifact.io（） |
| `rpipe/structure/artifact/test_io_atomic.py` | structure.artifact.io（atomic） |
| `rpipe/structure/artifact/test_io_atomic_boundaries.py` | structure.artifact.io（atomic_boundaries） |
| `rpipe/structure/artifact/test_io_config_io.py` | structure.artifact.io（config_io） |
| `rpipe/structure/artifact/test_io_diagnostics.py` | structure.artifact.io（diagnostics） |
| `rpipe/structure/artifact/test_io_inputs.py` | structure.artifact.io（inputs） |
| `rpipe/structure/artifact/test_io_json_io.py` | structure.artifact.io（json_io） |
| `rpipe/structure/artifact/test_io_shared.py` | structure.artifact.io（shared） |
| `rpipe/structure/artifact/test_io_shared_references.py` | structure.artifact.io（shared_references） |
| `rpipe/structure/artifact/test_io_staging_names.py` | structure.artifact.io（staging_names） |
| `rpipe/structure/artifact/test_provenance.py` | structure.artifact, structure.control |
| `rpipe/structure/artifact/test_readout.py` | structure.artifact.readout，正式记录及只读行为 |
| `rpipe/structure/artifact/test_readout_curves.py` | structure.artifact.readout，正式记录及只读行为 |
| `rpipe/structure/artifact/test_readout_lifecycle.py` | structure.artifact.readout，正式记录及只读行为 |
| `rpipe/structure/artifact/test_saved_records.py` | structure.artifact, structure.control |
| `rpipe/structure/data/test_factory_data.py` | structure.control, structure.data.factory |
| `rpipe/structure/data/test_factory_runtime.py` | structure.data.factory |
| `rpipe/structure/data/test_factory_svhn.py` | structure.algorithm.factory, structure.algorithm.tracker, structure.artifact.checkpoint, structure.control, structure.data.factory, structure.model.factory, structure.system.factory |
| `rpipe/structure/data/test_profile.py` | structure.control, structure.data, structure.data.factory, structure.data.profile |
| `rpipe/structure/data/test_resume.py` | structure.data.factory |
| `rpipe/structure/model/test_factory_model.py` | structure.control, structure.model.factory, structure.model.input_norm |
| `rpipe/structure/system/test_factory_system.py` | structure.control, structure.system.factory |
| `rpipe/structure/system/test_logger.py` | structure.algorithm.tracker, structure.artifact.io, structure.control, structure.system.logger |
| `rpipe/structure/system/test_origin.py` | structure.control, structure.data.factory, structure.model.factory, structure.system, structure.system.origin |
| `rpipe/structure/system/test_origin_concurrency.py` | structure.system.origin |
| `rpipe/structure/system/test_origin_sdk.py` | structure.system, structure.system.origin |
| `rpipe/structure/system/test_resources.py` | structure.system |
| `rpipe/structure/system/test_runtime.py` | structure.control, structure.system.runtime |
| `rpipe/structure/system/test_torchvision_load.py` | structure.system, structure.system.torchvision_load |
| `rpipe/structure/test_api.py` | structure.api轻量接口导入 |
| `rpipe/structure/test_control.py` | structure.control |
| `rpipe/structure/test_control_mapping.py` | structure.control |
| `rpipe/structure/test_control_resolution.py` | structure.control |
| `rpipe/structure/test_make.py` | structure.artifact, structure.control, structure.make |
| `rpipe/structure/test_make_declaration.py` | structure.artifact, structure.artifact.io, structure.control, structure.make |
| `rpipe/structure/test_make_plan.py` | structure.make |
| `rpipe/structure/test_run_spec.py` | structure.control.RunSpec及来源路径边界 |
| `rpipe/test_architecture.py` | rpipe包边界、模块导入及唯一发布静态合同 |
| `rpipe/test_cli_mnist_native_vs_hf.py` | flow.cli完整可选真实数据旅程 |
| `rpipe/test_cli_mnist_train_size.py` | flow.cli完整可选真实数据旅程 |
| `rpipe/test_main.py` | rpipe.__main__与公开CLI |
| `studies/main_exp/flow/execute/test_precision.py` | main_exp/flow/execute研究接入及recipe/阶段调用 |
| `studies/main_exp/flow/prepare/test_run.py` | main_exp/flow/prepare研究接入及recipe/阶段调用 |
| `studies/main_exp/flow/process/test_curves.py` | main_exp/flow/process研究接入及recipe/阶段调用 |
| `studies/main_exp/flow/test_prepare_data.py` | main_exp/flow研究接入及recipe/阶段调用 |
| `studies/main_exp/structure/data/test_data_port.py` | main_exp/structure/data研究接入及recipe/阶段调用 |
| `studies/main_exp/test_recipe.py` | main_exp研究接入及recipe/阶段调用 |
| `studies/main_probe/flow/prepare/test_data.py` | main_probe/flow/prepare研究接入及recipe/阶段调用 |
| `studies/main_probe/flow/process/test_saved.py` | main_probe/flow/process研究接入及recipe/阶段调用 |
| `studies/main_probe/flow/test_study_journey.py` | main_probe/flow研究接入及recipe/阶段调用 |
| `studies/main_probe/flow/write/test_observed.py` | main_probe/flow/write研究接入及recipe/阶段调用 |
| `studies/main_probe/structure/algorithm/test_observer.py` | main_probe/structure/algorithm研究接入及recipe/阶段调用 |
| `studies/main_probe/structure/algorithm/test_paired.py` | main_probe/structure/algorithm研究接入及recipe/阶段调用 |
| `studies/main_probe/structure/algorithm/test_registration.py` | main_probe/structure/algorithm研究接入及recipe/阶段调用 |
| `tests/test_event_timing.py` | tests/conftest.py事件起始时间 |
| `tests/test_git_metadata.py` | tests/conftest.py Git来源采集 |
| `tests/test_manifest_bootstrap.py` | tests/run.py → conftest.py →正式证据协议 |
| `tests/test_package_smoke.py` | tests/package_smoke.py安装来源身份 |
| `tests/test_run_entry.py` | tests/run.py参数、环境与子进程启动 |
| `rpipe/structure/algorithm/test_base.py` | structure.algorithm.base.Algorithm/AlgorithmHook循环合同 |
| `rpipe/structure/artifact/test_io_external_roots.py` | make.plan/publish实体根、artifact.io输入引用、resources.output_snapshot、Git真实入口忽略 |
| `rpipe/structure/system/test_local_paths.py` | system.local_paths.configure/main → 实际Git忽略 → 本机目录链接；预览、创建/重复使用、既有内容保全、路径分离 |
| `rpipe/structure/data/test_stats.py` | structure.data.stats只读统计解析及缺失/损坏 |
| `rpipe/structure/model/test_factory_shape.py` | structure.model.factory.resolve_shape正整数、元信息一致性及显式变换 |
| `rpipe/structure/data/test_factory_stats.py` | structure.data.factory._with_recorded_stats消费已保存统计 |
| `tests/test_impact.py` | tests/impact.py 选择与拒绝路径，以及 run.py 的合并离线批次 |
| `tests/test_ci.py` | tests/ci.py 触发选择、真实 Git 合并树与基线、成功回执和必需汇总门 |
