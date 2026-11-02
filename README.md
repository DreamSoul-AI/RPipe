# RPipe

RPipe（Research Pipeline，研究流水线）是用于搭建研究实验的参考模板，也是开始一个研究项目的 warm-up / starting point。研究者可以复制或 fork 本仓，从中学习如何组织实验部件、执行流程、配置和结果，再直接修改代码以适应自己的课题。后续项目可以继续拆合模块、替换实现和调整流程，由该项目自行维护。

RPipe 的交付对象是可阅读、可修改的仓库起点，不以供其他项目长期安装调用的通用 package 为产品目标，也不要求衍生项目持续跟随本仓接口。模板自身仍需讲清职责、保持可运行并验证示例，帮助研究者把有效配置、执行事实、科学结果和比较分析连接起来。Python 包名 `rpipe`、安装命令和打包配置是当前源码的组织与运行方式，不构成跨项目稳定 API 的承诺。仓库为 [DreamSoul-AI/RPipe](https://github.com/DreamSoul-AI/RPipe)，采用 [MIT 许可](LICENSE.md)。

当前版本 **v0.5.0 已发布**，发布提交为 a33f057940e79a5a0e19c0713855566d13e9c4ab，制品及版本说明见 [GitHub Release](https://github.com/DreamSoul-AI/RPipe/releases/tag/v0.5.0)。五批核心重构及研究接入工程验证已本地提交；main_probe在固定环境、seed0和每侧60步条件下，8格CUDA配对及Study终验通过，见[本轮运行记录](<docs/运行记录 Run Record/2026-10-30 main_probe CUDA配对验证.md>)。首轮缺历史依赖的失败保留原身份，没有回签。架构和代码结构设计各以一份完整Markdown维护当前目标，剩余工作从[开发计划](<docs/开发计划 Development Plan 研究流水线.md>)进入。main_exp八格600步CUDA配对及039c941全新version的完整E4已按固定协议通过，证据与适用范围见[新电脑执行记录](<docs/运行记录 Run Record/2026-10-30 新电脑main_exp完整矩阵执行.md>)。测试组织、模块无环、外部资源与模型合同四项整改已完成，见[四项整改验证](<docs/运行记录 Run Record/2026-11-02 四项整改验证.md>)。后续预算冲突拒绝、完整使用说明与plan --show-config已在c4eed0f完成，616项受影响回归及该提交自身9项CI成功，见[模板使用验证](<docs/运行记录 Run Record/2026-11-02 模板使用走查.md>)。#236已合入dev，版本号不表示本轮已发布。

## 文档与阅读顺序

本轮使用与科学验证所绑定的代码f1f012bd6d8b2e601fbbd0ea30856c6b0f88e314已完成研究者完整使用验收：复制模板、两个条件/两个seed的训练及自身best评测、读懂报告、实际修改模型后用新version继续，共16个Run通过，旧版全部文件哈希保全。报告可读性缺口已补，受影响899项回归通过（另1项既定静态豁免），该提交自身9项CI成功。流程与结果示例见[运行记录第十八章](<docs/运行记录 Run Record/2026-11-02 模板使用走查.md#十八最终候选的完整使用与二次开发验收>)。

同一根仓候选的真实八格E0及main_exp八格seed0/每侧600步CUDA短配对已通过，horizon80000、原数据和原数值门保持；16份best/latest CPU状态读回及结束后16项数据哈希复核通过。新科学来源为53b6745f/76成员，详见[第二十一章](<docs/运行记录 Run Record/2026-11-02 模板使用走查.md#二十一最终候选八格600步短配对与收尾>)。完整E4仍绑定039c941历史身份，本次没有重跑。最新报告增量已在固定2959a88取得独立代码复核，16个Run及60组统计重算匹配、两版各100文件读取前后不变，未发现主流程阻断，见[第二十二章](<docs/运行记录 Run Record/2026-11-02 模板使用走查.md#二十二最新报告改动的独立增量复核>)。本轮模板实施与验证已完成，未发现待修主流程问题。用户于2026年11月3日确认旧独立实验不再追查，旧来源取证移出当前范围，历史记录与结果身份保留。交付状态见[开发计划第六章](<docs/开发计划 Development Plan 研究流水线.md#六研究者使用主流程与下一阶段开发计划>)及[收尾记录](<docs/开发记录 Development Record/2026-11-03 模板交付收尾.md>)；#236于2026年11月3日经用户授权转Ready并合入dev，当时main保持不变；后续v0.5.0发布进展与合并记录见[当日开发记录](<docs/开发记录 Development Record/2026-11-03 模板交付收尾.md#四236合并dev与仓库事项盘点>)。

2026年10月31日已安全同步交接候选c5b12d6并保留本地草稿，独立审查已覆盖#236相对dev的完整差异。待办与停止点见[开发计划](<docs/开发计划 Development Plan 研究流水线.md>)；规范复核依据见[复核记录](<docs/运行记录 Run Record/2026-10-31 规范复核与计划重排.md>)。原科学证据保持其SHA，新工程修复不继承旧E4验收。

研发协作的共用依据为 [研发用 AGENTS.md](https://dreamsoul.feishu.cn/docx/QICWdeEokobO9RxYR9zcjE6Pnyg)。采用其适用正文，并结合本仓 [AGENTS.md](AGENTS.md) 的项目约定执行。共用《研发用 AGENTS.md》与资料库根 AGENTS.md 是不同文档，不得相互替代。本地源与飞书正文需核对版本，不能以本仓规则已更新推定共用正文已同步。

先读本 README、共用《研发用 AGENTS.md》和本仓项目约定，再按问题进入以下唯一成果。开发与研究的计划独立维护，日期记录保存实际演进与证据，不另建同义进度、交接或验收报告。

| **成果** | **当前内容与边界** |
| --- | --- |
| [通用调研 General Research](<docs/通用调研 General Research 研究流水线.md>) | 机制、工程路线、直接来源、取舍与限制；项目历史执行只进入日期记录 |
| [架构设计 Architecture Design](<docs/架构设计 Architecture Design 研究流水线.md>) | 单一完整正文：当前选定的实验部件、流程扩展、观测恢复及产物合同 |
| [代码结构设计 Code Structure Design](<docs/代码结构设计 Code Structure Design 研究流水线.md>) | 依据完整架构推导的单一文档：目标目录、模块职责、接口、允许依赖及验证落点；规划/发布、生命周期、部件装配、快照读端、入口交付及本轮研究接入已实施，验收范围见开发计划 |
| [开发计划 Development Plan](<docs/开发计划 Development Plan 研究流水线.md>) | 实现差距、逐模块工作、旧接口清理及验收顺序；按明确目标队列连续推进，每批验证、记录并本地提交 |
| [缺陷记录 Bug Report](<docs/缺陷记录 Bug Report 研究流水线.md>) | 活动问题与待验证事项；已关闭问题的变更和证据只从日期记录追溯 |
| [开发记录 Development Record](<docs/开发记录 Development Record/>) | 日期记录，本轮文档调整与历史集成、分支保全的归属；不复制整份测试结果 |
| [运行记录 Run Record](<docs/运行记录 Run Record/>) | 各次静态、CPU、安装及 CI 验证的实际身份、结果和缺口；原始证据在 Git 外 |
| [文献调研 Literature Review](<docs/文献调研 Literature Review 研究流水线.md>) | 按问题比较六篇原始研究的方法、证据和限制，注明阅读深度；非穷尽系统综述，无外部优越性结论 |
| [研究计划 Research Plan](<docs/研究计划 Research Plan 研究流水线.md>) | 唯一中文研究计划，RQ1–RQ4、全部 Study 的 E0–E4 设计与判据；main_probe E3和main_exp600步配对已有固定证据，新电脑039c941全新version的32train/32eval及完整E4科学门已通过，适用范围见运行记录 |

项目演进、旧文档归并与兼容迁移从[开发记录](<docs/开发记录 Development Record/2026-10-27 文档重构与历史归属.md>)追溯，实际历史验证从[运行记录](<docs/运行记录 Run Record/2026-10-27 历史验证整理.md>)追溯。记录保留原提交、日期及运行身份。

项目经验集中在 [Workspace 沉淀](<docs/Workspace 沉淀/Experience.md>)，记录问题、实际方法、工具、验证依据及未确认项，保留原有共用AGENTS改进候选。workspace统一按来源项目汇集；成熟建议经审阅确认后再进入issue和模板PR，不因经验收集自动生效。项目采用的专项规范仍须执行，共用入口不另定布局不构成豁免理由。

## 技术栈与目录

Python **3.10+**，setuptools 的 `src` 布局。实际依赖为 PyTorch、torchvision、NumPy、SciPy、PyYAML、matplotlib、kornia。`dev`、`tensorboard`、`hf` 为可选依赖组，准确范围见 [pyproject.toml](pyproject.toml)。元数据的支持范围不代表所有版本组合已验证。

| **位置** | **职责** |
| --- | --- |
| [src/rpipe/](src/rpipe/) | 模板主体；当前 structure 提供声明、产物机制及 data/model/algorithm/system，flow 拥有阶段、执行权、所属科学进程及唯一正式写入；后续工程核验与研究接入差距见开发计划 |
| [studies/](studies/) | `_template` 示例与 main_probe/main_exp 声明、研究适配及必要静态科学输入 |
| [tests/](tests/) | 本仓源码/Study与测试程序自身镜像、最小合成输入、统一执行器和安装 smoke；[目标映射与例外](tests/README.md) |
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

### 数据、模型与大产出（本项目约定）

本节作为本项目的执行约定，共用条款仍按跨项目适用性评估。2026年11月2日已用独立小文件核部分Windows本机能力，具体实测范围见本节末及[运行记录](<docs/运行记录 Run Record/2026-11-02 四项整改验证.md>)，不外推未验证平台或后端。已有长实验使用其固定路径与运行身份，本轮不在运行中迁移输入、缓存或产出。

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

3. 仓内需要便捷访问时，设计指向上述外部位置的symbolic link入口，并登记目标和用途；链接本身与运行物不进Git。现有`/data`、`/model`、`/output`、`/shared`入口及Study运行物已在[.gitignore](.gitignore)中排除，新入口另核实际忽略效果。科学参数、schema、固定输入清单、来源元数据及许可证继续按职责版本化，不按扩展名一概忽略。
4. 当前实际执行使用解析后的真实绝对根。受监管输出树中的shared/data目录须保持实体目录，不能用指向树外的目录符号链接或junction代替；Windows输出监管会拒绝这类目录链接。因此仓内便捷入口与运行树内部链接须分别设计，不能把仓内入口直接当作已支持的监管路径。
5. 原始文件复用优先使用明确的文件级symbolic link；现有artifact.io.link_input/reference_tree在符号链接不可用时可回退到同卷硬链接，使用前检查条件，实际类型和目标须记录。不要把整个多项目数据/模型根递归挂进Study，不静默复制大文件作为链接替代。模型后端是否支持外部权重及链接另核实际构造入口，不将文件链接能力外推为所有后端支持。
6. 链接和硬链接都不构成写保护。共享原始输入按来源和固定哈希核验，可写派生与其分开；变更或清理前核消费者和执行占用。移除链接入口不得递归删除真实目标，未知文件、旧Run、权重及无关项目产出继续按仓库清理授权处理。
7. 本机已实测：plan/publish将symlink/junction访问别名解析为实体运行根；output_snapshot拒绝直接目录别名而接受实体根，文件引用按身份去重；无symlink权限时同卷硬链回退，实际C/D跨卷以明确跨卷错误拒绝，未静默复制；Git忽略真实入口，静态科学配置继续保留；手工unlink/rmdir入口后真实目标及peer保全。这些用例不等于完整supervise旅程或自动清理器验收，也不证明链接具有只读保护。模型后端的外部权重路径、其他系统及实际用户目录迁移未执行，后续按实际消费者验证；没有使用真实数据或旧Run作清理fixture。

### 从模板开始一个小研究

以下先完成一次无下载的原生 CPU 训练及自身 best 独立评测，再替换成自己的数据和方法。示例只验证模板使用，不构成研究精度结论。先在已有隔离环境确认当前 checkout 的导入位置，不需要安装另一份同版本包。

1. 在仓库根复制模板，不手写 runs。PowerShell 示例：

```powershell
Copy-Item -Recurse studies/_template studies/my_study
```

2. 完整替换副本的两份声明，去掉模板原有 fixed、train_size 轴、20 epoch 和 CUDA 条件。配置合并顺序为 experiment_config.yaml → study.yaml.fixed → axes 当前取值，嵌套 algorithm.config 内同名内容条件优先于外层扩展字段。训练只能给出 num_epochs 或 num_steps 中的一种非空预算，progress_unit 表达计数单位，不负责消除另一预算。

`studies/my_study/study.yaml`：

```yaml
study: my_study
origin: foreign
recipe: recipe.py
experiment: {name: tiny_cpu}
axes:
  algorithm.mode: [train, eval]
seeds: [0]
run_description: "mode={mode} seed={seed}"
```

`studies/my_study/experiment_config.yaml`：

```yaml
version: first-cpu-v1
experiment: tiny_cpu
data:
  name: TinyCPU
  source: study
  config: {batch_size: 4}
model: {name: linear, source: custom_torch}
algorithm:
  source: custom_torch
  num_steps: 2
  progress_unit: step
  eval_period: 1
  checkpoint: latest
  checkpoint_period: 1
  save_best: true
  lr: 0.05
system: {device: cpu, deterministic: true, cudnn_benchmark: false}
```

3. 新建副本内的 recipe.py。这个例子只替换数据，模型和算法继续使用模板主体中的原生实现。builder 交付实际数据元信息，不能靠模型参数静默覆盖真实形状或类别。

`studies/my_study/recipe.py`：

```python
from rpipe.structure.data.factory import Data, DataRegistry
from rpipe.structure.system.runtime import make_generator

def register(ctx):
    def build(config, assets, seed=None):
        import torch
        from torch.utils.data import DataLoader, TensorDataset

        x = torch.arange(16, dtype=torch.float32).reshape(8, 2) / 16
        y = (torch.arange(8) % 2).long()
        dataset = TensorDataset(x, y)
        batch_size = config.config['batch_size']
        return Data(name='TinyCPU', source='study', loaders={
            'train': DataLoader(dataset, batch_size=batch_size, shuffle=True,
                                generator=make_generator(seed)),
            'test': DataLoader(dataset, batch_size=batch_size),
        }, meta={'seed': seed, 'config': dict(config.config), 'data_size': [2],
                 'target_size': 2, 'batch_size': batch_size, 'train_size': len(dataset)})

    DataRegistry.register('TinyCPU', 'study', build)
```

自己的数据、模型、算法实现可分别落在 Study 的 structure/data、structure/model、structure/algorithm，再由 recipe 显式连接实际构造点。模型使用 model.factory.ModelRegistry，算法使用 algorithm.factory.AlgorithmRegistry，循环合同在 algorithm.base。可以直接修改本仓主体，无需先包装成插件或保留跨项目稳定接口。目录骨架本身不会自动注册实现。

仅在需要阶段协作时，于 study.yaml 显式设置 flow: {study_phases: true}，再在 flow/<阶段>/__init__.py 提供 run(ctx)，或采用同名 flow/<阶段>.py。prepare 可另提供 before(ctx)，在科学部件装配前执行。不要为启用而填满六个空 hook，也不要在研究 hook 另开正式结果写端，具体边界见两份设计。

4. 填入已确认、与源码和输入分离的真实绝对输出根。下面的占位符须替换，不能原样执行：

```powershell
$runRoot = '<已确认的外部绝对输出根>'
python -m rpipe plan studies/my_study --run-root $runRoot --show-config
python -m rpipe make studies/my_study --run-root $runRoot
python -m rpipe launch studies/my_study --run-root $runRoot
python -m rpipe process studies/my_study --run-root $runRoot
python -m rpipe status studies/my_study --run-root $runRoot
python -m rpipe report studies/my_study --run-root $runRoot
python -m rpipe logs studies/my_study --run-root $runRoot
```

plan --show-config 是只读有效配置预览，先核两个 Run、train_id、first-cpu-v1、CPU、单一2步预算及数据/模型条件。它不运行 recipe 或训练，既有 plan 默认只显示身份和路径。配置预览不能代替实际数据、设备和精度准入。已有发布可用 run 合并 make 与 launch，例中分开执行便于核对各阶段。

5. 运行物位于 `<runRoot>/my_study/first-cpu-v1/`，shared 缓存位于外部根的 shared。按实际 run_dir 查看下列文件：

| **位置** | **查看什么** |
| --- | --- |
| index.json、runs/<id>/config.yaml | 正式清单、train_id 及保存的有效配置 |
| runs/<id>/execution.json | 当前 attempt、执行与收尾事实，不用日志猜终态 |
| runs/<id>/result.json | status、quality、evidence_complete、metrics 和 execute。train 的 execute.steps 应为2，eval 的 execute.resume_path 应指向对应训练的 best.pt |
| runs/<id>/assets/checkpoints/、assets/tracker/scalars.jsonl | 实际 best/latest 快照及有效数字轨迹 |
| runs/<id>/assets/logs/events.jsonl | 带 Run/attempt/PID 的结构化事件 |

process 输出 Study 派生摘要，需要曲线时显式 process --figures。科学结果、执行成功与质量判断分别核对，进程退出0不能单独证明研究完成。修改数据、模型、算法或源码后使用明确的新 version，重新 plan/make/run，不给已成功的旧 Run 原地换来源。status/report/logs 可显式 --version first-cpu-v1 查看旧记录。扩大数据、步数或设备前先更新研究计划及实际准入条件。

需要比较条件和复测时，可在该例的axes中增加`algorithm.lr: [0.01, 0.05]`，将seeds改为`[0, 1]`，得到4个train与4个eval。`report`在同一份Markdown中提供Run状态、seed、单次指标、Experiment条件映射及跨seed均值/标准差/n/完整性。表内E1等是临时展示标签，Saved identity列保留正式Experiment身份，不能用展示标签代替Run ID。条件摘要突出实际变化字段及数据/模型/算法模式，完整配置仍以`plan --show-config`或保存的config.yaml为准。

报告浮点值按6位有效数字呈现，原始JSON与轨迹不改。通用跨seed汇总沿现有样本标准差定义，n≥2时ddof=1，缺项和未提供质量判定保持可见；main_exp固定科学验收的ddof=0由其研究协议和正式比较报告承接，二者用途不同。研究代码修改后的新版本应重新执行并保留旧源码/配置快照，使用显式`--version`回看旧结果。跨版本比较先看条件和来源差异，改了模型后比较不通过不等于运行失败。

### 计划发布与执行

发布先写完整配置，最后以单次原子替换提交 schema 3 的 index，来源清单与运行索引在同一记录内，所有目标 Run 在提交前持续持权。重复发布不改已有配置字节；既有配置、结果、执行事实或计划来源不匹配时拒绝，不自动给旧记录换身份。需要新实测时选择明确新 version；不提供旧格式回退。

先用只读计划核对 Run 数量、版本、依赖和输出身份。CLI plan --show-config 可打印每个 Run 的完整有效配置，默认 plan 保持身份与路径输出。设备、训练预算与部件参数也可通过程序化 RunSpec.to_config()，或 make 后的 config.yaml 核对。基底配置先合并 study.yaml 的 fixed，再由 axes 取值覆盖相应条件。以下外部路径由调用方替换，不能将占位符作为实际路径：

```console
python -m rpipe plan studies/_template --run-root <external-run-root>
python -m rpipe status studies/_template --run-root <external-run-root>
python -m rpipe report studies/_template --run-root <external-run-root>
```

复制模板后的完整短流程见[从模板开始一个小研究](#从模板开始一个小研究)，已完成整改与验收见[开发计划](<docs/开发计划 Development Plan 研究流水线.md#四模板使用走查与下一批工作>)。训练的有效 num_epochs 和 num_steps 同时非空时拒绝。切换成纯步数训练时，清除不再采用的 epoch 条件，或在实际覆盖位置显式设为 null，再核有效配置。合法 epoch 预算内部推导更新数不属于第二份输入。

**`_template` 当前是 MNIST/CUDA、20 epoch 示例，不是离线 CPU smoke。** 复制后先在研究计划登记问题、条件和判据，再核有效声明、数据/模型来源、设备、精度与预算。plan 不下载或训练。模板配置注释指向本节。离线合成安装旅程由 tests/package_smoke.py 提供，不能外推到模板科学实验。

| **命令** | **作用与边界** |
| --- | --- |
| `plan [--show-config]` / `shared` | 只读当前声明，可显式预览每个Run有效配置，不启动、清理或补写 |
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

RSS 与输出是采样停止线，可能漏峰或超调，**不是硬内存或磁盘配额**。须使用专用外部运行根与新建独立证据根，保留原 Run owner/unknown 协议，其他平台拒绝此入口。用户已授权按本机硬件与准入实测自主设置常规预算；换机须重新核环境、硬件和停止线。039c941的完整研究矩阵已按记录验收，当前工程候选未重新做完整矩阵，不继承旧SHA的科学验收。详细计量与清理合同见架构第四章；本次停止本机实验、推送与换机重新执行的边界见[交接记录](<docs/开发记录 Development Record/2026-10-30 跨电脑交接.md>)。

## 验证与证据

统一入口为 [tests/run.py](tests/run.py)，按标签与成本选择，结果根在 checkout 外，每个 pytest 进程指定独立临时目录。以下为后续受控 CPU 检查用法，不是本轮执行结果：

```console
python tests/run.py --help
python tests/run.py --core --results-root <external-test-root> --timeout-seconds 600 --budget-seconds 660 -- --basetemp <unique-external-temp>
```

结果包含 manifest、逐项 events 与可重建 report，未执行、跳过、取消、缺失与失败分别保留。执行器只监督本次 pytest 根进程，不提供逐项内存或全部后代硬限。制品验证另用 [tests/package_smoke.py](tests/package_smoke.py)，须用本次实际 wheel/sdist 安装到新 venv 的解释器，显式 --artifact 指明制品和 --workspace 指明新外部位置；核 venv/导入/console/RECORD 与制品来源哈希，不用 editable、外来 PYTHONPATH 或旧同版本包替代。

现有工作流在 dev/main 的 PR 和 push 上运行 CPU 矩阵（Ubuntu/Windows × Python3.10/3.13）及两平台 Python3.10 打包安装，另在 PR 检查分支方向。CPU 作业归档统一测试回执和命令日志，打包作业归档构建/安装日志及已生成的 wheel/sdist smoke 工作区。上传在成功或失败后均尝试执行，缺少文件时明确失败；artifact 名包含矩阵、Actions run/attempt 和检出 SHA，保留7天。日志管道使用 Bash pipefail，不吞测试或构建失败。平台强制终止、runner 丢失或上传服务不可用时可能无完整归档，不能将未上传当作通过。工作流声明、远端实际结果与分支保护不是同一事实，不把配置存在记为当前 HEAD 已通过。

当前已验收工程代码为f1f012bd6d8b2e601fbbd0ea30856c6b0f88e314，其自身9项CI均成功，实际使用、真实E0及600步范围见[模板使用验证第十八至二十一章](<docs/运行记录 Run Record/2026-11-02 模板使用走查.md#十八最终候选的完整使用与二次开发验收>)；后续文档收尾提交自身CI在交付时按真实HEAD另核。c4eed0f的预算拒绝、历史只读边界、原生训练与恢复、配置规划、CLI及README原文复现保留原执行身份。Windows/Ubuntu两版Python的CPU、真实TensorBoard及两平台严格安装检查均通过，平台跳过与未执行范围分别保留。当前源码的完整科学矩阵、专门的长程性能评价、矩阵外环境及原始历史归档可恢复性未另行验收。

安装最小旅程使用显式Toy/Stub，README使用8个合成样本的两步CPU训练及自身best独立评测，均为工程验证。main_probe/main_exp既有CUDA配对及039c941完整E4按原SHA、环境和配方保留，不能据本轮工程通过回签。更早各批的命令、结果与失败从[运行记录](<docs/运行记录 Run Record/>)追溯，README不重复维护逐批通过数。

来源完整度按范围解释：通用 input_provenance 只观察声明及默认引用，Study 自定义输入另保存核验证据。已完成八格的准备、raw 哈希及归档关联经[只读复核](<docs/运行记录 Run Record/2026-10-30 来源边界与准入选择验证.md>)确认，未改写旧 Run。main_exp preflight 已拒绝空、重复或不支持的显式组合，避免意外扩大执行；600步实际执行证据见当前研究状态，不能用该离线修复代替。

## 宿主接入与资料来源

下游固定完整上游提交，整体同步 src/rpipe/，记录完整 SHA、成员/哈希并保留 MIT。dayan、SD、量化的内嵌副本不得自行修改，问题先在 RPipe 修复验证，再按单独授权整套同步。宿主集成身份与验证记录只在日期记录维护。共用文档职责的同步不包含内嵌库、源码、测试或配置同步。

数据、权重、日志、trace、图、checkpoint、源码快照与大报告保存在显式外部根，不进 Git。studies/main_exp/docs/ 的两个 JSON 是有实际消费者的静态科学输入，原路径与 pin 保留，不按扩展名删除。历史 Run 不因文档改名回签身份。

共用资料从 [DreamSoul GitHub 组织](https://github.com/DreamSoul-AI)、[公司资料入口](https://dreamsoul.feishu.cn/drive/folder/HFFFfNlvelicdsd46PCcrVZ2n4g)、[规范总索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 与 [工具教程索引](https://dreamsoul.feishu.cn/docx/VSQ5dlJl3o0hbIxrEhUc8qKEnBc) 定位。[研发用 AGENTS.md](https://dreamsoul.feishu.cn/docx/QICWdeEokobO9RxYR9zcjE6Pnyg)、[通用调研](https://dreamsoul.feishu.cn/docx/XOwxdH7aYoplBjx7JCFcZCQ5n9f)、[使用教程](https://dreamsoul.feishu.cn/docx/JrUgdOKh2oJ9SBxosWyc4Dk7nIh) 提供共用方法，采用范围见 AGENTS 与运行记录。架构粒度参考 [固定 CPU 内存清理样板](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/6938f10447b250958856a845fc5448aa1185b52d)，不把案例或其他仓库指令当本仓授权。

当前开发任务、建议顺序与执行边界见开发计划。代码、实验、合并、发布与下游同步按各自明确范围推进。

当前用户已要求持续完成开发计划。研究资源由Agent按[研究计划的硬件自适应规则](<docs/研究计划 Research Plan 研究流水线.md>)自主测量与分配，不再逐次询问常规预算；科学参数和门限保持，首败停止。main_exp八格CPU准入及[600步CUDA配对](<docs/运行记录 Run Record/2026-10-30 main_exp 600步配对验证.md>)已通过，新电脑039c941完整曲线已按固定协议验收，当前工程修复保留独立候选身份。pytest使用importlib导入，允许镜像目录中的同名测试完整收集；本机原生DLL启动条件与候选制品验证范围从[本批记录](<docs/运行记录 Run Record/2026-10-30 硬件自适应与交付准入验证.md>)读取，不推定所有机器环境相同。

新电脑main_exp完整32train/32eval矩阵已按039c941及新version从头完成，完整曲线、四seed统计、自身best独立eval、固定参考及模型顺序门均通过，正式资源收尾完成；实际环境、证据与适用范围从[新电脑执行记录](<docs/运行记录 Run Record/2026-10-30 新电脑main_exp完整矩阵执行.md>)追溯；[旧电脑执行记录](<docs/运行记录 Run Record/2026-10-30 main_exp完整矩阵执行.md>)保留交接中断事实，不拼接。该验收不外推其他机器、配方或总体统计等价。采用本轮结构的新课题应以当前两份设计和示例直接组织研究代码，使用新version保留来源。旧接口和旧checkpoint不作新版兼容承诺；需要复查的历史Run保留其原源码与环境，不给旧成果换身份，也不要求下游迁移。
