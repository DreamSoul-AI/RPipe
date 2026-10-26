# RPipe

RPipe（Research Pipeline，研究流水线）是研究项目的代码与执行模板。用声明组织比较因素与 seed，将 Run 的有效配置、执行事实、科学结果和跨 seed 分析连接起来。Python 包名为 `rpipe`，仓库为 [DreamSoul-AI/RPipe](https://github.com/DreamSoul-AI/RPipe)，采用 [MIT 许可](LICENSE.md)。

当前 dev 已集成两柱重构及输入校验、异常清理、Windows 批次监管和日志启动竞争修复。包元数据仍为 **0.4.1**，不表示这些兼容变化已发布。文档整理基线为 `96fefa863fafb5eae20e885a4757322c671effbd`，工程验收分别绑定原代码 SHA，正式数值、GPU 和长程性能仍待验收。

## 文档与阅读顺序

先读本 README 和 [AGENTS.md](AGENTS.md)，再按问题进入以下唯一成果。开发与研究的计划独立维护，日期记录保存实际演进与证据，不另建同义进度、交接或验收报告。

| **成果** | **当前内容与边界** |
| --- | --- |
| [通用调研 General Research](<docs/通用调研 General Research 研究流水线.md>) | 机制、工程路线、来源与简短固定历史摘要；外部工具未按同协议实测 |
| [架构设计 Architecture Design](<docs/架构设计 Architecture Design 研究流水线.md>) | 质量场景、职责、接口、状态、运行部署、取消/故障、测试与 CI/CD 合同及保留限制 |
| [代码结构设计 Code Structure Design](<docs/代码结构设计 Code Structure Design 研究流水线.md>) | 两柱到当前50个包内 Python 文件、公开入口、依赖、Study/测试映射及源码/产物分类 |
| [开发计划 Development Plan](<docs/开发计划 Development Plan 研究流水线.md>) | 本轮文档阶段、后续工程任务、前置、验收和停止点；代码阶段待文档审阅后确认 |
| [缺陷记录 Bug Report](<docs/缺陷记录 Bug Report 研究流水线.md>) | 活动合同缺口：CI 失败证据归档、模板旧指南注释；已修复历史不积累于活动清单 |
| [开发记录 Development Record](<docs/开发记录 Development Record/>) | 日期记录，本轮文档调整与历史集成、分支保全的归属；不复制整份测试结果 |
| [运行记录 Run Record](<docs/运行记录 Run Record/>) | 本轮静态核验与固定 CPU 验收回顾；原始证据在 Git 外，本轮未重核其完整性 |
| [文献调研 Literature Review](<docs/文献调研 Literature Review 研究流水线.md>) | 恢复已有定向检索、阅读深度和证据比较；不是新检索或完整系统综述，无外部优越性结论 |
| [研究计划 Research Plan](<docs/研究计划 Research Plan 研究流水线.md>) | 唯一中文研究计划，RQ1–RQ4、全部 Study 的 E0–E4 设计与判据；正式对照和资源校准未完成 |

历史设计 [#228](https://github.com/DreamSoul-AI/RPipe/pull/228)、实现 [#229](https://github.com/DreamSoul-AI/RPipe/pull/229) 与后续 [#231](https://github.com/DreamSoul-AI/RPipe/pull/231) / [#232](https://github.com/DreamSoul-AI/RPipe/pull/232) / [#233](https://github.com/DreamSoul-AI/RPipe/pull/233) 的身份和证据从日期记录追溯。旧版33份正文及当时 README/AGENTS 可在 [固定历史 docs](https://github.com/DreamSoul-AI/RPipe/tree/ed0d50be4328af3480b54de31243bf507901d033/docs) 查阅，归并对照保留于代码结构设计第八章。历史原名与 SHA 不因本轮改名回签。

## 技术栈与目录

Python **3.10+**，setuptools 的 `src` 布局。实际依赖为 PyTorch、torchvision、NumPy、SciPy、PyYAML、matplotlib、kornia。`dev`、`tensorboard`、`hf` 为可选依赖组，准确范围见 [pyproject.toml](pyproject.toml)。元数据的支持范围不代表所有版本组合已验证。

| **位置** | **职责** |
| --- | --- |
| [src/rpipe/](src/rpipe/) | 通用库，structure 提供声明、执行/产物机制及 data/model/algorithm/system，flow 组织阶段、后端装配与所属进程 |
| [studies/](studies/) | `_template` 示例与 main_probe/main_exp 声明、研究适配及必要静态科学输入 |
| [tests/](tests/) | 本仓源码/Study 镜像、最小合成输入、统一执行器和安装 smoke |
| [docs/](docs/) | 七类开发成果与两类研究成果、日期记录 |
| [.github/workflows/](.github/workflows/) | 当前 CPU、打包和分支方向检查，不含 GPU/正式实验验收 |

包根只保留公开入口与版本信息，不增加平级载荷层。宿主科学包保持自身公共领域结构，只有 Study 接入声明与薄适配。六阶段为 `prepare → execute → collect → summarize → write → process`，默认阶段可直接复用，有实际职责差异才增加 hook，不创建空树。

## 安装与入口

以下是使用命令，不表示本轮执行过安装或测试。先选择满足项目依赖的独立 Python 环境。安装会解析依赖，缺包时可能联网和下载 PyTorch 等大包，按当前环境与授权准备，不自动升级已有环境。

```console
python -m pip install -e ".[dev]"
python -m rpipe --help
python -m rpipe --version
```

安装后 `rpipe` 与 `python -m rpipe` 同走 [flow/cli.py](src/rpipe/flow/cli.py) 的 main()，模块入口为 [__main__.py](src/rpipe/__main__.py)，Python 公共入口为 [rpipe.api](src/rpipe/api.py)。HF/TensorBoard 功能另需相应依赖。只读路径延迟导入科学栈，不等于安装元数据没有科学依赖。

已有环境可显式使用本仓源码，PowerShell 示例：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -c "import rpipe; print(rpipe.__version__, rpipe.__file__)"
python -m rpipe --help
```

确认导入路径属于当前 checkout，避免误用其他项目内嵌副本。这里只解决源码定位，不安装或补齐依赖。

## Study 使用与执行边界

Study 至少有 `study.yaml` 和 `experiment_config.yaml`。因素轴展开为 Experiment，每个 seed 形成一个 Run。`api.plan(study_dir, run_root=...)` 只读声明与源码，不加载 recipe/Torch、不写盘。常规 Study 命令要求显式 `--run-root`，路径须与 Study、库源码、可定位的完整 checkout/宿主项目和受保护输入双向分离，运行输出不写回源码。

先用只读计划核对配置与输出身份。以下外部路径由调用方替换，不能将占位符作为实际路径：

```console
python -m rpipe plan studies/_template --run-root <external-run-root>
python -m rpipe status studies/_template --run-root <external-run-root>
python -m rpipe report studies/_template --run-root <external-run-root>
```

**`_template` 当前是 MNIST/CUDA、20 epoch 示例，不是离线 CPU smoke。** 复制后先在研究计划登记问题、条件和判据，再核有效声明、数据/模型来源、设备、精度与预算。plan 不下载或训练。模板中指向旧 README Study 指南的注释待 BR2 修正。离线合成安装旅程由 tests/package_smoke.py 提供，不能外推到模板科学实验。

| **命令** | **作用与边界** |
| --- | --- |
| `plan/status/logs/report/shared` | 读取，不启动、清理或补写；旧目录读取使用显式 legacy 入口 |
| `make` | 发布配置与索引，不准备科学数据或生成旧 jobs/脚本 |
| `run` / `study` | 先 make 再 launch，显式执行 |
| `launch/run-one` | 执行明确规格，核来源与自身 train 依赖，默认每 Run 独立科学子进程 |
| `process [--figures]` | 写外部派生摘要及明确请求的图，不训练或覆盖科学结果 |
| `data [--download]` / `tensorboard --out` | 显式 profile/下载或导出，遵守输入、输出与来源约束 |
| `supervise` | 显式 Windows 批次监管，不自动 make、预留 GPU 或恢复旧队列 |

在已确认来源、资源及本次执行授权后，执行用法为：

```console
python -m rpipe run <study-dir> --run-root <external-run-root>
python -m rpipe process <study-dir> --run-root <external-run-root>
```

已有正式 result 拒绝原地重跑，须使用显式新 version。无 result 的已确认 failed/cancelled 仅允许显式 --retry（API retry=True），unknown/busy 不重放。execution.json 明确索引当前 attempt，执行、科学结果、派生分析、证据完整度分列，不由 mtime、UUID、退出码或日志推断整体成功。

provenance.inputs 可固定外部文件或目录清单，未固定 path/stats_path、默认缓存与隐式 profile 分列未验证。旧内部导入、冻结归档、隐式运行目录、GPU 轮次和阶段裁剪不自动恢复，迁移表见代码结构设计。run(..., payload=...) 保留调用兼容，不代表新增第三个源码层。

### 资源监管与当前限制

默认 Budget 拒绝硬内存限制和同进程硬超时。Windows 有启用的 Study hook、recipe、DataLoader worker 或 HF 后端时，普通 run 的硬超时仍拒绝。CLI 没有完整 Ctrl+C 合作取消桥接，不承诺任意键盘中断均完成所属资源清理。

Windows 可另选 api.supervise / rpipe supervise。CLI 必须显式提供 --run-root、--evidence-dir、--run-seconds、--batch-seconds、--rss-bytes、--output-bytes，另可设置 --poll-seconds 与 --cleanup-seconds。匿名 Job 监管本批协调者、worker 和后代，入组后才放行，首败、质量 False、证据不全、观察失败或超限即停后续派发。

RSS 与输出是采样停止线，可能漏峰或超调，**不是硬内存或磁盘配额**。须使用专用外部运行根与新建独立证据根，保留原 Run owner/unknown 协议，其他平台拒绝此入口。当前仅有微型 CPU 合同证据，正式资源校准和研究矩阵仍待完成，不能把研究计划提案当已批准预算。详细计量与清理合同见架构第四章。

## 验证与证据

统一入口为 [tests/run.py](tests/run.py)，按标签与成本选择，结果根在 checkout 外，每个 pytest 进程指定独立临时目录。以下为后续受控 CPU 检查用法，不是本轮执行结果：

```console
python tests/run.py --help
python tests/run.py --core --results-root <external-test-root> --timeout-seconds 600 --budget-seconds 660 -- --basetemp <unique-external-temp>
```

结果包含 manifest、逐项 events 与可重建 report，未执行、跳过、取消、缺失与失败分别保留。执行器只监督本次 pytest 根进程，不提供逐项内存或全部后代硬限。制品验证另用 [tests/package_smoke.py](tests/package_smoke.py)，针对实际安装 wheel/sdist 的解释器与新的外部 workspace，不用 editable 导入替代。

现有工作流在 dev/main 的 PR 和 push 上运行 CPU 矩阵（Ubuntu/Windows × Python3.10/3.13）及两平台 Python3.10 打包安装，另在 PR 检查分支方向。当前没有失败/中断证据 artifact 上传，见 BR1。工作流声明、远端实际结果与分支保护不是同一事实，不把配置存在记为当前 HEAD 已通过。

历史本地有界 CPU、有限原生/HF、安装 smoke 与远端 CI 各有固定身份，见通用调研摘要和运行记录。本轮只做文档/源码事实核对，不运行本地项目测试、安装或科学实验，不做桌面新对话验证。正式 MNIST/CIFAR、GPU、长程性能、矩阵外环境与原始历史归档可恢复性仍未验收。

## 宿主接入与资料来源

下游固定完整上游提交，整体同步 src/rpipe/，记录完整 SHA、成员/哈希并保留 MIT。dayan、SD、量化的内嵌副本不得自行修改，问题先在 RPipe 修复验证，再按单独授权整套同步。dayan [#171](https://github.com/DreamSoul-AI/dayan/pull/171) 固定的是 #229 上游来源，有限 CPU Engine 证据不表示其已同步 #231–#233，也不外推其他宿主。SD、量化同步状态沿历史记录，本轮未读取或修改它们。

数据、权重、日志、trace、图、checkpoint、源码快照与大报告保存在显式外部根，不进 Git。studies/main_exp/docs/ 的两个 JSON 是有实际消费者的静态科学输入，原路径与 pin 保留，不按扩展名删除。历史 Run 不因文档改名回签身份。

共用资料从 [DreamSoul GitHub 组织](https://github.com/DreamSoul-AI)、[公司资料入口](https://dreamsoul.feishu.cn/drive/folder/HFFFfNlvelicdsd46PCcrVZ2n4g)、[规范总索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 与 [工具教程索引](https://dreamsoul.feishu.cn/docx/VSQ5dlJl3o0hbIxrEhUc8qKEnBc) 定位。[研发用 AGENTS.md](https://dreamsoul.feishu.cn/docx/QICWdeEokobO9RxYR9zcjE6Pnyg)、[通用调研](https://dreamsoul.feishu.cn/docx/XOwxdH7aYoplBjx7JCFcZCQ5n9f)、[使用教程](https://dreamsoul.feishu.cn/docx/JrUgdOKh2oJ9SBxosWyc4Dk7nIh) 提供共用方法，采用范围见 AGENTS 与运行记录。架构粒度参考 [固定 CPU 内存清理样板](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/6938f10447b250958856a845fc5448aa1185b52d)，不把案例或其他仓库指令当本仓授权。

本轮文档以独立 Draft PR 供审阅，代码、实验、合并、发布与下游同步按各自授权推进，当前停止点见开发计划。
