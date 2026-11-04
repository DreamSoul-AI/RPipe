# 一、测试范围与入口

采用[测试规范 代码](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf)。项目执行命令及资源边界从[README](../README.md)进入，当前整改与验收从[开发计划](<../docs/开发计划 Development Plan 研究流水线.md>)进入。这里维护实际镜像、目标映射与例外，不复制研发计划。

| **镜像位置** | **被测程序及落位** |
| --- | --- |
| rpipe/ | src/rpipe/同级骨架，unit对应源文件或对象，integration对应调用入口，e2e对应外部入口或程序根 |
| studies/main_exp、studies/main_probe | 研究接入源码镜像，数据、算法及阶段按实际目录落位 |
| tests/ | 独立测试程序自身的镜像，即仓库tests/run.py、conftest.py、package_smoke.py的合同；不将镜像中的用例再次递归镜像 |
| _data/ | 最小输入、子进程脚本和可选真实研究旅程的声明fixture；不是需要再镜像的生产程序 |

rpipe根下test_architecture按独立模块展开静态合同，test_main覆盖外部命令入口。可选MNIST旅程以公共CLI为入口，位于rpipe根，原声明留在_data/studies中，不再建立不存在的独立Study镜像。run_artifacts旅程由flow.run启动，readout_lifecycle以正式读端为验证对象；二者共用合成准备过程，按实际合同分别落位。

# 二、骨架及排除登记

- src布局外壳不是Python程序层级，镜像从rpipe包开始，包初始化与无独立行为的命名空间不要求空用例
- Study main_exp的structure/api、structure/model仅有初始化/注册接入，未建独立测试目录，实际构建与注册通过recipe及prepare旅程验证。main_probe的flow/collect、execute、summarize目前通过完整合成旅程及paired合同验证，尚无独立目录用例，此处登记骨架缺项，不称独立覆盖已完成
- studies/_template只有声明模板，按模板发布与配置旅程覆盖，不复制科学源码或空测试壳
- 排除__pycache__、build、dist、egg-info、.tmp、虚拟环境、外部依赖、生成输出/缓存、外部证据及旧Run。测试程序的镜像范围只含前述三个执行文件，共享conftest与fixture数据不递归建立镜像

# 三、逐文件目标索引

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
