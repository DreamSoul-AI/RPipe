# RPipe

RPipe（Research Pipeline）是研究项目的代码与执行模板，用声明组织比较因素和seed，把一次Run的有效配置、执行事实、结果与跨seed分析连接起来。Python包名为 `rpipe`，中文名为“研究流水线”。

项目仓库：[DreamSoul-AI/RPipe](https://github.com/DreamSoul-AI/RPipe)。MIT 许可见 [LICENSE.md](LICENSE.md)。源码包元数据仍为 0.4.1；两柱设计与代码已通过 #228、#229 依次合入 dev，尚未发布新版本。

## 阅读入口

| **内容** | **唯一维护位置** |
| --- | --- |
| 项目协作、规范入口和硬规则 | [AGENTS.md](AGENTS.md) |
| 技术机制、现有方法、历史验证、来源和未知 | [研究流水线 通用调研](<docs/研究流水线 通用调研.md>) |
| 质量场景、模块合同、状态、运行取消、测试和CI/CD | [研究流水线 架构设计](<docs/研究流水线 架构设计.md>) |
| 目标目录、每个Python文件、依赖规则与测试镜像 | [研究流水线 代码结构设计](<docs/研究流水线 代码结构设计.md>) |
| 项目研究问题、Study实验设计、数据/模型/算法/指标和停止条件 | [研究流水线 研究计划](<docs/研究流水线 研究计划.md>) |

架构设计集中维护研究对象、质量场景、四类组件、六阶段、状态与取消、身份与产物、恢复、日志、测试和交付合同。代码结构设计登记当前 50 个包内 Python 文件、Study 接入、依赖、调用方、测试镜像及源码与输出分类。[#228](https://github.com/DreamSoul-AI/RPipe/pull/228) 保留 `3da2c50bb8db70fbeb78f07029dd4c8cc28012de` 的设计基线，[#229](https://github.com/DreamSoul-AI/RPipe/pull/229) 跟进实现，集成代码固定为 `a32bdd110dda51986148369ea3358cc41544eb6c`。两份正文分别维护已实现合同、剩余边界与实际验证范围。

历史债务分支只作参考。Git 历史结果只支持其固定提交和条件，不能当新分支验收。开发决定与演进进入开发记录，实际执行从运行记录定位，PR 与提交引用对应记录，不新增同义报告。

改版前完整文档见[固定版本 docs](https://github.com/DreamSoul-AI/RPipe/tree/ed0d50be4328af3480b54de31243bf507901d033/docs)：33 份正文及当时 README、AGENTS 共 35 个原文件已原样恢复到独立历史快照，来源与字节清单随快照保存。每份正文的归并去向见代码结构设计第八章：稳定设计进入两份设计，研究、历史证据和规范保持各自职责；旧 CLI 与运行目录不自动成为当前合同。

## 当前使用边界

源码包位于 `src/rpipe/`，声明位于 `studies/`。`rpipe.api.plan(study_dir, run_root=...)` 不装载 recipe/Torch、不写盘。CLI 的 `plan/status/logs/report/shared` 为读取；`make` 发布配置与索引，`run/launch/run-one` 显式执行，`process` 写派生摘要，`data/tensorboard` 显式输出。常规 Study 命令要求 `--run-root`，旧目录读取另走显式 legacy 入口。运行根与 Study、库源码及可定位的整个 checkout/宿主项目双向分离；解析符号链接及尚未创建的输出路径后检查，不能把输出写入输入目录。

```console
rpipe plan studies/_template --run-root <external-run-root>
rpipe run studies/_template --run-root <external-run-root>
rpipe status studies/_template --run-root <external-run-root>
rpipe process studies/_template --run-root <external-run-root>
```

默认每 Run 独立科学子进程，启动前核源码摘要、配置和自己的 train 依赖。`provenance.inputs` 可显式固定外部文件或目录清单；未固定的 path/stats_path、默认缓存及隐式 profile 分列为未验证，不自动扫描全部数据。已有 result 不原地重跑，须换显式 version；无 result 的已确认失败/取消须 `--retry`（API `retry=True`），unknown/busy 不重放。Run 级 `execution.json` 明确本次 attempt，status/report 分列整体执行、科学结果、派生分析和证据完整性，不按 mtime/UUID 猜最近一次。内存硬限制、同进程硬超时及 Windows 启用 Study hook、recipe、数据 worker 或 HF 后端时的硬超时仍拒绝。没有自动 GPU 分配或旧队列恢复。

Windows 另有显式 `api.supervise(specs, limits=SupervisionLimits(...), evidence_dir=...)` / `rpipe supervise` 批次入口：匿名 Job 监管本批协调者、worker 及后代，入组后才放行，覆盖格内和全批期限；首个失败、质量 False、证据不完整或资源触线即停后续派发。四项上限必须显式传入，没有实验默认值。RSS 与输出为周期观察停止线，可能超调，不是硬内存/磁盘配额。须使用专用外部运行根及新建独立证据根，保留原 Run 科学结果与 unknown/执行权协议；不自动 make、预留 GPU 或恢复旧队列。其他平台拒绝此入口；计量范围、文件引用和清理合同见架构设计第四章。

CLI 必须同时提供 `--run-root`、`--evidence-dir`、`--run-seconds`、`--batch-seconds`、`--rss-bytes`、`--output-bytes`；`--poll-seconds` 与 `--cleanup-seconds` 控制采样间隔和有限收尾等待。当前仅以微型 CPU 合同验收监管机制；研究计划中的资源提案仍未校准或批准，不表示已授权真实实验。

库内实现只分两柱：`structure/`提供配置、产物、读取及data/model/algorithm/system组件，`flow/`组织默认六阶段、原生/HF组装和所属进程。旧 `structure.api/*` 等内部导入不自动恢复；调用方改用 `rpipe.api`、纯 `structure.contracts` 或明确structure领域模块。公开`run(..., payload=...)`参数保持调用兼容，不代表第三个源码层。旧Study内运行目录不会自动迁移/补签；目录对照须明确 `compare --legacy`，其Tensor/轨迹比较保留但来源标为未验证。旧GPU调度/阶段裁剪/隐式cache迁移参数不再接受；先按当前声明和显式根重新计划。精确API与兼容表见代码结构设计。

历史代码验收绑定 #229 的最终完整代码 SHA。已有本地有界 CPU、有限离线原生/HF、安装 smoke，以及 dev 合并后的远端 CPU/打包检查，范围与来源集中在[固定验证摘要](<docs/研究流水线 通用调研.md#四历史验证与可复查边界>)。#231/#232 的固定输入、异常清理和资源监管增量以各 PR 正文的完整 HEAD、本地有界 CPU 与独立 CI 记录为准，不沿用 #229 的绿项。正式 MNIST/CIFAR、GPU、长程性能仍待验收，下一轮准备条件与资源提案见[研究计划](<docs/研究流水线 研究计划.md#四下一轮验收准备与资源提案>)。

## 宿主项目如何接入

下游固定完整上游提交，整体同步 `src/rpipe/`，记录来源、清单/哈希并保留许可证。dayan、SD、量化内嵌副本不得自行修改。问题先在RPipe修复、验证，再整体同步一个明确新固定提交，不用浮动dev或逐文件补丁冒充同一版本。

宿主科学包使用自己的公共接口和领域结构。只有 `studies/` 采用研究模板的声明与薄适配，默认六阶段语义为prepare、execute、collect、summarize、write、process，有实际需求才增加hook，不创建空树。宿主不导入Study，RPipe不静态依赖某个正式Study。新公共 API 与迁移策略见代码结构设计。dayan 已整体固定上述代码，经 [#171](https://github.com/DreamSoul-AI/dayan/pull/171) 合入其 dev，并完成有限真实 CPU Engine 接入验证。SD、量化仍未同步，本轮不修改这些项目。具体身份与验证边界见固定验证摘要。

真实数据、权重、日志、trace、图、checkpoint及源码快照保存在显式外部运行/共享输入根，不进Git。当前main_exp保留的两个静态JSON是科学输入，暂不移动原路径。测试只测本仓行为，下游不复制RPipe内部测试和辅助脚本。

## 资料与交付

DreamSoul项目与规范从 [组织入口](https://github.com/DreamSoul-AI)、[公司资料入口](https://dreamsoul.feishu.cn/drive/folder/HFFFfNlvelicdsd46PCcrVZ2n4g) 及 [规范索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 定位，具体链接以AGENTS的当前入口为准，不依赖某台机器路径。文档设计采用 [CPU内存清理样板](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/6938f10447b250958856a845fc5448aa1185b52d) 的粒度，从质量场景推导职责和文件边界。

文档交付与代码跟进分别经独立 PR 指向 dev。#230 维护交付与验收计划，#231/#232 跟进固定输入、异常清理和资源监管；合并、发布、实验执行和其他项目接入分别按明确授权推进。

## 研发维护与文档现状

Python 3.10+ 与 PyTorch 等依赖见 [pyproject.toml](pyproject.toml)，安装后的 CLI 入口为 `rpipe`。目录入口：[产品或库源码](src/rpipe/)、[研究声明与适配](studies/)、[测试](tests/)、[当前文档](docs/)。运行与测试按本 README、相关设计和实际配置选择，不把历史命令视为新的实验授权。

已有通用调研、架构设计、代码结构设计和研究计划，入口见上表。文献调研、开发计划、缺陷记录、开发记录与运行记录尚未建立独立当前入口，待按实际研究与开发进展补齐。现有研究计划不能替代开发计划，通用调研不能替代文献调研；历史结果仍按各文档的来源和版本解释。

现有专项文件沿用历史“项目名 文档类型”名称，通过上表定位，作为本仓现阶段的命名例外登记；新建或后续迁移采用 AGENTS.md 的“文档类型 [English Name] 项目名称.md”，改名时同步所有消费者与引用，不自动批量迁移。

本地测试入口为 [tests/run.py](tests/run.py)。先按其参数选择有界 CPU 合同与外部结果根，不能用测试代替真实数据、GPU 或完整研究验收。
