更新日期：2026年10月28日。实现基线为 dev `19e1e74a911a84ebe9e6f3251649315ca4fcd309`。本计划维护工程工作、依赖和验收，科学问题与实验设计由 [研究计划](<研究计划 Research Plan 研究流水线.md>) 维护。

# 一、目标与执行边界

最新定位校准：RPipe 是供人复制、参考并直接修改的研究流水线模板。第六章据此复核现有架构与文件结构；此前两份设计和下表的模块落位需要重新审定，不能直接作为已定案的实施任务。先恢复实验组成与过程的语义说明，再决定文件拆合，不围绕长期公共 package 或下游兼容设计。

当前阶段只更新 Markdown，不继续修改代码。目标先构建职责合理、干净整洁的 RPipe 核心，保留两柱原则，内部结构按职责重新梳理。旧接口及其兼容层全部退出目标，不因现有 Study、测试或下游调用继续保留。目标需要的能力和单一正式入口从架构推导，核心稳定后再按新接口重新编写本仓研究接入。

本轮把此前“有调用先保留、候选再决定”的方案改为明确的旧接口清理目标，现有调用清单只作影响登记，不作为设计保留理由。用户已明确先搞定 RPipe，不迁移下游；Study 可以重写，本轮不安排其详细重接工作。只同步受影响 Markdown 并做静态检查，不删除或修改代码、Study、测试、配置及历史成果。用户最新授权将这轮文档提交到当前工作分支，仅本地 commit；不推送、合并、发布、改 main、写飞书或同步下游。代码工作仍等待用户明确恢复。

# 二、重构顺序与验收

| **批次** | **核对和调整范围** | **验收** | **状态** |
| --- | --- | --- | --- |
| DP4.1 核心目标结构与正式入口 | 按两份目标设计落实两柱内职责、状态所有者、依赖及正式入口；来源身份、受保护 I/O、执行、读端及共用训练状态分别落位 | 实现符合目标合同，差距和工作只在本计划登记，不把旧接口或改造步骤写回设计 | 目标设计已独立成文，当前不实施代码 |
| DP4.6 旧接口及兼容层退出 | 依据本计划第五章的接口清理工作表，清理旧转导出、忽略参数、payload 注入、legacy 读取/比较、旧 sibling、共享准备入口、路径/周期别名、无操作配置键、历史类路径及旧扩展签名 | 不留兼容别名、转发壳、双格式回退或自动转换框架；保全已有数据、原始证据及身份，不承诺新版读取或恢复旧成果 | 退出方向已明确，不再按旧消费者决定保留；仅文档目标，未删除代码 |
| DP4.3 核心科学装配 | 按目标定义科学组件与运行流程的正式连接、注册或构造作用域、状态存取和资源收尾，不照搬旧 Factory/Registry/hook 签名 | 使用新合同的合成 CPU 场景核装配、隔离、清理失败及目标恢复，不引入第二套执行框架 | 待核心方案确定及代码工作恢复 |
| DP4.4 入口与故障收尾 | 在重构后的实际执行链核对 CLI/API 的状态呈现、取消、重试、unknown 和后续派发。普通 CLI 中断作为其中一项缺口，不能代替整体重构 | 受控子进程及失败反例，确认清理与退出一致，不误报成功或操作无关资源 | 待前序核对后实施 |
| DP4.5 核心验证与文档回写 | 围绕新正式合同更新测试、fixture、CLI 与实际 wheel/sdist 旅程；旧接口专属测试随旧接口退出，需求级反例迁到新合同 | 核心能力、研究待重接及科学未验收分别报告，不靠静默跳过旧 Study 测试声称全仓完成 | 待核心实现范围稳定 |
| DP4.7 核心稳定后重新接入研究 | 按新正式入口重新编写需要的 main_exp/main_probe 接入及对应测试，不要求旧脚本或旧 checkpoint 在新版直接可用 | 科学来源、数据、算法语义、条件和判据单独核对，使用新代码身份运行，不回签历史成果 | 后续阶段，本轮不设计或实施具体重写，不迁移下游 |
| DP5 发布与兼容准备 | 元数据仍为 0.4.1，发布前确认候选提交、版本、迁移与交付对象 | 许可证、制品及受影响平台与兼容旅程完成，发布另获授权 | 未启动 |

未来重构保全已有数据、正式结果、原始证据、许可证及其历史身份，但不为此保留旧调用、读取、恢复或转换接口。目标接口可以破坏旧用法；Study 和旧接口测试围绕新合同重写，不通过兼容壳拖住结构。需要的科学能力及质量原则仍由需求决定，研究语义变化单独校准，旧成果不改签为新实现结果。已完成的代码和历史验证只从日期记录追溯。

现有调用证据保留为影响记录，不再为是否退出旧接口增加门槛。实现中若发现目标合同无法支撑必要行为，应说明具体矛盾并校准相应设计；实施差距、清理步骤及未执行项继续维护在本计划，不在设计中并列目标与旧接口，也不把文档目标直接转成代码删除。

# 三、研究衔接

工程重构及测试通过不代替研究 E0–E4。正式实验前固定新代码 SHA/version、原始输入、数据与模型、算法、指标、设备与精度、依赖、seed、外部运行及归档根、资源预算和停止条件，并另行取得执行授权。历史结果不补签为重构后验证。

# 四、记录与交付

完成经过见 [开发记录](<开发记录 Development Record/2026-10-28 研发约定核对与代码重构.md>)，实际命令及证据见 [运行记录](<运行记录 Run Record/2026-10-28 研发约定核对与重构验证.md>)。每批更新实际状态，未完成批次保持待办，不把当前首批改动记为全仓重构完成。

# 五、现状核对与接口清理工作

## （一）逐模块实施影响单

下表保留梳理时的 50 个包文件和直接消费证据，供实施定位、改写和验收安排使用。它描述现状到目标的工作，不属于目标结构规范。目标模块及职责以代码结构设计为准；其中来源身份由 identity 承接，原子 I/O 与执行权原语归 artifacts，共用训练状态归 algorithm/state，学习率观察归 optim。具体研究在核心稳定后按正式接入合同重写。

| **当前文件** | **应承担的职责** | **已定位的消费依据** | **保留或调整及理由** |
| --- | --- | --- | --- |
| [__init__.py](../src/rpipe/__init__.py) | 包标识和版本 | 源码：[src/rpipe/flow/cli.py](../src/rpipe/flow/cli.py) 等 3 文件；研究：[studies/main_exp/flow/execute/capacity.py](../studies/main_exp/flow/execute/capacity.py) 等 2 文件；测试：[tests/package_smoke.py](../tests/package_smoke.py) 等 2 文件；[pyproject 包发现](../pyproject.toml) | 保留；包根只暴露版本，其余包标识不聚合装载科学实现。setuptools 禁用 namespace 发现，不能凭无直接函数调用就删除包标识。 |
| [__main__.py](../src/rpipe/__main__.py) | 模块命令入口 | python -m rpipe 入口；字符串调用另查 CLI/制品测试 | 保留；与 console script 同走 flow.cli.main，不新增第二套命令行为。 |
| [api.py](../src/rpipe/api.py) | 公共用例入口 | 源码：[src/rpipe/flow/cli.py](../src/rpipe/flow/cli.py) 等 2 文件；研究：[studies/main_exp/flow/execute/capacity.py](../studies/main_exp/flow/execute/capacity.py) 等 6 文件；测试：[tests/rpipe/flow/test_cli.py](../tests/rpipe/flow/test_cli.py) 等 22 文件 | 保留计划、执行、查询等必要用例，正式入口按目标重定。process 的研究装配归 flow；清除偶然转导出和旧调用适配，不因 Study 现有导入保留旧 API。 |
| [flow/__init__.py](../src/rpipe/flow/__init__.py) | 包发现与命名空间 | [pyproject 包发现](../pyproject.toml) | 保留；包根只暴露版本，其余包标识不聚合装载科学实现。setuptools 禁用 namespace 发现，不能凭无直接函数调用就删除包标识。 |
| [flow/cli.py](../src/rpipe/flow/cli.py) | 命令解析、路由与呈现 | 源码：[src/rpipe/__main__.py](../src/rpipe/__main__.py)；测试：[tests/rpipe/flow/test_cli.py](../tests/rpipe/flow/test_cli.py) 等 6 文件 | 保留命令入口职责，命令集和参数按新正式用例定义；旧选项、别名和 legacy 路由退出，当前脚本用法不构成约束。 |
| [flow/exports.py](../src/rpipe/flow/exports.py) | 曲线和 TensorBoard 派生导出 | 源码：[src/rpipe/flow/cli.py](../src/rpipe/flow/cli.py)；测试：[tests/rpipe/flow/test_cli.py](../tests/rpipe/flow/test_cli.py) 等 3 文件 | 保留所需派生导出能力，改用统一目标读端；不维持旧 index/轨迹读取入口及格式回退来满足现有导出代码。 |
| [flow/huggingface.py](../src/rpipe/flow/huggingface.py) | HF 装配选择 | 源码：[src/rpipe/flow/run.py](../src/rpipe/flow/run.py)；测试：[tests/rpipe/flow/test_huggingface.py](../tests/rpipe/flow/test_huggingface.py) | 保留；虽短但隔离后端选择并复用 Native 生命周期，不单为少一个文件去掉现有后端边界。 |
| [flow/native.py](../src/rpipe/flow/native.py) | 四类组件装配与作用域收尾 | 源码：[src/rpipe/flow/huggingface.py](../src/rpipe/flow/huggingface.py) 等 2 文件；测试：[tests/rpipe/flow/test_native.py](../tests/rpipe/flow/test_native.py) 等 3 文件 | 保留组件装配、注册作用域及收尾职责；装配对象和接入签名按目标重设，不为旧注入测试或旧 Study 保持接口。 |
| [flow/run.py](../src/rpipe/flow/run.py) | 所属 worker 与父子协议 | 源码：[src/rpipe/api.py](../src/rpipe/api.py) 等 2 文件；测试：[tests/rpipe/structure/system/test_factory_recipe.py](../tests/rpipe/structure/system/test_factory_recipe.py) | 保留协调职责；api.run 和内部 CLI 使用，科学阶段交给 pipeline，执行权仍归 execution，不恢复第二套 Run 状态。 |
| [flow/stages.py](../src/rpipe/flow/stages.py) | 阶段顺序与扩展时点 | 源码：[src/rpipe/flow/run.py](../src/rpipe/flow/run.py) | 保留；flow.run 调用，阶段只决定顺序和检查时点，不重复执行权、终态和正式结果发布。 |
| [flow/study.py](../src/rpipe/flow/study.py) | 研究加载及受信类定位 | 源码：[src/rpipe/api.py](../src/rpipe/api.py) 等 3 文件；测试：[tests/rpipe/flow/test_study.py](../tests/rpipe/flow/test_study.py) 等 3 文件 | 研究加载只服务新接入合同；旧 recipe 形状、上下文字段、迁位 pickle 类 reader 和旧模块映射退出，不把旧加载协议包装成新门面。 |
| [flow/supervision.py](../src/rpipe/flow/supervision.py) | 显式有界批次监管 | 源码：[src/rpipe/api.py](../src/rpipe/api.py) 等 2 文件 | 保留；api.supervise 和内部协调者实际使用，与普通 launch 分开，采样 RSS/输出不变成硬配额承诺。 |
| [structure/__init__.py](../src/rpipe/structure/__init__.py) | 包发现与命名空间 | [pyproject 包发现](../pyproject.toml) | 保留；包根只暴露版本，其余包标识不聚合装载科学实现。setuptools 禁用 namespace 发现，不能凭无直接函数调用就删除包标识。 |
| [structure/algorithm/__init__.py](../src/rpipe/structure/algorithm/__init__.py) | 包发现与命名空间 | [pyproject 包发现](../pyproject.toml) | 保留；包根只暴露版本，其余包标识不聚合装载科学实现。setuptools 禁用 namespace 发现，不能凭无直接函数调用就删除包标识。 |
| [structure/algorithm/batch.py](../src/rpipe/structure/algorithm/batch.py) | 批次形状与设备适配 | 源码：[src/rpipe/structure/algorithm/evaluation_support.py](../src/rpipe/structure/algorithm/evaluation_support.py) 等 3 文件；测试：[tests/rpipe/structure/algorithm/test_batch.py](../tests/rpipe/structure/algorithm/test_batch.py) | 保留；原生、HF 及评测共用，不能因文件短就复制到各训练循环。 |
| [structure/algorithm/evaluation.py](../src/rpipe/structure/algorithm/evaluation.py) | 独立评测算法 | 源码：[src/rpipe/structure/algorithm/factory.py](../src/rpipe/structure/algorithm/factory.py) 等 2 文件；研究：[studies/main_exp/flow/execute/precision.py](../studies/main_exp/flow/execute/precision.py) 等 2 文件；测试：[tests/rpipe/structure/algorithm/test_evaluation_support.py](../tests/rpipe/structure/algorithm/test_evaluation_support.py) 等 3 文件 | 保留独立评测能力，与训练周期评测分清；旧 Study 的直接类导入不能固定目标类名或构造方式。 |
| [structure/algorithm/evaluation_support.py](../src/rpipe/structure/algorithm/evaluation_support.py) | 周期评测及 best 判定 | 源码：[src/rpipe/structure/algorithm/evaluation.py](../src/rpipe/structure/algorithm/evaluation.py) 等 2 文件；测试：[tests/rpipe/structure/algorithm/test_evaluation_support.py](../tests/rpipe/structure/algorithm/test_evaluation_support.py) 等 2 文件 | 保留；训练与独立评测真实共用，明确与 evaluation 的职责差别，不凭名称近似合并。 |
| [structure/algorithm/factory.py](../src/rpipe/structure/algorithm/factory.py) | 算法注册及实例基础能力 | 源码：[src/rpipe/flow/native.py](../src/rpipe/flow/native.py) 等 3 文件；研究：[studies/main_probe/structure/algorithm/__init__.py](../studies/main_probe/structure/algorithm/__init__.py)；测试：[tests/_data/launch_retry_recipe.py](../tests/_data/launch_retry_recipe.py) 等 7 文件 | 算法实例、注册分派及运行辅助分清；旧 Algorithm/Hook/Registry/Factory 签名不保留适配，目标扩展由职责重新定义。 |
| [structure/algorithm/hf_map.py](../src/rpipe/structure/algorithm/hf_map.py) | 配置到 HF 参数的映射 | 源码：[src/rpipe/structure/algorithm/transformers_trainer.py](../src/rpipe/structure/algorithm/transformers_trainer.py)；测试：[tests/rpipe/structure/algorithm/test_hf_map.py](../tests/rpipe/structure/algorithm/test_hf_map.py) | 保留；后端参数差异在这里显式表达，不能用静默默认替代原生配置含义。 |
| [structure/algorithm/kwargs.py](../src/rpipe/structure/algorithm/kwargs.py) | 构造参数解析工具 | 源码：[src/rpipe/structure/algorithm/hf_map.py](../src/rpipe/structure/algorithm/hf_map.py) 等 2 文件；测试：[tests/rpipe/structure/algorithm/test_hf_map.py](../tests/rpipe/structure/algorithm/test_hf_map.py) | 保留局部职责；仅供 optim/hf_map 使用，不扩成全仓 utils，也不借整理扩大忽略科学参数的范围。 |
| [structure/algorithm/metrics.py](../src/rpipe/structure/algorithm/metrics.py) | 指标定义与归约 | 源码：[src/rpipe/structure/algorithm/tracker.py](../src/rpipe/structure/algorithm/tracker.py) 等 2 文件；研究：[studies/main_exp/flow/execute/precision.py](../studies/main_exp/flow/execute/precision.py)；测试：[tests/_data/recovery_worker.py](../tests/_data/recovery_worker.py) 等 6 文件 | 保留；tracker/train 和 precision 使用，指标单位、样本权重及完整归约属于科学合同。 |
| [structure/algorithm/optim.py](../src/rpipe/structure/algorithm/optim.py) | 优化器、调度器与裁剪构造 | 源码：[src/rpipe/structure/algorithm/factory.py](../src/rpipe/structure/algorithm/factory.py) 等 2 文件；测试：[tests/rpipe/structure/algorithm/test_hf_map.py](../tests/rpipe/structure/algorithm/test_hf_map.py) 等 2 文件 | 保留；构造和训练时序分开，更新及保存的时点仍由算法控制。 |
| [structure/algorithm/progress.py](../src/rpipe/structure/algorithm/progress.py) | 训练进度单位与保存节点 | 源码：[src/rpipe/structure/algorithm/evaluation.py](../src/rpipe/structure/algorithm/evaluation.py) 等 5 文件；测试：[tests/rpipe/structure/algorithm/test_progress.py](../tests/rpipe/structure/algorithm/test_progress.py) | 保留进度单位及周期规则，正式入口保持单一；due_eval_period 等同义旧别名退出，不用转发方式延续。 |
| [structure/algorithm/resume.py](../src/rpipe/structure/algorithm/resume.py) | 恢复选择、RNG 与权重应用 | 源码：[src/rpipe/structure/algorithm/factory.py](../src/rpipe/structure/algorithm/factory.py) 等 2 文件；测试：[tests/rpipe/structure/algorithm/test_optim.py](../tests/rpipe/structure/algorithm/test_optim.py) 等 2 文件 | 保留目标协议内明确恢复需求；旧 sibling/index 猜配、历史引用解析和旧格式适配退出，不为旧 checkpoint 保留额外恢复路径。 |
| [structure/algorithm/tracker.py](../src/rpipe/structure/algorithm/tracker.py) | 数值累计及轨迹恢复 | 源码：[src/rpipe/flow/native.py](../src/rpipe/flow/native.py) 等 6 文件；研究：[studies/main_exp/flow/execute/precision.py](../studies/main_exp/flow/execute/precision.py) 等 4 文件；测试：[tests/_data/recovery_worker.py](../tests/_data/recovery_worker.py) 等 11 文件 | 保留数值累计及目标轨迹/恢复需求，接口与状态格式按目标定义；现有两项研究的调用可以重写，旧格式回退不保留。 |
| [structure/algorithm/train.py](../src/rpipe/structure/algorithm/train.py) | 原生训练时序 | 源码：[src/rpipe/structure/algorithm/factory.py](../src/rpipe/structure/algorithm/factory.py) 等 2 文件；研究：[studies/main_exp/flow/execute/speed.py](../studies/main_exp/flow/execute/speed.py) 等 3 文件；测试：[tests/_data/recovery_worker.py](../tests/_data/recovery_worker.py) 等 5 文件 | 保留原生训练能力；共用观察/状态构造从后端私有接口中收拢，移除 HF 对旧私有函数及周期别名的连接，不冻结旧类接口。 |
| [structure/algorithm/transformers_trainer.py](../src/rpipe/structure/algorithm/transformers_trainer.py) | HF 后端执行与回调 | 源码：[src/rpipe/structure/algorithm/factory.py](../src/rpipe/structure/algorithm/factory.py)；测试：[tests/rpipe/structure/algorithm/test_transformers_trainer.py](../tests/rpipe/structure/algorithm/test_transformers_trainer.py) | 保留所需 HF 后端能力，按新共用职责连接；移除对原生文件旧私有辅助的依赖，不以转发层延续旧名。 |
| [structure/artifacts.py](../src/rpipe/structure/artifacts.py) | 产物、身份及受保护 I/O | 源码：[src/rpipe/api.py](../src/rpipe/api.py) 等 17 文件；研究：[studies/main_exp/flow/execute/capacity.py](../studies/main_exp/flow/execute/capacity.py) 等 14 文件；测试：[tests/rpipe/flow/test_exports.py](../tests/rpipe/flow/test_exports.py) 等 28 文件 | 按身份/输入来源、owner/锁、原子 I/O 明确组织；旧常量别名、旧配置/结果路径包装退出，目标产物只遵循一套正式合同。 |
| [structure/comparison.py](../src/rpipe/structure/comparison.py) | 显式深比较与比较报告 | 源码：[src/rpipe/api.py](../src/rpipe/api.py)；测试：[tests/rpipe/structure/test_comparison.py](../tests/rpipe/structure/test_comparison.py) | 保留目标输入的比较能力，旧 --legacy 目录接口及历史 pickle 注入退出；比较入口及数据形状按新正式合同定义。 |
| [structure/contracts.py](../src/rpipe/structure/contracts.py) | 纯配置、规格和执行合同 | 源码：[src/rpipe/api.py](../src/rpipe/api.py) 等 26 文件；研究：[studies/main_exp/flow/execute/precision.py](../studies/main_exp/flow/execute/precision.py) 等 5 文件；测试：[tests/_data/recovery_worker.py](../tests/_data/recovery_worker.py) 等 38 文件 | 保留纯声明、规格和执行观察职责，类型及字段按目标设计；旧 Control 转换包装、忽略参数和兼容导出退出，不为旧调用冻结类型名。 |
| [structure/data/__init__.py](../src/rpipe/structure/data/__init__.py) | 包发现与命名空间 | [pyproject 包发现](../pyproject.toml) | 保留；包根只暴露版本，其余包标识不聚合装载科学实现。setuptools 禁用 namespace 发现，不能凭无直接函数调用就删除包标识。 |
| [structure/data/factory.py](../src/rpipe/structure/data/factory.py) | 数据对象、注册与构造 | 源码：[src/rpipe/flow/native.py](../src/rpipe/flow/native.py) 等 3 文件；研究：[studies/main_exp/flow/execute/precision.py](../studies/main_exp/flow/execute/precision.py) 等 6 文件；测试：[tests/_data/recovery_worker.py](../tests/_data/recovery_worker.py) 等 12 文件 | 保留数据构造、迭代与所属资源职责；对象和注册签名按目标设计，Study builder 可以重写，不反向约束核心。 |
| [structure/data/prepare.py](../src/rpipe/structure/data/prepare.py) | 显式共享数据准备 | 测试：[tests/rpipe/structure/data/test_prepare.py](../tests/rpipe/structure/data/test_prepare.py) | 旧共享准备入口列入移除，不因专门测试保留。需要的数据准备按目标正式数据用例设计；不为原模块保留空壳。 |
| [structure/data/profile.py](../src/rpipe/structure/data/profile.py) | 数据统计与 profile 用例 | 源码：[src/rpipe/flow/cli.py](../src/rpipe/flow/cli.py) 等 2 文件；测试：[tests/rpipe/flow/test_cli.py](../tests/rpipe/flow/test_cli.py) 等 2 文件 | 保留逐批统计及严格 stats 读取；profile_study 的请求协调与统计计算可再分清，但不改统计精度或把 profile 变成 plan 副作用。 |
| [structure/data/resume.py](../src/rpipe/structure/data/resume.py) | 加载器游标与生成器恢复 | 源码：[src/rpipe/structure/data/factory.py](../src/rpipe/structure/data/factory.py) | 保留；由 Data 使用，恢复索引而不重放已消费变换，不能为合并文件改变采样顺序。 |
| [structure/execution.py](../src/rpipe/structure/execution.py) | 单 Run 执行权和终态 | 源码：[src/rpipe/flow/run.py](../src/rpipe/flow/run.py) 等 3 文件；测试：[tests/rpipe/flow/test_native.py](../tests/rpipe/flow/test_native.py) 等 5 文件 | 保留唯一执行权、生命周期与终态；所需扩展协议按目标定义，不承诺旧四方法签名和 payload 注入接口。 |
| [structure/model/__init__.py](../src/rpipe/structure/model/__init__.py) | 包发现与命名空间 | [pyproject 包发现](../pyproject.toml) | 保留；包根只暴露版本，其余包标识不聚合装载科学实现。setuptools 禁用 namespace 发现，不能凭无直接函数调用就删除包标识。 |
| [structure/model/architectures.py](../src/rpipe/structure/model/architectures.py) | 内置网络实现 | 源码：[src/rpipe/structure/model/factory.py](../src/rpipe/structure/model/factory.py)；测试：[tests/rpipe/structure/algorithm/test_batch.py](../tests/rpipe/structure/algorithm/test_batch.py) | 保留计算职责；允许按网络独立变化拆合，但没有据此改网络结构或参数初始化的依据。 |
| [structure/model/factory.py](../src/rpipe/structure/model/factory.py) | 模型构造与领域注册 | 源码：[src/rpipe/flow/native.py](../src/rpipe/flow/native.py)；研究：[studies/main_exp/flow/execute/precision.py](../studies/main_exp/flow/execute/precision.py) 等 5 文件；测试：[tests/_data/launch_retry_recipe.py](../tests/_data/launch_retry_recipe.py) 等 6 文件 | 保留模型构造与数据元信息边界；旧 builder/Registry/Factory 表面不获兼容承诺，按新正式接入重写研究调用。 |
| [structure/model/input_norm.py](../src/rpipe/structure/model/input_norm.py) | 输入归一化及训练增强 | 源码：[src/rpipe/structure/model/factory.py](../src/rpipe/structure/model/factory.py) | 保留；由模型工厂装配，搬到数据端会改变研究口径及随机数时序，不能视为纯目录整理。 |
| [structure/plan.py](../src/rpipe/structure/plan.py) | 纯声明展开和配对 | 源码：[src/rpipe/api.py](../src/rpipe/api.py) | 保留纯展开及明确依赖，声明只接受目标字段；移除旧空操作字段和 freeze 专门兼容处理，不保留旧配置接受路径。 |
| [structure/readout.py](../src/rpipe/structure/readout.py) | 状态读取及数值汇总 | 源码：[src/rpipe/api.py](../src/rpipe/api.py) 等 2 文件；测试：[tests/rpipe/flow/test_exports_curves.py](../tests/rpipe/flow/test_exports_curves.py) 等 4 文件 | 保留状态、轨迹及汇总需求，统一服务目标正式格式；旧结果读取、旧 index/轨迹兼容分支、路径别名和旧聚合入口全部退出，exports 围绕新读端改写。 |
| [structure/system/__init__.py](../src/rpipe/structure/system/__init__.py) | 包发现与命名空间 | [pyproject 包发现](../pyproject.toml) | 保留；包根只暴露版本，其余包标识不聚合装载科学实现。setuptools 禁用 namespace 发现，不能凭无直接函数调用就删除包标识。 |
| [structure/system/factory.py](../src/rpipe/structure/system/factory.py) | 设备对象与 checkpoint 存取 | 源码：[src/rpipe/flow/native.py](../src/rpipe/flow/native.py)；研究：[studies/main_exp/flow/execute/precision.py](../studies/main_exp/flow/execute/precision.py) 等 4 文件；测试：[tests/_data/recovery_worker.py](../tests/_data/recovery_worker.py) 等 11 文件 | 保留设备及目标状态存取；旧 checkpoint 目录镜像、历史类路径和格式回退退出，不为旧恢复数据维持多套协议。 |
| [structure/system/logger.py](../src/rpipe/structure/system/logger.py) | 诊断事件、脱敏及限额 | 源码：[src/rpipe/flow/run.py](../src/rpipe/flow/run.py) 等 2 文件；测试：[tests/rpipe/flow/test_logging.py](../tests/rpipe/flow/test_logging.py) 等 3 文件 | 保留；父/worker/System 真实使用，不把训练指标存储和科学判据并入日志。 |
| [structure/system/origin.py](../src/rpipe/structure/system/origin.py) | 来源策略及受约束获取 | 源码：[src/rpipe/flow/native.py](../src/rpipe/flow/native.py) 等 5 文件；研究：[studies/main_exp/flow/prepare_data.py](../studies/main_exp/flow/prepare_data.py)；测试：[tests/rpipe/structure/system/test_origin.py](../tests/rpipe/structure/system/test_origin.py) 等 4 文件 | 保留；Data/Model/profile 及 prepare_data 使用，endpoint 作用域与实际来源观察不可因封装省略。 |
| [structure/system/resources.py](../src/rpipe/structure/system/resources.py) | 所属系统资源及输出观察 | 源码：[src/rpipe/flow/supervision.py](../src/rpipe/flow/supervision.py)；测试：[tests/rpipe/structure/system/test_resources.py](../tests/rpipe/structure/system/test_resources.py) | 保留；supervision 使用 WindowsJob 和文件增长观察，资源原语不接管批次调度或默认实验预算。 |
| [structure/system/runtime.py](../src/rpipe/structure/system/runtime.py) | 随机数、精度与合作取消 | 源码：[src/rpipe/flow/native.py](../src/rpipe/flow/native.py) 等 5 文件；研究：[studies/main_exp/flow/execute/precision.py](../studies/main_exp/flow/execute/precision.py) 等 4 文件；测试：[tests/_data/recovery_worker.py](../tests/_data/recovery_worker.py) 等 2 文件 | 保留；Native、算法和研究测量共用，不迁到 flow 导致领域层反向依赖。 |
| [structure/system/torchvision_load.py](../src/rpipe/structure/system/torchvision_load.py) | 可选库装载兼容 | 源码：[src/rpipe/structure/data/factory.py](../src/rpipe/structure/data/factory.py)；测试：[tests/rpipe/structure/system/test_torchvision_load.py](../tests/rpipe/structure/system/test_torchvision_load.py) | 保留；DataFactory 使用，已有针对性测试，不把当前瞬态重试误扩成吞任意导入错误。 |
| [structure/system/transfers.py](../src/rpipe/structure/system/transfers.py) | 来源操作的传输适配 | 源码：[src/rpipe/structure/system/origin.py](../src/rpipe/structure/system/origin.py)；测试：[tests/rpipe/structure/system/test_origin_sdk.py](../tests/rpipe/structure/system/test_origin_sdk.py) | 保留私有边界；origin 调用，清理 owned stream 及作用域，不向算法和 Study 暴露全局网络代理。 |

## （二）接口清理工作

下表是待实施的清理工作，旧名称只在这里作为定位依据，不出现在目标接口设计中。方向已明确，但当前仍只改文档，未获代码工作恢复授权。

| **当前旧入口或机制** | **目标处理** | **能力归属与理由** |
| --- | --- | --- |
| api.source_digest 等偶然转导出 | 移除旧导出，不因三处 Study 调用保留，也不做同名兼容转发 | 来源身份能力归明确的身份职责；新研究代码直接采用目标正式入口 |
| control_from_config 的忽略参数及旧配置转换包装 | 移除兼容参数和包装，不保留接收后忽略的签名 | 新配置及规格由一套明确合同构造和校验，不为旧调用维持空语义参数 |
| run(..., payload=...) 的旧注入接口 | 退出旧参数及旧实现适配，不以测试依赖保留旧调用形状 | 执行实现或研究扩展若仍有需求，只按目标架构定义一种正式接入合同，不建立 payloads 柱或旧参数适配层 |
| read_legacy、CLI --legacy、旧目录比较入口 | 从目标框架移除旧读取和兼容入口，不新增转换命令接替 | 目标查询和比较仅服务正式声明的输入及格式；历史成果留存不等于新框架负责读取或恢复 |
| sibling_train_ids/sibling_train_dependency 等旧 index 猜配 | 移除旧入口和推断路径 | 训练与评测依赖只能来自目标计划中的明确关系，不用目录、旧索引或附近文件猜配 |
| data.prepare.prepare_shared_data 旧独立准备入口 | 移除旧入口，不恢复到 make，也不因已有专门测试继续保留 | 数据准备若是目标能力，在正式数据用例中明确设计，不同时保留旧隐式流程或旧包装 |
| flow.prepare_shared=False、freeze 等旧兼容声明 | 不保留旧键的接受、忽略或专门兼容分支 | 目标声明只接受自身规定的字段，未知旧字段按统一校验拒绝，不因旧 Study 存在而留空操作开关 |
| readout.process_path/run_process_path、旧别名和旧 index/结果汇总入口 | 移除旧路径、别名与格式回退；现有 exports 改用目标正式读端 | 状态、轨迹与统计能力按清晰职责重新提供，不因当前内部调用继续暴露旧读取表面 |
| train.due_eval_period / progress.due_eval_period 等同义别名 | 移除别名和旧调用位置，不留转发函数 | 周期和进度规则归单一正式入口，测试改测实际规则，不维护旧名称本身 |
| HF 从 train 导入 _build_payload/_current_lr/_hook_extra 的私有连接 | 移除旧跨后端私有接口，按共用职责重新组织 | 共用观察和 checkpoint 状态构造不属于某一个后端，不能以清理为名再包一层旧名转发 |
| 迁位 pickle 类 reader、旧模块名映射和 checkpoint 目录镜像回退 | 移除历史类路径及旧格式兼容机制，目标不承诺旧 checkpoint 可直接恢复 | 新状态存取合同服务目标恢复需求，避免为旧私有模块和任意历史对象绑定新结构 |
| 旧 recipe/hook/Registry/Factory 签名、上下文字段及旧命令约定 | 不作为新扩展合同的保留条件；需要的构造和生命周期能力重新定义 | 具体研究及其测试可以重写，不用旧 Study 反向限定核心字段、类名或目录 |
| 已不在当前包树中的 payloads、structure.api/make/control、flow.context 等旧路径 | 不恢复，不补兼容壳，也不为下游副本重建旧层次 | 目标只按两柱及合理职责组织，下游继续使用其固定版本，迁移不在当前范围 |

## （三）实现差距与验收安排

| **目标职责** | **需要完成的工作** | **验收依据** |
| --- | --- | --- |
| 来源身份独立 | 从产物模块提取身份、源码/输入来源和准入核验到 identity，受保护 I/O 保持唯一实现 | 源码/输入漂移、路径保护、无写计划及并发保全 |
| 训练状态共用 | 两后端依目标 state/optim 合同连接，共用构造不依附单个后端的私有函数 | 原生/HF 各自更新顺序、指标及恢复范围，不抹平后端差异 |
| 正式入口单一 | 按目标用例重定公共输入输出，实施上表接口清理，更新核心测试、fixture、命令及安装旅程 | 无兼容壳或双格式回退，需求级反例在新合同上可定位 |
| 研究独立接入 | 核心稳定后按正式合同重新编写需要的本仓研究接入，不让现有代码制约核心 | 核心、研究接入及科学验收分开报告，不把未接入记为通过 |

当前源码、直接调用和执行限制的取证范围见日期记录。没有执行代码迁位或清理；新的目标模块尚不能因设计列出就记为已有实现。

# 六、模板定位下的架构复核与结构建议

最新执行顺序：先完善架构，再推导新的代码结构设计。架构已按早期两柱版本恢复，并将部件建构、阶段上下文、研究扩展、指标进度、checkpoint/恢复、来源及比较合同全部合并到单一架构设计 Markdown；不保留架构分册或专用文件夹。本章此前的新结构建议继续暂缓采纳；代码结构设计尚未重写，源码不变。下一步依据完善后的架构逐项推导模块与依赖，不把旧代码清单或此前提案直接当作新目标。

## （一）复核结论与优先级

本章是待校准方案，不是新的最终架构。用户确认的定位优先于此前“公共运行库”的假设：仓库交付一个能读懂、能运行、能直接修改的研究起点，衍生项目自行演化，不承担稳定公共 API、集中接入多个研究项目或持续兼容旧消费者的产品责任。现有 Python 包布局和安装方式本身并不与模板冲突，不因定位调整直接删除打包配置。

| **问题** | **当前证据与影响** | **建议** |
| --- | --- | --- |
| 实验部件没有成为架构主线 | 当前架构前三章先列身份、执行权、来源和观察机制，四类科学组件合为一行；读者难以回答如何构造实验 | 先讲 structure 各部件、flow 各阶段、输入输出与协作，再补必要质量约束 |
| 架构语义与代码命名脱节 | 架构使用抽象职责表，代码结构仍按 data/model/algorithm/system 与阶段组织 | 共享部件名称；架构说明语义和关系，代码结构说明具体落点及依赖，不要求两者逐文件相等 |
| 静态组成与执行过程混杂 | structure/execution.py 拥有执行生命周期，flow/run.py 与 stages.py 又组织实际过程 | 生命周期和阶段推进以 flow 为归属；structure 保留配置、科学对象、产物存取及必要原语，避免两套运行主线 |
| 流程说明大于实际落点 | stages.pipeline 实际推进 prepare 到 write hook，process 由其它入口负责，正式结果发布在 execution；“六阶段”不等于一个函数已经完成六步 | 架构明确单 Run 的阶段、结果发布时机与整轮汇总的 scope，代码结构标出各自唯一落点 |
| 模板被当作公共平台 | 先前目标要求领域包经统一接入、正式公共用例及制品旅程，增加理解和修改成本 | 内置一个完整可运行示例，允许直接改部件和流程；注册、后端适配和并发机制按实际示例需求保留，不升级为平台前提 |

以上是对文档和选定源码的分析，不表示现有错误处理或来源保全应删除，也不等于完成了全仓行为审计。

## （二）当前实际文件结构

以下为当前源码的职责级视图，省略包标识和四类部件内的叶文件；第五章保留完整模块影响表。identity.py、algorithm/state.py 是此前文档提出的文件，实际源码中不存在。

```text
RPipe/
  src/rpipe/
    api.py, __main__.py
    structure/
      contracts.py, plan.py, execution.py
      artifacts.py, readout.py, comparison.py
      data/, model/, algorithm/, system/
    flow/
      cli.py, run.py, supervision.py
      native.py, huggingface.py, stages.py
      study.py, exports.py
  studies/
    _template/, main_exp/, main_probe/
  tests/
  docs/
```

实际主线是声明经 plan 展开，再经入口与 run 装配到 execution；科学计算通过 stages 与 native/HF 调用四类组件。源码已有清晰的部件名，但执行职责在两柱间分布，文档没有充分解释这一点。

## （三）建议的文件结构

保留 src/rpipe 作为模板源码根，先调整其内部表达，不立即另建框架包与项目包。下面是讨论中的主要落点；文件名和细分粒度尚未定案，不建立空壳或逐阶段转发文件。

```text
RPipe/
  src/rpipe/
    __init__.py, __main__.py
    api.py                   模板内便利入口，按实际使用保留
    structure/
      control.py             配置、实验变量与纯数据合同
      make.py                声明展开及运行清单构造
      data/                  数据来源、划分、预处理与批次
      model/                 模型结构、权重与前向接口
      algorithm/             训练、评测或推理计算及指标
      system/                设备、精度、随机性和运行环境
      artifact/
        io.py                布局、路径保护与存取原语
        identity.py          配置、源码及输入来源
        readout.py           状态、日志和结果读取
        comparison.py        明确条件下的比较
    flow/
      cli.py                 本仓命令入口
      run.py                 一次运行的生命周期和收尾
      stages.py              六阶段的顺序及实际职责
      native.py              原生实现的组件装配
      huggingface.py         HF 装配，仅按实际需求保留
      supervision.py         批次与所属进程管理
      study.py               研究声明加载、整轮流程及汇总
      exports.py             派生成果导出，按职责复核
  studies/
    _template/               完整且尽量小的可修改示例
    main_exp/, main_probe/   本仓验证实例，稍后决定保留范围
  tests/                     围绕模板行为与示例验证
  docs/                      沿用现有成果分工
```

control/make 名称借回早期架构清楚的实验语义，不恢复旧函数、旧参数或兼容门面。改名是否改善理解，以及 artifact 是否确实值得分为四个文件，需结合函数归属再定；不单凭树形美观移动代码。此方案不再预设新增 algorithm/state.py，后端共享状态是否独立成文件须先证明实际需要。

| **当前落点** | **建议落点与理由** |
| --- | --- |
| contracts.py / plan.py | control.py / make.py，分别表达一次实验条件与批量展开；也可保留原名，但架构必须明确这两个语义 |
| structure/execution.py + flow/run.py | 以 flow/run.py 为生命周期主落点，纯状态数据和 I/O 原语回各自职责；避免直接拼接成更大的文件 |
| artifacts.py / readout.py / comparison.py | structure/artifact 下按产物职责组织；身份校验与 I/O 是否拆分依据实际内聚，不为统一命名增加层 |
| stages.py / native.py / huggingface.py | stages 表达过程，后两者承接确有差异的组件装配；不另建一套模板扩展协议 |
| data/model/algorithm/system | 保留四类部件语义，内部叶文件逐个按职责审定；不承诺现有 Factory/Registry 或私有函数签名 |

## （四）两柱的部件与接口语义

| **部件** | **负责什么** | **交给下一方什么** |
| --- | --- | --- |
| control | 本次比较因素、seed、四类部件的有效配置 | 明确的实验条件，不含活的模型或进程句柄 |
| make | 固定条件与因素轴、seed 展开 | 一组明确的 Run 条件及执行清单，不偷偷启动实验 |
| data | 来源、版本、划分、采样、预处理和数据批次 | 数据迭代接口与数据语义；不决定训练更新 |
| model | 网络结构、参数初始化和权重加载、前向接口 | 可计算的模型与输入输出约定；不掌管整轮实验 |
| algorithm | 如何训练、评测或推理，loss、更新顺序和指标归约 | 计算观测、指标和必要计算状态；不重新定义另一套 Flow |
| system | 设备、精度、随机性、环境与底层资源操作 | 本次计算所需环境和句柄；checkpoint 中优化器/RNG 等科学状态仍由对应组件解释 |
| artifact | 配置、结构化结果、文件型产物的组织与受保护存取 | 可追溯记录和路径；不决定实验结论或推动阶段 |
| flow | 装配上述部件、推进阶段、处理中断并收尾 | 执行事实及正式结果；谁创建资源谁负责关闭，底层关闭工具可以在 system |

接口先定义交付内容、状态所有者和失败行为，再决定函数或类。模板内不必为每个部件增加抽象基类、插件发现和稳定公共 API；直接函数调用可以满足职责边界。

## （五）Flow 应说明的过程

```text
研究问题与实验声明
  → make：展开因素和 seed，确定每次运行条件
  → 每个 Run：prepare → execute → collect → summarize → write
  → process：按明确的 Run / Experiment / Study 范围派生分析
  → 人阅读结果，修改下一轮实验
```

| **阶段** | **语义与边界** |
| --- | --- |
| prepare | 读取有效配置，准备 system/data/model/algorithm；检查输入，不在运行中偷偷改实验条件 |
| execute | 算法使用数据和模型完成计算；训练更新、评测或生成的具体语义由研究确定 |
| collect | 收集计算观测、指标和证据位置，不把尚未产生的观测补成成功 |
| summarize | 整理可序列化的单次摘要、条件和执行事实，不混入跨 seed 结论 |
| write | 在检查与必要资源收尾满足要求后发布正式结果；具体时序需和生命周期统一设计 |
| process | 从明确结果派生统计、比较和图表；声明处理范围，不覆盖原始科学结果 |

架构先完整解释这六项职责，代码可以在一个 stages.py 中实现多个阶段，有实际复杂度才拆文件或目录。重试、取消、失败和资源收尾是贯穿流程的行为，不另立一个与实验流程脱节的架构主干。当前阶段不修改实际执行顺序。

## （六）需在设计定稿前校准的取舍

建议暂时沿用当前 Study → Experiment → Run 语义（比较条件、seed 复测），不随文件整理改回早期“Experiment 就是一份可运行实现”的定义。原生/HF、进程监管和研究实例也先登记现有事实，是否进入最小模板要看示例教学与实际验证价值；不以模板定位一概删除。

下一步先确认本章的部件与流程方向，再重写架构的结构图、部件说明、协作过程和必要约束；代码结构文档跟随选定职责落位。旧接口清理仍只在计划，历史设计取证仍只在日期记录。这轮不实施文件迁移、研究重写、下游同步或代码变更。
