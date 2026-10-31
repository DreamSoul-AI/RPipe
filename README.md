# RPipe

RPipe（Research Pipeline，研究流水线）是用于搭建研究实验的参考模板，也是开始一个研究项目的 warm-up / starting point。研究者可以复制或 fork 本仓，从中学习如何组织实验部件、执行流程、配置和结果，再直接修改代码以适应自己的课题。后续项目可以继续拆合模块、替换实现和调整流程，由该项目自行维护。

RPipe 的交付对象是可阅读、可修改的仓库起点，不以供其他项目长期安装调用的通用 package 为产品目标，也不要求衍生项目持续跟随本仓接口。模板自身仍需讲清职责、保持可运行并验证示例，帮助研究者把有效配置、执行事实、科学结果和比较分析连接起来。Python 包名 `rpipe`、安装命令和打包配置是当前源码的组织与运行方式，不构成跨项目稳定 API 的承诺。仓库为 [DreamSoul-AI/RPipe](https://github.com/DreamSoul-AI/RPipe)，采用 [MIT 许可](LICENSE.md)。

当前包元数据为 **0.4.1**。五批核心重构及研究接入工程验证已本地提交；main_probe在固定环境、seed0和每侧60步条件下，8格CUDA配对及Study终验通过，见[本轮运行记录](<docs/运行记录 Run Record/2026-10-30 main_probe CUDA配对验证.md>)。首轮缺历史依赖的失败保留原身份，没有回签。架构和代码结构设计各以一份完整Markdown维护当前目标，剩余工作从[开发计划](<docs/开发计划 Development Plan 研究流水线.md>)进入。main_exp八格600步CUDA配对及039c941全新version的完整E4已按固定协议通过，证据与适用范围见[新电脑执行记录](<docs/运行记录 Run Record/2026-10-30 新电脑main_exp完整矩阵执行.md>)。当前进行#236独立审查与工程交付，版本号不表示已发布。

## 文档与阅读顺序

2026年10月31日已安全同步交接候选c5b12d6并保留本地草稿，当前审查覆盖#236相对dev的完整差异。待办与停止点见[开发计划](<docs/开发计划 Development Plan 研究流水线.md>)；规范复核依据见[复核记录](<docs/运行记录 Run Record/2026-10-31 规范复核与计划重排.md>)。原科学证据保持其SHA，新工程修复不继承旧E4验收。

研发协作的共用依据为 [研发用 AGENTS.md](https://dreamsoul.feishu.cn/docx/QICWdeEokobO9RxYR9zcjE6Pnyg)。采用其适用正文，并结合本仓 [AGENTS.md](AGENTS.md) 的项目约定执行。共用《研发用 AGENTS.md》与资料库根 AGENTS.md 是不同文档，不得相互替代。本地源与飞书正文需核对版本，不能以本仓规则已更新推定共用正文已同步。

先读本 README、共用《研发用 AGENTS.md》和本仓项目约定，再按问题进入以下唯一成果。开发与研究的计划独立维护，日期记录保存实际演进与证据，不另建同义进度、交接或验收报告。

| **成果** | **当前内容与边界** |
| --- | --- |
| [通用调研 General Research](<docs/通用调研 General Research 研究流水线.md>) | 机制、工程路线、直接来源、取舍与限制；项目历史执行只进入日期记录 |
| [架构设计 Architecture Design](<docs/架构设计 Architecture Design 研究流水线.md>) | 单一完整正文：当前选定的实验部件、流程扩展、观测恢复及产物合同 |
| [代码结构设计 Code Structure Design](<docs/代码结构设计 Code Structure Design 研究流水线.md>) | 依据完整架构推导的单一文档：目标目录、模块职责、接口、允许依赖及验证落点；规划/发布、生命周期、部件装配、快照读端及入口交付已实施，后续研究接入见开发计划 |
| [开发计划 Development Plan](<docs/开发计划 Development Plan 研究流水线.md>) | 实现差距、逐模块工作、旧接口清理及验收顺序；按明确目标队列连续推进，每批验证、记录并本地提交 |
| [缺陷记录 Bug Report](<docs/缺陷记录 Bug Report 研究流水线.md>) | 活动问题与待验证事项；已关闭问题的变更和证据只从日期记录追溯 |
| [开发记录 Development Record](<docs/开发记录 Development Record/>) | 日期记录，本轮文档调整与历史集成、分支保全的归属；不复制整份测试结果 |
| [运行记录 Run Record](<docs/运行记录 Run Record/>) | 各次静态、CPU、安装及 CI 验证的实际身份、结果和缺口；原始证据在 Git 外 |
| [文献调研 Literature Review](<docs/文献调研 Literature Review 研究流水线.md>) | 按问题比较六篇原始研究的方法、证据和限制，注明阅读深度；非穷尽系统综述，无外部优越性结论 |
| [研究计划 Research Plan](<docs/研究计划 Research Plan 研究流水线.md>) | 唯一中文研究计划，RQ1–RQ4、全部 Study 的 E0–E4 设计与判据；main_probe E3和main_exp600步配对已有固定证据，新电脑039c941全新version的32train/32eval及完整E4科学门已通过，适用范围见运行记录 |

项目演进、旧文档归并与兼容迁移从[开发记录](<docs/开发记录 Development Record/2026-10-27 文档重构与历史归属.md>)追溯，实际历史验证从[运行记录](<docs/运行记录 Run Record/2026-10-27 历史验证整理.md>)追溯。记录保留原提交、日期及运行身份。

修订建议集中放在 [docs/修订建议/](<docs/修订建议/>)，按被修订文档分别维护，目前为[研发用 AGENTS 修订建议](<docs/修订建议/修订建议 Revision Proposals 研发用 AGENTS.md>)。项目已采用的专项规范仍须执行；共用入口不另定布局，不构成豁免专项规范的理由。建议经讨论与验证后再纳入相应正文。

## 技术栈与目录

Python **3.10+**，setuptools 的 `src` 布局。实际依赖为 PyTorch、torchvision、NumPy、SciPy、PyYAML、matplotlib、kornia。`dev`、`tensorboard`、`hf` 为可选依赖组，准确范围见 [pyproject.toml](pyproject.toml)。元数据的支持范围不代表所有版本组合已验证。

| **位置** | **职责** |
| --- | --- |
| [src/rpipe/](src/rpipe/) | 模板主体；当前 structure 提供声明、产物机制及 data/model/algorithm/system，flow 拥有阶段、执行权、所属科学进程及唯一正式写入；后续工程核验与研究接入差距见开发计划 |
| [studies/](studies/) | `_template` 示例与 main_probe/main_exp 声明、研究适配及必要静态科学输入 |
| [tests/](tests/) | 本仓源码/Study 镜像、最小合成输入、统一执行器和安装 smoke |
| [docs/](docs/) | 七类开发成果与两类研究成果、日期记录 |
| [.github/workflows/](.github/workflows/) | 当前 CPU、打包和分支方向检查，不含 GPU/正式实验验收 |

当前包根保留命令入口与版本信息，structure 描述条件、科学部件和持久化，flow 拥有生命周期。六阶段为 `prepare → execute → collect → summarize → write → process`，均由 stages 中的实际函数承接：worker 只交回草稿与收尾观察，run 持权并经唯一 write 定稿。基于模板开展课题时可以直接修改这些部件和流程；已有领域包也可以按需复用，不要求通过统一插件或薄适配接入。

## 安装与入口

以下是使用命令，不表示本轮执行过安装或测试。先选择满足项目依赖的独立 Python 环境。安装会解析依赖，缺包时可能联网和下载 PyTorch 等大包，按当前环境与授权准备，不自动升级已有环境。

```console
python -m pip install -e ".[dev]"
python -m rpipe --help
python -m rpipe --version
```

安装后 `rpipe` 与 `python -m rpipe` 同走 [flow/cli.py](src/rpipe/flow/cli.py) 的 main()，模块入口为 [__main__.py](src/rpipe/__main__.py)，规划与发布直接使用 [structure.make](src/rpipe/structure/make.py) 的 plan/expand/publish；单次执行使用 [flow.run.run](src/rpipe/flow/run.py)，批次使用 [flow.supervision](src/rpipe/flow/supervision.py)，派生使用 [flow.process](src/rpipe/flow/process.py)；正式读取使用 [artifact.readout](src/rpipe/structure/artifact/readout.py) 的 RunRef 接口，比较使用 [artifact.comparison](src/rpipe/structure/artifact/comparison.py) 的明确对象接口。HF/TensorBoard 功能另需相应依赖。只读路径延迟导入科学栈，不等于安装元数据没有科学依赖。

已有环境可显式使用本仓源码，PowerShell 示例：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -c "import rpipe; print(rpipe.__version__, rpipe.__file__)"
python -m rpipe --help
```

确认导入路径属于当前 checkout，避免误用其他项目内嵌副本。这里只解决源码定位，不安装或补齐依赖。main_probe/main_exp还需要固定历史源码的实际导入依赖，例如evaluate及TensorBoard；当前原生CPU smoke通过不等于历史全模块可导入。先完整核两侧导入、真实数据准备和实际模型结构/参数dtype，按需在隔离环境补依赖并记录版本与来源，不能用stub替代固定参考实现。

## Study 使用与执行边界

Study 至少有 `study.yaml` 和 `experiment_config.yaml`。因素轴展开为 Experiment，每个 seed 形成一个 Run。`structure.make.plan(study_dir, run_root=...)` 只读声明与源码，不加载 recipe/Torch、不写盘。常规 Study 命令要求显式 `--run-root`，路径须与 Study、库源码、可定位的完整 checkout/宿主项目和受保护输入双向分离，运行输出不写回源码。

<a id="数据模型与大产出暂行规范待实测"></a>

### 数据、模型与大产出（暂行规范，待实测）

本节暂作为本项目的执行约定，先验证再评估是否沉淀到共用《研发用 AGENTS.md》。以下区分当前实现限制与待验证的链接布局，不表示所有后端、平台和链接类型均已通过。已有长实验使用其固定路径与运行身份，本轮不在运行中迁移输入、缓存或产出。

1. 原始数据、大模型权重、派生缓存、checkpoint及大产出放在源码checkout之外。多个项目可共用中央原始数据或模型根，缓存与可写产出按项目隔离，正式输出再按Study/version区分。输入根、输出根和证据根须核真实路径、权限与包含关系，不将output放进data/model内，也不让清理输出触及共用输入。
2. 首次使用、换机或路径变化时，Agent根据机器和已有布局提出具体位置，由用户确认；确认后复用，不逐次询问。项目使用者填写以下本机记录，不能照抄另一台机器的盘符。实际值保留在用户的本机配置记录中，不写死为团队默认路径，也不包含凭据。下表是人工确认模板，不是已实现的自动配置加载接口；执行命令仍须显式传入实际支持的路径参数。

| **本机字段** | **用户填写并确认** | **用途** |
| --- | --- | --- |
| 原始数据根 | `<绝对路径>` | 可跨项目复用的原始数据及固定来源 |
| 模型与权重根 | `<绝对路径或未使用>` | 版本、来源和许可证明确的预训练权重 |
| 派生缓存根 | `<绝对路径>` | 可再生成缓存，注明由谁创建和清理 |
| 项目运行输出根 | `<绝对路径>` | 本项目Study/version、Run、checkpoint及图表 |
| 执行证据根 | `<绝对路径>` | 监管、测试与核验原件，按用途和身份隔离 |
| 仓内访问入口 | `<入口路径 → 真实目标；链接类型，或未使用>` | 便捷访问的symbolic link/junction及其作用域 |

3. 仓内需要便捷访问时，设计指向上述外部位置的symbolic link入口，并登记目标和用途；链接本身与运行物不进Git。现有`/data/`、`/model/`、`/output/`、`/shared/`及Study运行物已在[.gitignore](.gitignore)中排除，新入口另核实际忽略效果。科学参数、schema、固定输入清单、来源元数据及许可证继续按职责版本化，不按扩展名一概忽略。
4. 当前实际执行使用解析后的真实绝对根。受监管输出树中的shared/data目录须保持实体目录，不能用指向树外的目录符号链接或junction代替；Windows输出监管会拒绝这类目录链接。因此仓内便捷入口与运行树内部链接须分别设计，不能把仓内入口直接当作已支持的监管路径。
5. 原始文件复用优先使用明确的文件级symbolic link；现有artifact.io.link_input/reference_tree在符号链接不可用时可回退到同卷硬链接，使用前检查条件，实际类型和目标须记录。不要把整个多项目数据/模型根递归挂进Study，不静默复制大文件作为链接替代。模型后端是否支持外部权重及链接另核实际构造入口，不将文件链接能力外推为所有后端支持。
6. 链接和硬链接都不构成写保护。共享原始输入按来源和固定哈希核验，可写派生与其分开；变更或清理前核消费者和执行占用。移除链接入口不得递归删除真实目标，未知文件、旧Run、权重及无关项目产出继续按仓库清理授权处理。
7. 后续用独立合成文件验证：路径确认与解析、实际链接类型、同源校验、无链接权限/跨卷限制、隔离与忽略、入口移除后目标保全，以及监管对支持/不支持布局的实际处理。未完成的项目仍标待验证，不使用真实数据或旧Run作清理fixture。

### 计划发布与执行

发布先写完整配置，最后以单次原子替换提交 schema 3 的 index，来源清单与运行索引在同一记录内，所有目标 Run 在提交前持续持权。重复发布不改已有配置字节；既有配置、结果、执行事实或计划来源不匹配时拒绝，不自动给旧记录换身份。需要新实测时选择明确新 version；不提供旧格式回退。

先用只读计划核对配置与输出身份。以下外部路径由调用方替换，不能将占位符作为实际路径：

```console
python -m rpipe plan studies/_template --run-root <external-run-root>
python -m rpipe status studies/_template --run-root <external-run-root>
python -m rpipe report studies/_template --run-root <external-run-root>
```

**`_template` 当前是 MNIST/CUDA、20 epoch 示例，不是离线 CPU smoke。** 复制后先在研究计划登记问题、条件和判据，再核有效声明、数据/模型来源、设备、精度与预算。plan 不下载或训练。模板配置注释指向本节。离线合成安装旅程由 tests/package_smoke.py 提供，不能外推到模板科学实验。

| **命令** | **作用与边界** |
| --- | --- |
| `plan/shared` | 只读当前声明，不启动、清理或补写 |
| `status/logs/report` | 按已发布清单和保存身份读取，不核当前源码；显式 --version 可在源码不在场时定位历史版本 |
| `compare <left-run-dir> <right-run-dir>` | 两端须在各自正式 index 中；容差和 checkpoint 选择明确，--out 必须在输入 Study 域外 |
| `make` | 经 structure.make.publish 发布配置与索引，不准备科学数据或生成旧 jobs/脚本 |
| `run` | 先 make 再 launch，显式执行 |
| `launch [--id]` | 执行明确规格，核来源与自身 train 依赖，默认每 Run 独立科学子进程 |
| `process [--figures]` | 核当前源码、正式清单、配置与结果，写派生摘要及明确请求的图；输入漂移拒绝发布，不覆盖科学结果 |
| `data [--download]` / `tensorboard --out` | 显式 profile/下载或导出，遵守输入、输出与来源约束 |
| `supervise` | 显式 Windows 批次监管，不自动 make、预留 GPU 或恢复旧队列 |

在已确认来源、资源及本次执行授权后，执行用法为：

```console
python -m rpipe run <study-dir> --run-root <external-run-root>
python -m rpipe process <study-dir> --run-root <external-run-root>
```

四类部件的程序化构造分别使用各职责模块的 build_data/build_model/build_algorithm/build_system，轻量能力合同在 [structure.api](src/rpipe/structure/api.py)。模型接 data_meta 和 System，算法构造显式接本次 checkpoint_dir 和可选 train_checkpoint_dir，compute 接注入部件及只读取消检查器；runtime/System/Logger 和研究准备有明确顺序。不同来源的数据游标与恢复支持分别解释，不因接口相同就保证跨后端等价。

持权前 busy、来源失配或无效请求只返回 status=rejected 的 RunReport，不给目标 Run 写失败记录。成功定稿须先核源码与有效配置、科学响应和资源关闭；未确认释放时保留 owner 和 unknown 事实，禁止成功发布或新派发。正式结果后的 process 仅使用已保存记录，派生失败单独报告。Study 摘要记录 index/config/result/execution、明确 outcome 及 busy 状态的输入指纹，汇总期间变化时保全旧文件。

已有正式 result 拒绝原地重跑，须使用显式新 version。无 result 的已确认 failed/cancelled 仅允许显式 --retry（flow.run.run 的 retry=True），unknown/busy 不重放。批次退出 0 还要求非空、完整覆盖所选项、各项执行成功、质量非 False 且证据完整；提前停止、取消或成功前缀不算整批成功。execution.json 明确索引当前 attempt，执行、科学结果、派生分析、证据完整度分列，不由 mtime、UUID、退出码或日志推断整体成功。

只读命令省略 --version 时，仅读取当前声明的版本字段定位输出；需要查看另一版本或源码已移走时显式提供 --version，不按目录时间猜选。正式轨迹和导出只使用清单列出的 Run 及 JSONL 有效观测，不扫描旧目录或用 state-history 回退补齐。

完整快照由 [artifact.checkpoint](src/rpipe/structure/artifact/checkpoint.py) 的 save_snapshot/load_snapshot 存取，明确绝对 .pt 文件，以原子替换为完成点，只接受安全 Tensor 与基础状态，不加载研究类或旧格式。保存失败保全先前完整文件；恢复准入归算法，CPU 原生与 HF 的支持范围分别验证。

provenance.inputs 可固定外部文件或目录清单，未固定 path/stats_path、默认缓存与隐式 profile 分列未验证。以上命令和能力说明对应当前实现；目标结构见两份设计，实施差距与接口清理见开发计划，实际演进和验证见日期记录。

### 资源监管与当前限制

默认 Budget 拒绝硬内存限制和同进程硬超时。Windows 有启用的 Study hook、recipe、DataLoader worker 或 HF 后端时，普通 run 的硬超时仍拒绝。执行命令作用域中的 Ctrl+C 请求同一 Cancellation，父进程通过绑定本 attempt 的标记通知科学进程；算法在只读检查点合作退出。协作和所属资源强停均受清理时限约束，硬超时不延长为合作宽限；不能确认关闭时保留 unknown 和 owner，不承诺任意阻塞代码都可立即合作退出。

main_probe参考侧训练、周期评测和自身best独立评测也使用同一只读取消检查器，在请求批次及交回观测前检查；已经进入的一次阻塞读取不能被合作检查器抢占，实际边界与关闭证据见[参考侧取消记录](<docs/运行记录 Run Record/2026-10-29 参考侧合作取消验证.md>)。

Windows 可另选 flow.supervision.supervise / rpipe supervise。CLI 必须显式提供 --run-root、--evidence-dir、--run-seconds、--batch-seconds、--rss-bytes、--output-bytes，另可设置 --poll-seconds 与 --cleanup-seconds。匿名 Job 监管本批协调者、worker 和后代，入组后才放行，首败、质量 False、证据不全、观察失败或超限即停后续派发。

RSS 与输出是采样停止线，可能漏峰或超调，**不是硬内存或磁盘配额**。须使用专用外部运行根与新建独立证据根，保留原 Run owner/unknown 协议，其他平台拒绝此入口。用户已授权按本机硬件与准入实测自主设置常规预算；换机须重新核环境、硬件和停止线，完整研究矩阵仍待验收。详细计量与清理合同见架构第四章；本次停止本机实验、推送与换机重新执行的边界见[交接记录](<docs/开发记录 Development Record/2026-10-30 跨电脑交接.md>)。

## 验证与证据

统一入口为 [tests/run.py](tests/run.py)，按标签与成本选择，结果根在 checkout 外，每个 pytest 进程指定独立临时目录。以下为后续受控 CPU 检查用法，不是本轮执行结果：

```console
python tests/run.py --help
python tests/run.py --core --results-root <external-test-root> --timeout-seconds 600 --budget-seconds 660 -- --basetemp <unique-external-temp>
```

结果包含 manifest、逐项 events 与可重建 report，未执行、跳过、取消、缺失与失败分别保留。执行器只监督本次 pytest 根进程，不提供逐项内存或全部后代硬限。制品验证另用 [tests/package_smoke.py](tests/package_smoke.py)，须用本次实际 wheel/sdist 安装到新 venv 的解释器，显式 --artifact 指明制品和 --workspace 指明新外部位置；核 venv/导入/console/RECORD 与制品来源哈希，不用 editable、外来 PYTHONPATH 或旧同版本包替代。

现有工作流在 dev/main 的 PR 和 push 上运行 CPU 矩阵（Ubuntu/Windows × Python3.10/3.13）及两平台 Python3.10 打包安装，另在 PR 检查分支方向。CPU 作业归档统一测试回执和命令日志，打包作业归档构建/安装日志及已生成的 wheel/sdist smoke 工作区。上传在成功或失败后均尝试执行，缺少文件时明确失败；artifact 名包含矩阵、Actions run/attempt 和检出 SHA，保留7天。日志管道使用 Bash pipefail，不吞测试或构建失败。平台强制终止、runner 丢失或上传服务不可用时可能无完整归档，不能将未上传当作通过。工作流声明、远端实际结果与分支保护不是同一事实，不把配置存在记为当前 HEAD 已通过。

首批配置规划与发布的受影响合并回归为 623 通过、1 项既有 Windows/POSIX 静态豁免，另有五命令 CLI 旅程；科学 worker 验证使用临时 DLL 搜索路径，环境诊断、失败原件和限制见[2026-10-29 运行记录](<docs/运行记录 Run Record/2026-10-29 配置规划与发布验证.md>)。

生命周期批次的最终合同回归 741 通过、1 项既有静态豁免，原 fixture 反例定向 381 通过，另有真实六命令旅程；扩大运行的预算结束、缺项和依赖跳过仍保留，见[生命周期验证记录](<docs/运行记录 Run Record/2026-10-29 生命周期与唯一发布验证.md>)。

部件批次另以分组合同、原数值、10 个 CPU 独立进程恢复、315 项 Flow 集成及最后形状补验核对，该批固定源码的六命令旅程通过；分组重叠、失败/中断及未执行范围见[科学部件验证记录](<docs/运行记录 Run Record/2026-10-29 科学部件合同与装配验证.md>)，不将部分证据合成全仓或正式研究通过。

快照与读端批次保留真实 CPU 恢复失败及修复、402 项读端/比较、原失败文件174与研究reader69的分组证据，最终六命令和比较 CLI 通过；完整范围、重叠和 SDK 跳过见[快照读端验证记录](<docs/运行记录 Run Record/2026-10-29 快照读端与派生职责验证.md>)。

研究读端的工程接入使用正式RunRef与保存结果，配对质量通过独立quality交付；旁路报告、旧日志和未闭合执行不能补出通过。实际反例、分组回归及未执行范围见[研究读端验证记录](<docs/运行记录 Run Record/2026-10-29 研究读端正式合同验证.md>)。

入口和交付批次完成取消、批次退出与旧接口清理；两份实际制品各自通过新 venv 的十命令、四 Run 工程旅程。分组回归、共享冲突原例及本次安装来源验证分别在[入口交付运行记录](<docs/运行记录 Run Record/2026-10-29 入口取消与制品交付验证.md>)维护；工程最小旅程仍用显式 Toy/Stub，不把它当作研究精度验收。

历史与当前的本地有界 CPU、有限原生/HF、安装 smoke 与远端 CI 分别按运行记录中的固定身份核对。重构先执行受影响合同回归，再按实际范围验证科学装配、Study 与制品旅程。main_probe与main_exp的MNIST/CIFAR10八格CUDA短前缀均已按各自固定环境通过；完整曲线、长程性能、矩阵外环境与原始历史归档可恢复性仍未验收。

来源完整度按范围解释：通用 input_provenance 只观察声明及默认引用，Study 自定义输入另保存核验证据。已完成八格的准备、raw 哈希及归档关联经[只读复核](<docs/运行记录 Run Record/2026-10-30 来源边界与准入选择验证.md>)确认，未改写旧 Run。main_exp preflight 已拒绝空、重复或不支持的显式组合，避免意外扩大执行；600步实际执行证据见当前研究状态，不能用该离线修复代替。

## 宿主接入与资料来源

下游固定完整上游提交，整体同步 src/rpipe/，记录完整 SHA、成员/哈希并保留 MIT。dayan、SD、量化的内嵌副本不得自行修改，问题先在 RPipe 修复验证，再按单独授权整套同步。宿主集成身份与验证记录只在日期记录维护。共用文档职责的同步不包含内嵌库、源码、测试或配置同步。

数据、权重、日志、trace、图、checkpoint、源码快照与大报告保存在显式外部根，不进 Git。studies/main_exp/docs/ 的两个 JSON 是有实际消费者的静态科学输入，原路径与 pin 保留，不按扩展名删除。历史 Run 不因文档改名回签身份。

共用资料从 [DreamSoul GitHub 组织](https://github.com/DreamSoul-AI)、[公司资料入口](https://dreamsoul.feishu.cn/drive/folder/HFFFfNlvelicdsd46PCcrVZ2n4g)、[规范总索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 与 [工具教程索引](https://dreamsoul.feishu.cn/docx/VSQ5dlJl3o0hbIxrEhUc8qKEnBc) 定位。[研发用 AGENTS.md](https://dreamsoul.feishu.cn/docx/QICWdeEokobO9RxYR9zcjE6Pnyg)、[通用调研](https://dreamsoul.feishu.cn/docx/XOwxdH7aYoplBjx7JCFcZCQ5n9f)、[使用教程](https://dreamsoul.feishu.cn/docx/JrUgdOKh2oJ9SBxosWyc4Dk7nIh) 提供共用方法，采用范围见 AGENTS 与运行记录。架构粒度参考 [固定 CPU 内存清理样板](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/6938f10447b250958856a845fc5448aa1185b52d)，不把案例或其他仓库指令当本仓授权。

当前开发任务、建议顺序与执行边界见开发计划。代码、实验、合并、发布与下游同步按各自明确范围推进。

当前用户已要求持续完成开发计划。研究资源由Agent按[研究计划的硬件自适应规则](<docs/研究计划 Research Plan 研究流水线.md>)自主测量与分配，不再逐次询问常规预算；科学参数和门限保持，首败停止。main_exp八格CPU准入及[600步CUDA配对](<docs/运行记录 Run Record/2026-10-30 main_exp 600步配对验证.md>)已通过，新电脑039c941完整曲线已按固定协议验收，当前工程修复保留独立候选身份。pytest使用importlib导入，允许镜像目录中的同名测试完整收集；本机原生DLL启动条件与候选制品验证范围从[本批记录](<docs/运行记录 Run Record/2026-10-30 硬件自适应与交付准入验证.md>)读取，不推定所有机器环境相同。

新电脑main_exp完整32train/32eval矩阵已按039c941及新version从头完成，完整曲线、四seed统计、自身best独立eval、固定参考及模型顺序门均通过，正式资源收尾完成；实际环境、证据与适用范围从[新电脑执行记录](<docs/运行记录 Run Record/2026-10-30 新电脑main_exp完整矩阵执行.md>)追溯；[旧电脑执行记录](<docs/运行记录 Run Record/2026-10-30 main_exp完整矩阵执行.md>)保留交接中断事实，不拼接。该验收不外推其他机器、配方或总体统计等价。采用本轮结构的新课题应以当前两份设计和示例直接组织研究代码，使用新version保留来源。旧接口和旧checkpoint不作新版兼容承诺；需要复查的历史Run保留其原源码与环境，不给旧成果换身份，也不要求下游迁移。
