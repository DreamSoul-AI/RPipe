# 产物证据、只读读取与原子写入收敛设计

## 一、摘要与硬约束

- **状态：已实施。** 三类已按第三类→第二类→第一类分别经 PR #52 / #54 / #55 合入 dev，后续同类修复继续按已批准合同推进，不再等待设计批准。 本修订依据维护者补充指令，取代 `c9e0141` 中的尝试账本、结果视图和格式迁移方案；旧稿只保留在 Git 历史中，不作为实施计划。
- **文件架构和产物布局不改。** 不增加文件、目录、模块或格式版本，不移动模块；`result.json` 的位置、格式、含义与现有读写合同保持不变。函数内的内存类型、判断和私有复用可以收敛，不引入持久化新结构。
- **实施范围：** 在现有函数和文件内归纳保全规则、读取边界及原子发布职责。先保留既有修复，再用统一规则替代重复判断，不恢复逐个相邻路径审计。
- **实施约束：** 每类先补合同回归；第一类仅提炼现有私有判断，不扩大旧成功保留范围。第三类架构测试仅覆盖可整体替换文本，特殊协议按第五（三）精确豁免。不测速、改性能、调查超时、运行 GPU、迁移数据或重签 provenance。

初始设计的历史基线为 dev `d294c1b5aa99b4d7148f991f9a4ac8903fe23374`。#50 已由 [PR #51](https://github.com/DreamSoul-AI/RPipe/pull/51) 合入，最终工作提交和实际 dev 的必需 CI 已通过；实际 dev 依据为 [Unit Tests](https://github.com/DreamSoul-AI/RPipe/actions/runs/38633933430) / [Package Check](https://github.com/DreamSoul-AI/RPipe/actions/runs/38633933432)。不重复开 #50 修复 PR。上述历史 CI 是初始基线证据；三类实施与后续修复的已合 PR、提交和验收见第十节及各补充节。

| **类别** | **初始分散处理** | **已实施的收敛方式** | **不采用的结构变更** |
| --- | --- | --- | --- |
| 失败写入与证据保全 | Runner 特定拒绝保留成功；调度失效、阶段失败和重试各有职责 | 在现有失败写入 / 调度入口统一“允许写什么、何时拒绝写”的规则，错误继续追加到原 run.log | 无 attempts 目录、不可变账本、结果指针或双版本 schema |
| status / logs 安全读取 | stat、打开、解码及类型 guard 分散 | 在现有读取函数内统一三态与字段校验；Run 行先由计划 config 建立内存中的有类型值，再补结果 | 无新读取模块、磁盘 RunView、字段迁移或新结果语义 |
| 原子写与并发 | 文本已有 helper，其他调用仍有重复 staging / 直接文本发布 | 复用 `_atomic.py` 的 `atomic_write_text` → `atomic_replace`，统一替换类文本发布及临时文件生命周期 | 不新造万能 I/O 层，不搬 logger / tracker / checkpoint，不增加锁目录 |

## 二、问题归类与已完成状态

| **问题** | **dev 已修事实** | **本设计要归纳的共同规则** |
| --- | --- | --- |
| [#36](https://github.com/DreamSoul-AI/RPipe/issues/36) | 初始冻结拒绝不覆盖旧成功 | 计算前拒绝与运行中失败分开；拒绝不写旧结果 |
| [#41](https://github.com/DreamSoul-AI/RPipe/issues/41) | `--no-retry` 投影现有 API，默认行为保留且不跨调用残留 | 首错先记录原日志；重试选项只属于本次 launch。此 Issue 本身不是所有覆盖问题的同一根因 |
| B-067 | 冻结采集 I/O / 结构错误统一为 `ProvenanceChangedError`，保留 cause | 冻结拒绝不依赖底层异常恰好是哪一类；不能因错误分类遗漏走错失败写入 |
| [#37](https://github.com/DreamSoul-AI/RPipe/issues/37) | 固定 / 基底 eval、inference 的 pending mode 可读生成 config | 计划值优先，缺失与损坏不混淆，不猜 train |
| [#48](https://github.com/DreamSoul-AI/RPipe/issues/48) / B-069 | 三处 stat 已纳入原有 I/O try | stat / read / decode 是同一文件的一个错误边界 |
| [#50](https://github.com/DreamSoul-AI/RPipe/issues/50) / B-070 | 不再提前 `dict(metrics)`，复用单格 guard | 非对象不强转；坏格不遮蔽 peer 或其他合法字段 |
| B-068 | activity 的 stat / 编码失败被隔离 | 可选提示不可用不遮蔽正式结果，不能代替 index |
| [#38](https://github.com/DreamSoul-AI/RPipe/issues/38) | 初次部分文本写入进入 finally 清理 | staging 创建、写入、发布、失败清理是一套完整规则 |
| B-062 | 共享引用使用短 UUID 临时名，补齐失败交错回归 | 每个发布者仅处理自己的 staging，保留旧 target 和 peer |
| B-057 / B-064 | 文本碰撞及合法长 basename 的 staging 问题已修 | 不再各造基于时钟或拼完整目标名的临时文件方案 |

这些已知反例均已在 dev 修复，不能重新登记为开放根因。B-062 / B-067 / B-068 经 [PR #46](https://github.com/DreamSoul-AI/RPipe/pull/46)，后续边界经 [PR #47](https://github.com/DreamSoul-AI/RPipe/pull/47)，B-069 经 [PR #49](https://github.com/DreamSoul-AI/RPipe/pull/49)，B-070 经 PR #51 集成。证据见 [record §40–65](record.md) 和 [维护归档](maintenance_archive_2026-10-15.md)。这六个 Issue 已随 [v0.4.0](https://github.com/DreamSoul-AI/RPipe/releases/tag/v0.4.0) 发布关闭；当前 main 为 v0.4.1 / `00e70b1`。后续已修 Issue 按各自版本关闭，不因本文重复处理。B-065 性能去重已撤回，不恢复。

## 三、第一类：现有写入入口统一证据保全规则

**已实施：** [PR #55](https://github.com/DreamSoul-AI/RPipe/pull/55)；后文为持续有效的合同，非待审任务。

### （一）结果与诊断的现有含义不变

`result.json` 继续是当前合同下的一份 Run 结果，字段仍由 `validate_result / encode_result / load_result` 管理；不改成指针、账本或“最近有效结果视图”，不添加 attempt ID / history / format_version 等字段。已完成 Run、失败和诊断仍留在原路径。

错误追加到现有 `assets/logs/run.log` 并输出终端；首个业务错误必须先记录，重试产生的第二个错误另追加，不覆盖原日志。不能把文本日志说成完整结构化尝试历史，也不能仅凭旧 succeeded 文件推断本次命令成功。

### （二）统一决策表

已在既有 `flow/runner.py` 提炼私有 `_preserve_success`，原表达式等价返回；`FlowRunner._write_failed_result`、`schedule._fail_result` 及调用方保持下表各自职责。不新建文件、移动业务模块或把语义塞进 `_atomic.py`；既有 `write_result` 仍明确校验并写入，不静默忽略调用方要求的写入。

| **事件** | **result 处理** | **诊断与调用结果** |
| --- | --- | --- |
| 初始来源冻结拒绝，有可读旧成功 | 保留 result 原字节；计算、旧资产与 provenance 不动 | 原日志追加阶段 / 原异常，命令仍失败 |
| 初始拒绝，无旧成功，进入现有 Runner 失败定稿路径 | 按现行合同写 failed；CLI 总门原本不写结果的仍不新增 | 保留原异常及 cause，不虚构执行过阶段 |
| 普通执行阶段失败 | 维持原有 failed 定稿语义，不因“保全”改为保留 succeeded | 首错先记日志，退出仍失败；不宣称保留了历次结果快照 |
| process 失败，原计算成功已写入 | 维持现行 process 失败保留成功的规则 | process 的异常与命令失败仍可见，不称完整 Flow 通过 |
| 合法 rerun / train→eval 失效 | 继续现有失效写回与屏障；必须先过相关拒绝门 | 不把失效写成科学计算失败，不因旧成功跳过本次失败 |
| 旧 result 不可读或新结果无法编码 / 写入 | 有效诊断与原错误不得被二次异常遮蔽；本次文件写失败不得破坏 target | 在现有日志 / stderr 说明记录失败，不递归写错误、不拿 unreadable 当成功 |

本表统一现行边界，不扩大成“任何失败都保留旧成功”。`write_result` 里的语义校验、Runner 的阶段信息、freeze 的原始 cause、调度的返回码和依赖检查都保留。保全判断应使用同一次读取所得值，不能多个调用方重复读取后得出冲突结论。

### （三）替换旧局部判断

1. Runner 保留日志与异常传播，现有 `preserve_success` 对应的规则收成上述固定表；B-067 的错误归类继续作为来源拒绝合同，不取消冻结检查。
2. 调度入口在任何旧结果失效写入前完成现有来源 / 计划检查；实际允许的 rerun 继续按原合同失效，不能留下错误 succeeded 被下游使用。若发现新反例，先写回归，不用通用“保留成功”掩盖它。
3. `--no-retry` 与默认重试均保持当前作用域和次数；不把 retry_failed 写入 jobs / config，也不从上次 CLI 调用继承。
4. make 的配置 / 来源拒绝先验证再写，调用既有原子函数。单文件失败保全与多文件事务是不同保证；不新增 plan generation 或承诺 make 整体回滚。

## 四、第二类：现有读取函数内统一安全读取

**已实施：** [PR #54](https://github.com/DreamSoul-AI/RPipe/pull/54)；后文为持续有效的合同，非待审任务。

### （一）三个内存状态

三态只在现有函数内部表示，可用简单的判别值与已解码对象，不持久化新文件，也不改变现有 API 返回字段。

| **读取状态** | **判定** | **展示处理** |
| --- | --- | --- |
| 可用 | 打开、解码和最低结构检查成功 | 使用合法字段；各子字段仍独立校验 |
| 缺失 | 打开时目标确实不存在 | 沿用计划信息与既有 pending / 缺失提示，不伪造 failed |
| 读不了 | 权限 / I/O、不是预期文件、坏编码、解析或根结构错误 | 该文件提供不了的格为 `-`，保留已有诊断位置与正常 peer |

`is_file` 如需使用，必须与打开 / 解码放在同一错误边界；存在性检查不是后续读取安全的保证。只捕获明确的文件 / 解码 / 结构异常，非预期 RuntimeError 等继续暴露。日志保留现行文本容错规则，JSON / config 不用替换字符假造可用值。

### （二）有类型值先来自计划，再补结果

`list_runs / _mode / _metric_cell` 内先由 index 定位现有生成 config，核对身份、seed、mode，形成仅在内存中使用的明确字段；不构建 Data / Model / System。结果再补状态、metrics、错误与资产引用，不能悄悄改写计划 mode。

- config 可用时，按其合法默认规则读 mode，并核对 index / result。冲突在已有 error / note 中诊断，受影响格为 `-`，不能默选一个值消除冲突；严格执行与聚合路径继续拒绝非法身份。
- 历史 config 缺失时，保留已知合法 axes / result.control 的兼容读取；来源都未知时沿用 unknown，不从损坏配置猜 train。此优先级是内存读出规则收敛，须以现有合法结果与过滤回归证明兼容，不修改磁盘格式。
- metrics 不做 `dict(...)` 强转；对象子字段通过原有数值 guard，非法格为 `-`。不得为统一读取而放宽科学 schema 或把 bool / 列表对变成数字。
- 一个 Run 的文件出错只影响该行相关格；config 仍能给出的 mode / seed 与 peer 继续显示。缺失或损坏的 Study index 不能当空矩阵，保留 Study 级明确失败。

### （三）函数内复用与旧补丁替换

`status._read_optional / _note_from_log` 与 `logs.event_lines` 已采用同一 stat→read→decode 边界；共享读取已在既有 `readout/status.py` 的私有 `_read_optional` 中实现，`logs.py` 沿用现有依赖方向复用。`activity.read_activity` 在原函数内遵守同一规则，不能为了共用 helper 反向依赖 readout 或制造循环导入。

#48 的重复 try、#50 的字段 guard、B-068 的可选提示隔离，收敛的是规则与真正重复的代码；必要的字段验证不删除。`load_config / load_result` 的严格读取不改成全局吞错；process / compare / 调度不能把展示回退的 `-` 当真实输入。status / logs 不写盘、不自动修复、不清理文件，现有表头 / 输出字段和有效日志排序保留。

## 五、第三类：复用现有原子写函数

**已实施：** [PR #52](https://github.com/DreamSoul-AI/RPipe/pull/52)；后文为持续有效的合同，非待审任务。

### （一）现有职责不搬动

继续使用 `_atomic.py` 的两层调用链：`atomic_write_text` 管文本 staging / 清理，`atomic_replace` 管最终替换和既有 Windows 有界重试。不另加万能 `atomic_write(producer)` 接口，不把 logger、tracker、锁、checkpoint 的业务实现搬入此模块。

替换类文本产物统一调用 `atomic_write_text`：config、result、provenance、activity、process、readout 导出已有调用保留；index、jobs、生成脚本、profile / marker 中可复用的直接文本发布，逐项改为现有入口。编码、换行、返回路径及脚本属性必须保持；若某处现有签名不能保持字节语义，只在原函数 / 同一 `_atomic.py` 内评估最小适配，未有回归不得直接替换。

### （二）原子与并发保全规则

1. 临时名使用已修的短随机方案，不拼完整目标名，不用时钟当写者身份。临时创建需避免占用其他写者文件；故障测试验证真实所有权，而不只检查命名风格。
2. 写入与替换均在 finally 的范围内；失败保留旧 target，只清理本写者的普通临时文件，peer staging / 源文件不动。KeyboardInterrupt、编码失败、替换拒绝都要覆盖。
3. shared 的引用 / 迁移保留本文件内逻辑和资源锁，最终发布复用 `atomic_replace`；不新建引用子目录、改变 hardlink / symlink / fallback 语义或扩大无锁多写者支持。B-062 的原并发反例必须继续通过。
4. logger / tracker 的原路径追加和恢复 offset 不改成整文件替换；checkpoint 的 bundle / 分件与 `.writing / .incomplete` 保全合同不改、不清理诊断。锁文件不能以替换 inode 的方式“原子更新”。
5. 单文件替换不是多文件事务、全机互斥或断电持久性保证。Run 执行互斥按下述 R1 合同提供，不靠旧 activity 猜独占。

### （三）架构回归的可执行边界

测试禁止业务路径绕开现有原子入口发布**可整体替换的文本产物**，覆盖导入别名、Path.write_text / 写模式 open 等明确模式，同时给真实写入注入失败检查 target 字节。不能只用 grep 字符串或误禁 `str.replace`。

日志追加、tracker、checkpoint 二进制、现有 shared 文件锁与受控第三方保存仍在原模块；逐个函数列明其不可直接替换的理由与已有发布保护，不能把整个目录豁免。测试须包含绕过 atomic 的负例，以及合法追加 / checkpoint 不被误判的控制例；隔离测试 fixture 必须仍能制造坏字节、部分写和故障。

原指令“其他地方绝不直接写任何文件”的字面绝对保证，涉及 append / torch.save / 文件锁时不能仅复用现有文本函数做到。这里明确收窄为替换类文本发布的硬门，其余保持现有协议并做正确性回归；不通过搬模块、新增 I/O 框架或删除功能冒充全仓统一。该边界已获本轮授权，不扩展为万能 I/O 框架。

### （四）R1：Run 执行所有权

`FlowRunner.run` 在 logger、阶段和失败定稿前取得 Run 根目录的非重入 OS 锁，持有至整个调用结束。竞争者立即拒绝，不修改 result、日志、执行证据或阶段资产；锁文件仅是 OS 锁载体，存在不表示忙。共享缓存锁默认仍可同线程重入。不同 Run 不互斥。

每个调度子进程由独立环境副本传入随机 attempt token。Runner 入口在进程内小锁保护下一次消费私有环境 handoff / context state（包括随后被拒绝的入口）；后代环境不再继承它，同进程后续调用生成新 token。该 token 仅关联本次 child，不是外部权限凭据。Runner 获锁后先原子保存 `execution.json`（token、PID），失败则在业务异常处理之外退出；直接调用生成新 token。该文件只证明最近一次获得写入权的执行身份，不是锁、完成标记或完整历史。调度启动的旧 success 在 Runner 成功落 token 后失效；启动前失败或锁拒绝保留旧结果及非零退出。直接 Runner 原有冻结拒绝、process 失败的成功保全合同继续有效。

调度器对依赖 eval 等 Run 的失效写入同样先取得目标 Run 锁；需要失效的 eval 正在执行时，本次 train 不启动。等待子进程后，非零纠正须重取 Run 锁且持久 token 等于本 child 的 token。缺失、损坏、新 token 或锁忙时保留非零退出与诊断，不覆盖结果，不自动重试身份不明或被拒绝的执行。零退出也必须重取当前空闲 Run 锁并核对 token；不匹配时记录拒绝，不能由旧 success 推导本轮执行成功。`launch_jobs` 仍返回实际 `list[int]`，可选 `refused_runs` 接收本轮所有权拒绝 / 身份不明的 Run ID；CLI 与生成脚本据此保留非零退出，不把旧 success 当本轮执行成功。普通失败后重试恢复仍可成功退出。不能由父进程持锁让子进程绕过，否则父进程退出会提前释放仍在写入的子进程所有权。

本合同覆盖 Runner 与 scheduler 的单 Run 写入生命周期。低层写文件 API 不自动取得 Run 锁；不同 Run 间的 checkpoint 稳定性、train→eval 精确代际绑定、多文件事务和完整 attempt 历史仍不提供。依赖检查与目标文件写入的锁不能证明 eval 使用的另一 Run checkpoint 被冻结。

## 六、兼容与无需迁移的交付顺序

1. **无需产物迁移。** 既有目录、schema、Run ID、version、结果含义和 provenance 原字节不变；R1 仅在 Run 根新增锁载体和最近执行身份文件，不迁移历史结果；不存在升级时创建 legacy 快照、重签、重排日志或复制正式数据的步骤。
2. **不改变公共返回合同。** 内存三态与类型检查止于现有读取函数边界；公共 API、CLI 表格和严格执行失败规则由原用例锁定。旧合法输入按同一路径继续读取，未知 / 非法输入只按已批准规则降级。
3. **实施顺序：第三类→第二类→第一类。** 在现有测试文件先补三组合同回归，再在对应原文件内收敛规则；每步只移除已被公共规则覆盖的重复判断。无匹配保证时保留保护，不先删后补。
4. **开发流不变。** 实施仍按工作分支→PR→dev，最终 head 和实际 dev 的全部实际 CI 分别验收；每类独立 PR，CI 通过后合 dev；后续正确性批次不合 main；已结束工作分支按 [cicd](cicd.md) 与当前明确授权清理，不删除数据。

三类已实施合同已同步至 [layout](../code/layout.md)、[structure](../code/structure.md)、[flow](../code/flow.md)；本文件记录已批准并落地的规则，不是待审或暂停实施的入口。文件架构和产物布局继续不变，第七节限制保持。

## 七、确实需要结构变化才能提供的能力（仅说明，不实施）

| **能力 / 旧稿方案** | **为什么现有合同不够** | **本轮处理** |
| --- | --- | --- |
| 保存每次尝试的完整机器可读结果和独立资产 | 一个保持现有含义的 result 文件与共享可变 assets 无法同时保存全部历史；仅追加日志不能替代快照 | 不建 attempts，不加字段，不承诺该能力；继续原错误日志保全和现行失败定稿 |
| result 仅作有效结果指针、按明确训练代引用 eval | 会改变结果含义、schema、依赖身份与读取方；不是函数内部去重 | 撤回实施方案；现有 train→eval 屏障和新 version 规则保留 |
| make 多文件全有或全无的持久化提交 | 逐文件原子替换不能保证 config / index / provenance / jobs 整体事务；需新提交标识、布局或格式协议 | 不建 generation / manifest；保留预验证和单文件保证，不宣称跨文件原子 |
| 任意两个进程同 Run 写入的全局互斥 | 低层 API 直接写入不等于 Runner 执行 | R1 为 Runner 与 scheduler 增加相同 Run OS 锁，范围及限制见五（四）；低层任意写者不自动纳入 |
| 所有直接文件写入均归一个万能入口 | 日志追加、恢复偏移、锁 inode 和 checkpoint 诊断具有不同合同，强行迁移会改变职责或协议 | 不搬模块，不做框架；复用现有 atomic 链，按五（三）的明确边界测试 |

这些是限制说明，不是认定结构必须立即改变，也不是待自动执行的任务。确需上述保证时，另给具体反例和无法在现有合同内解决的理由，由维护者决定；当前不动手。

## 八、正确性回归计划

测试围绕固定合同归组；后续真实反例先登记 Issue 并补回归，不把编号当作独立设计或设计批准等待项。位置沿用现有测试文件；隔离临时目录、独立 basetemp，无 GPU / 外网 / 正式 Study / 性能测量。

| **测试组** | **关键断言** |
| --- | --- |
| result、Runner 与冻结拒绝 | 旧成功 / 无旧成功 × 外层门 / prepare × 来源 I/O / 结构拒绝；拒绝不计算，旧字节与 peer 保全，已有 failed 定稿语义不变 |
| make / launch / run-one | 拒绝发生在失效写回前；正常 rerun 和 sibling eval 失效不被新保全规则绕过；spawn / 非零退出保留原诊断与退出语义 |
| CLI 重试作用域 | 默认重试与 no-retry 次数保持；首错、次错按序追加原日志；第二次独立 CLI 调用不继承上次选项，不改 jobs 字节 |
| status / logs / activity | 三态 × stat / read / decode；缺文件与坏文件分开；异常 RuntimeError 不吞，单格回退不遮蔽 peer，目录成员 / 文件字节不变 |
| 内存类型与 mode | config 优先、合法历史 fallback、冲突 / unknown、train / eval / inference 过滤；非法 metrics 不强转，已有合法行 / 表头兼容 |
| atomic / shared_references | 双线程 / 双进程失败交错、随机名冲突、长 basename、部分写 / 编码错误 / KeyboardInterrupt；旧 target / 源 / peer 不变，只清自身普通 staging |
| logger / tracker / checkpoint 控制 | 追加、partial-line、恢复 offset 与 bundle / 诊断选择保持，既有 CPU 微小数值回归继续通过 |
| 架构约束与安装后 CLI | 替换文本绕行负例被拒，特殊协议精确控制；离线 Toy/Stub make→launch→process→status/logs/report；产物路径和合法 JSON 结构与原合同一致 |

正确性验收沿用 [testing.md](testing.md)、[tests/README](../../tests/README.md)、[cicd.md](cicd.md)：Ubuntu / Windows × Python 3.10 / 3.13 core 与 CPU Flow，以及两平台 wheel / sdist 安装。skip、未执行和通过分开记录；旧 CI 不替代实施后的验收。

## 九、本次状态

**已实施。** 本设计已按“不改文件架构和磁盘合同”完成三类收敛；`c9e0141` 的结构方案已撤回。PR #52 / #54 / #55 及后续 PR 是实际实施记录；旧成功保留范围、特殊协议和第七节不实施约束继续有效。后续正确性修复直接按已批准合同和失败回归推进，状态以确切提交、实际 dev CI 与开发记录为准，不重新等待设计批准。

## 十、实施进度

**三类均已实施并合入 dev，最终 head 与实际 dev 的全部实际 CI 已验收。** 完整改前/改后和旧失败见 [record §69–75](record.md)，历史候选或待验文字不表示当前待审。

| **类别** | **已合 PR** | **实际 dev merge commit** |
| --- | --- | --- |
| 第三类：原子文本发布 | [PR #52](https://github.com/DreamSoul-AI/RPipe/pull/52) | `ea2210bc27e5456062bdef0ffb331e208c64f838` |
| 第二类：三态安全读取 | [PR #54](https://github.com/DreamSoul-AI/RPipe/pull/54) | `945c9925c8657b50a7f0906c5cd4fd00b238f1a1` |
| 第一类：等价保全 predicate | [PR #55](https://github.com/DreamSoul-AI/RPipe/pull/55) | `34e95069a3a2814d0fabf4879b1584f1bdbd0758` |

第一类仅提炼原判断，42格合同与相邻128项锁定原成功保留范围；分支规则随 PR #55 合入。

特殊追加、checkpoint、锁协议保持，第七节能力未实施；没有布局、schema或 result 含义变化，未测速或运行 GPU。上述阶段先合 dev，后按 v0.4.0 / v0.4.1 发布流程进入 main；当前后续正确性工作仍只合 dev，本轮不修改 main。


## 十一、2026-10-15 全仓同类正确性接续

按最新授权继续三类系统核对。目录与磁盘合同唯一依据仍为 layout.md；不恢复结构性账本或改变已有成功保全范围。#53 原 index 反例已修且随 v0.4.0 发布，本轮先重验原回归，再补齐此前仅覆盖库包而漏掉的 Study 发布边界。

第三类本批：受跟踪的 Study recipe / flow / structure 中，可整体替换的报告、数据/源码 manifest 和 probe JSON 全部复用已有 atomic_write_text。main_probe 固定 `.writing` 是可复现的 peer 所有权缺陷（B-072）；测试先失败再修。数据下载的二进制 partial、原始 Git blob、OBSERVATIONS.pt、模型/checkpoint 不纳入文本转码，直接字节合同保持。precision.main.execute 的 worker.log 是 subprocess 实时写入流，按函数精确豁免，不作为整体替换报告。

只在隔离合成输入上运行回归；不会触发性能入口、下载数据或计算循环，也不修改旧 provenance。更改源码的后续研究须使用新 version / make 并重新过原门。第二类与第一类核对采用已有三态/保全判断，确认新反例后再补合同，不为制造工作扩大异常吞咽或保留范围。

第二类本批（B-073 / #62）：status 与数字表复用 report.py 既有私有 _fmt，统一有限数显示边界；浮点转换溢出/非有限值显示占位，status 继续寻找可用 fallback。report 对 metrics 先确认 dict，不把列表对强转为对象。只改显示，不放宽严格读取、计划一致性、compare 或 process 科学门；无新增函数层/文件/schema。22项合同先红20失败/2通过，正常peer和原字节由回归锁定。

第一类本批只补合同：六阶段原异常 × 失败结果编码/提交再失败12项，验证原异常身份、原错日志、旧result/peer和本写者清理，与原42项决策表共同锁定既有predicate。原规则全部通过，无新增生产反例，不改Runner/schedule/make的失效语义。全仓归纳及严格compare/process、流式/二进制/锁豁免边界见record §81；不把单文件原子等同多文件事务或任意同Run并发。

本轮第三类PR61与第二类PR63均已普通合dev，最终head和实际dev全部实际CI分别成功；#53/#62 后续已随 [v0.4.1](https://github.com/DreamSoul-AI/RPipe/releases/tag/v0.4.1) 发布关闭，旧 v0.4.0 六 Issue 不重开。第一类新增合同全部通过，生产规则不改。具体源码/红绿/验收链接见record §79—81；main与layout不变。

## 十二、2026-10-15 逐点清单后的三类边界补齐

以record §83全仓清单为准，所有固定产物位置/schema继续不变。第三类先修B-074：短随机名仍须排他取得所有权，失败不unlink未创建的peer；atomic_write_text先排他创建并记录owned，shared引用去掉递归晋升，软链→硬链创建与原子replace仍在原函数，迁移仅成功link后清自己。第二类B-076维持compare严格错误语义：坏容器拒绝，不降级成空；合法null兼容，整数不用float丢精度，有限容差按数学值比较，普通浮点比较逻辑不变；输出在CLI既有try内。第一类B-075只改日志/曲线构造器的append创建，目录错误与恢复offset不变，不扩大Runner旧成功范围。

三类分别先红合同、主题 PR 与最终/实际 dev CI；该阶段 Issue 后续已按 v0.4.1 发布关闭，后续批次按各自发版状态处理。无性能/GPU/原始结果覆盖，不增加账本、锁、目录、模块或第七节能力。

本节三类补齐已按PR69（第三类）、PR70（第二类）、PR71（第一类）普通合dev，最终head与实际dev全实际CPU/包CI分别成功；完整403组清单与验收见record §83—87。三类只在既有入口内部收敛，43项新增合同覆盖原反例和有效控制；既有布局/schema、专用协议与第七节不实施边界保持，该阶段 GitHub Issue 已随 v0.4.1 关闭，后续已修 Issue 仍在其下一版关闭。


## 既有文本表格的单元格合同补充（2026-10-16，B-086 / #97）

合法 factor 的 key/value 和 report metric key 允许多行文本，不应通过限制配置或修改持久化字节修显示问题。现有 status TSV 和 NUMBERS Markdown 是两种视图；格式化时统一将单元格内的 Python splitlines 行界及 tab 转为显示空格，保持普通空格与数字原样。Markdown 再先转义原反斜线、后转义 pipe，避免偶数反斜线让分隔符重新生效。只在现有 report.py 放私有纯文本函数，status 复用（已有 status→report formatter 依赖），不增加模块或输出格式。

status 对标题及全部9个单元格统一应用单行合同，替换 error/note 仅处理 LF/tab 的重复局部逻辑；report 对 factor、metric name/text、seed、Run ID 应用 Markdown 单元格合同，标题仅保持单行。日志链接仍复用 B-085 的 URI 规则，数字格式器与指标白名单不变。只写派生 NUMBERS 的原入口不变，reader 不改 index/process/result、peer 和人工结论。

验收先提交公共渲染红测试：factor key/value、report 自定义 metric、CR/LF/CRLF、tab、NEL/Unicode LS、pipe 与一/二反斜线 pipe及普通控制；校验固定行列、原数值/peer/链接/计数和输入精确字节。初轮误将自定义 metric 纳入 status 既有白名单的10测试假设已撤出，原始证据留存；更正后 56 项 39 failed / 17 passed、无 error / skip 是修复前基线，旧失败保留。**已实施：** [PR #98](https://github.com/DreamSoul-AI/RPipe/pull/98) 已普通合 dev `8dc6a914a6f0e0f42a2353f43e73505880672087`，最终 head 与实际 dev 全部实际 CI 已通过，见 [record §114](record.md)。此补充不包含通用富文本清洗、HTML框架、配置schema扩展或本设计第七节结构性能力。


### compare checkpoint 引用与显示合同（B-088 / #101）

layout.md已规定checkpoint为每Run的可变状态，compare的参数名称应指向该Run的assets/checkpoints。保留原regular-file/missing/目录/IO预检，再在既有_checkpoint内检查已解析的checkpoint根在已解析Run内、已解析文件在该根内，之后才调用torch.load。合法nested、自身绝对、内部文件/目录别名继续可用；缺失可选checkpoint仍按原missing，读取/程序异常不扩大捕获。保持原生IO路径，只对归属比较复用已有_log_path_key的Windowsnamespace键，不复制路径框架/移模块。

format_compare复用现有_text_cell，仅把动态key显示成单行；JSON原key、比较数值/容差、缺失判定和原输出位置不变。训练checkpoint保存/恢复与strict pickle语义不受影响；不建目录、搬产物或自动迁数据。错误外部引用由CLI既有ValueError边界返回2，旧--out不覆盖。

先Issue101，隔离实际三种外部引用可使完整比较错误true、正常不同权重control false；Unicode LS合法stem使显示5行而非4。再冻结原字节17节点10 failed/7 passed、0error/skip，包含API/CLI父路径/绝对/文件alias/根alias、合法nested/自身abs/内部alias/missing/普通名称/Windowsextendedown控制。原始快照与原始REPRO/REPRO2/导入path/hash/JUnit留.tmp/audit088（不随clone）；正式红测试先提交再改代码。record§111的compare key固定判断不完整，以本轮真实证据明确更正，不把旧结论当新验收。

**已实施：** B-088 / #101 经 [PR #102](https://github.com/DreamSoul-AI/RPipe/pull/102) 普通合 dev `ef6084681160a68588e0b2a359ab7cc07c398879`；checkpoint 归属预检与 compare 单行显示合同均已落地，最终 head 和实际 dev 全部实际 CI 已通过。上述 17 节点 10 failed / 7 passed 明确为修复前证据，不再表示未修或等待批准；后续验收见 [record](record.md)。第七节能力仍不实施，不修改文件架构与产物布局。


### 混合整数数值的比较精度（B-100 / #127）

dev8c9cb99上，整数2**60+1与实际可表示浮点float(2**60)差1；metrics/history/checkpoint标量和整数-实数/复数tensor共五入口、两向十个公开API反例均错误通过并显示差0。不是#122同类整数张量容差或#124有限运算溢出；这里在减法/张量提升前已经丢失整数位。统一在现有compare边界保全表示值，不改生产者/格式/容差定义。

Issue127先开，031571a先提交120个公开API+CLI/输出JSON回归：五入口×正负×两向，另unsigned实数/复数两入口正向值×两向；各覆盖零容差、绝对界外/界上、相对界外/界上。实际72失败/48有效边界控制通过，0error/skip；每项均先执行API和CLI再断言，输入与peer原字节保持。红XML/log、原REPRO/原源码ZIP与SHA在.tmp/audit100，不随clone。

实施方案：整数/整数既有Fraction分支保持；现有_close对混合int/float先处理唯一浮点的非有限身份，再用现有Fraction按表示值计算差及绝对+相对界，输出可表示差转float、超范围仍inf/原JSON null。现有有限complex精确分支保留平方范数判据，用精确实/虚差分量计算显示norm，避免先把整数转complex后报告差0。Python complex标量原严格比较不扩展。

tensor保留原CPU输入再转现有float64/complex128供普通格和非有限身份处理。只在一个输入为非bool整数、另一个为float/complex时，将该有限格与原溢出格一并复用_close；从原输入item取值，同时写回passed格和显示diff。纯float/complex、纯整数、bool、shape/empty、配对NaN/inf、checkpoint归属及严格读写合同保持。无需helper/模块/依赖/新schema，不改变Run产物/科学门；本批只CPU合同，不测速/GPU。


### Runner日志失败留证（B-101 / #130）

第一类相邻复查确认：Logger构造/首条start info在run既有try之外，日志路径为目录或IO被拒时，即便result可写仍不进入failed回写；旧成功保留成误导状态。_log_failure中的_ensure_logger也在诊断try之外，阶段原错可被二次日志初始化错误替换并阻断结果留证。先开Issue130，再以8a351df提交10项正式红回归，10 failed/0error/skip；六启动节点（初始化拒绝/首条输出拒绝/真实目录×新旧result）与四诊断节点（collect/process×新旧result）均实际执行。

实施方案：将原构造与首条输出移入run既有try，current仍先None；_log_failure对_ensure_logger再失败直接返回，让外层_write_failed_result和原bare raise继续。既有日志exception→error fallback、failure_logged去重、_preserve_success初始冻结拒绝/process规则、result/schema/路径、append日志与最终succeeded输出时机保持。不引入新helper/模块/logger框架，不改文件架构和产物布局。

原草稿六节点被夹具重复mkdir阻断的证据保留且排除；更正草稿10红与隔离候选10绿只供方案验证，不替代正式入库红/绿。正式RED.xml/log、修复前源码ZIP/hash和最初无效success夹具拒绝在.tmp/audit101（不随clone）；portable回归已入库。后续先验证现有42项保全决策表、12项结果写二次失败及Runner/execute/Logger相邻合同，再CPU Flow和最终head/actualdev全部实际CI；不跑GPU、性能或Study。


### 原生训练结束flush的异常优先级（B-102 / #132）

第一类继续复查：_run_supervised的finally无条件flush_state，训练迭代器先失败、tracker编码/原子发布再失败时，后者成为对外异常和result.error；前者仅留在traceback chain。execute外层best-effort flush无法还原已经被替换的异常。首个stub探针走_run_stub未执行迭代器，作为无效夹具保留；更正非stub零批次/零优化步CPU探针确认原错确已执行。

Issue132后，1e8c5d1先提交22项合同：unit12覆盖RuntimeError/KeyboardInterrupt原错、编码/发布二错、Logger可用/拒绝/缺省；Runner8覆盖两种原错×两种二错×新旧成功；另2项正常训练体完成后最终flush拒绝的控制。正式20 failed/2 passed、0error/skip。初轮两控制在默认周期checkpoint过早注入导致夹具失败，原始RED_INITIAL与说明保留，更正checkpoint_period=0只在终点注入；未放宽生产判据。

实施方案：仅把原finally拆成except BaseException与else。已有异常时在嵌套try尽力flush；二次普通Exception时使用现有Logger.exception尽力记traceback，Logger本身再失败不遮原错，最后原bare raise。成功体的else仍直接flush并传播错误。捕获BaseException仅为保留原finally在终止时的flush动作和原异常身份，原终止不转为成功或新failed；不吞新的终止信号。不用sys.exc_info或共享状态推断失败，不加helper/模块/格式/路径，不改数值、checkpoint/RNG或周期内flush。

相邻调用方已核对TrainAlgorithm.run/legacy run、execute的finally，以及eval_hook/Trainer回调；当前只在原生训练已有finally确认同类反例，其他路径无反例不修改。正式红、原始探针/拒绝、源码ZIP/hash在.tmp/audit102，不随clone。后续训练/恢复/checkpoint/Runner相邻、CPU core/Flow及最终head/实际dev全部实际CI分别验收后合dev、关闭Issue、普通清理本地/remote；无main/tag/GPU/测速或正式Study。


## B103 / #134：共享锁路径别名的重入身份（2026-10-16）

已复现：Windows 普通 drive 路径与扩展命名空间路径经 resolve 后仍有不同字符串，samefile 确认同一目录；两个嵌套方向在 timeout=0 下均因同线程再次取得同一 OS 锁而失败。42c8b5b 先提交四项原生 Windows 回归，四项全部失败；覆盖两个方向及内层正常/异常退出，并要求其他线程仍被排除、外层退出后可重获。

最小方案：保留 resolve、mkdir 与全部 OS 锁和预算流程，只将线程局部重入表的字符串身份改为目录 stat 的 st_dev/st_ino 物理身份（与标准库 samefile 一致），同时保留 pid 隔离。原 root 仍用于全部文件操作和错误信息；不改变共享输入目录、锁文件、磁盘布局或持久化格式，不引入跨层 helper。调用方 prepared_resource、migrate_shared、data/model factory、data prepare 均复用这个共享根实现。验证既有进程/线程排斥、死亡持有者释放、取消与零预算合同；不运行 GPU、正式 Study 或测速。


## B104 / #136：多根迁移锁按同一物理身份排序（2026-10-16）

继 PR135 的同线程重入验收后，原生空目录探针发现 migrate_shared 的 key=str 排序受 Windows namespace 拼写影响。同一 Study 两种表示在 shared.root 为普通/扩展绝对路径时，可交换私有根与共享根的取得顺序。真实 OS 锁、两线程各持前两根后请求第三根，零等待预算注入使两侧立即暴露循环，四根退出后均可重获；不作测速或运行真实迁移。Issue136 后四项排序回归先提交，2 failed/2 controls passed。

方案：在 shared.py 内将 PR135 已有 stat 二元身份提取为最小私有函数，cache_lock 的 pid/物理键保持；migrate_shared 仍只锁既有四个根，先确保这些原本就会 mkdir 的目录存在，再按同一 st_dev/st_ino 身份排序取得 OS 锁。排序不依赖 namespace/大小写或声明名称；同物理身份的重复根只重入，不改变数据迁移、冲突验证、硬链接、staging 或文件布局。跨目录并发更名不属于共享不可变输入根合同。保留既有失败、权限拒绝与取消行为，不增加全局互斥、模块、依赖或磁盘格式。


## B105 / #138：非零退出先留证，再输出诊断（2026-10-16）

在5762925上复现：子进程替身写出合法succeeded结果后返回1，schedule错误行stdout再抛BrokenPipeError，原实现先print导致既有失效写回完全未执行，run_succeeded仍真。没有真实子进程、训练或数据。Issue138后e599e4b先提交八项：BrokenPipe/Permission/UnicodeEncode三类×retry开关6 failed，正常输出两控制passed；peer与config原字节保持。

最小方案：仅把非零退出分支的print移到已有success→failed纠正之后。stdout原异常仍按原方式传播，不吞掉异常、不增加新logging策略；retry和codes、已有failed结果/无结果的处理、spawn语义、train/eval屏障和磁盘格式保持。不把诊断失败当成功；若纠正写入本身拒绝，原有严格写错误仍传播。相邻blocked依赖已先写failed再输出，announce/report/compare已先发布再输出，Runner日志二错已有B101/B102合同，不重复改动。当前设计已实施后按本机与最终/实际dev全部实际CI验收。


## B106 / #140：PNG单文件原子发布与自有figure释放（2026-10-16）

90078c8上已复现：有效旧PNG被savefig部分写覆盖，原OSError传播但新figure遗留。Issue140后5eef63d先提交19项C2回归：构造axes/绘制/部分保存/取消/替换/排他创建失败×新旧目标、UUID碰撞、清理拒绝、两项有效PNG控制及真实Windows只读目标；正式17 failed/2控制passed、0error/skip，首轮18项原红亦保留。测试放既有flow/process镜像目录的test_curves_publication.py，单独C2避免给原C1文件叠加两个cost marker，不新增生产模块。

实施方案沿用已批准第三类合同：只改curves.py，在原figsize下取得figure后立即进入finally所有权范围，再以等价fig.subplots取得axes；绘制主体原样缩进，不改数据、坐标/标注、dpi140或路径。短UUID的同目录.tmp用xb排他取得所有权，savefig向该文件句柄以显式PNG格式写入，关闭后复用既有atomic_replace发布。目标原mode沿用；新目标沿用普通open的umask。临时创建失败不unlink peer，自有文件才best-effort清理；PermissionError只对自有独立临时文件恢复0o600并重试一次，其余OS拒绝留诊断，不遮原保存/替换异常。最外finally始终只关闭本次取得的figure，peer figure保留。现有checkpoint诊断staging、共享硬链接和日志追加均不改；不加通用I/O框架、文件夹或schema。

验证：19新节点全部执行、旧图/缺目标/输入/peer bytes和mode，PNG有效控制及原绘图合同；本机另用冻结旧函数与新函数对相同合成输入比较PNG原字节。随后core、CPU Flow及最终head/实际dev全部实际CI分别验收。源ZIP/失败、原始PNG和哈希在.tmp/audit106，不随clone；无GPU、测速、正式Study或main/tag。


### B106同批补充：临时流关闭IO二错（2026-10-16）

9da02b4相邻故障注入确认：with临时流的__exit__/close再抛OSError，会替换savefig已经抛出的RuntimeError或KeyboardInterrupt。a095ef0先提交6项补充回归（两种首错×新旧目标4 failed，成功保存后close错误的2控制passed），与最初19项红分开保存；仍属Issue140/PR141，不另起主题。

仅把临时流上下文改为显式取得/关闭：保存已有BaseException时尽力close，只有关闭OSError不替换首错，随后bare raise；保存正常时close仍严格传播，未关闭成功不发布。捕获BaseException只为取消也释放资源且原样重抛，不吞新的终止信号或关闭中的非IO程序错误。PNG绘制/权限/所有权规则不变。原head9da02b4的CI快照保留，新代码应更新同一PR并重新接受最终head的实际CI，不能沿用旧head绿作为验收。


## B107 / #142：共享锁释放与关闭的首错保全（2026-10-16）

实际OS锁取得后注入unlock/close IO，准备RuntimeError或KeyboardInterrupt均被OSError替换；不同线程随后仍能零预算重获。Issue142后7614d31先提交25项正式回归：19 failed/6有效控制passed，0error/skip。覆盖两种/同时清理故障、首错/取消/正常体、重入、获取busy/坏descriptor后close、prepared_resource诊断staging及非IO程序错误；未假授锁，无训练/测速。

最小方案只改既有shared.py的cache_lock：复用已导入的stdlib ExitStack，在文件取得后登记close退出回调，取得OS锁后再登记release回调；逆序先release、后close。回调只在已有exc_type时不让自身OSError替换首错；没有首错则照常抛出，两个清理故障保留先发生的unlock错；非IO程序错误不吞。回调不返回True，不抑制原异常/取消。获取失败不登记unlock，仍保留Timeout的cause与原预算。held移除时机、物理root/PID键、OS原语/死持有者释放、模式与锁文件、多根排序和输入staging保全保持。不加资源框架、模块、路径/schema或共享状态推断。

native Windows/Posix各用原生获取，仅注入cleanup/acquire故障；失败/取消和普通严格控制均要验证descriptor关闭及另一线程真实重获、不可变输入字节和builder staging保留。随后共享相邻、core/CPU Flow与最终head/实际dev全部实际CI分别验收。原始探针/ZIP/red在.tmp/audit107不随clone，正式回归/设计随Git提供；无GPU/main/tag或正式Study。


## B108 / #144：普通文本及ledger流关闭的首错保全（2026-10-16）

PR143最终head全部实际CI通过并正常合dev e589862d，实际dev CI尚在运行；对应锁主题分支保留至验收，不混入本批。Issue144后dcd4e2d先提交30项正式红回归：16 failed/14有效控制passed，0error/skip。现有atomic_write_text、Logger._emit、AlgorithmTracker.begin_run/append_jsonl四入口，真实部分写入/flush或编码错误和取消遭文件上下文close OSError替换；原错仅在context。12探针中8首错/取消重现、4正常close严格控制，原始证据在.tmp/audit108，不随clone。

沿用已实施第一/第三类合同：只改既有_atomic.py、logger.py、tracker.py的上述四个写入块，显式取得句柄；正常体完成后close严格传播，已有BaseException时尽力close，仅关闭OSError不遮原错，随后bare raise；非IO程序错误继续暴露。不新增通用I/O层、helper/module或依赖，不用异常context恢复原错。打开模式、编码/newline、追加顺序、flush、JSON/日志字节、目标权限/umask、原子替换、自有tmp清理和checkpoint诊断staging均保持。原子文本改用同参数Path.open/write，两个旧测试从Path.write_text高层注入调整为同一真实文件write的部分写入注入，仍验证原错身份、旧目标和peer不变；先在未修生产上验证这些既有相邻合同，再修。

30新回归覆盖新旧文本目标、实际UnicodeEncodeError、日志write/flush、tracker起始标记/指标追加write/flush、OSError与KeyboardInterrupt原身份、正常close失败、非IO关闭程序错、实际descriptor关闭、旧ledger前缀/peer字节、失败不提交tracker state。测试共用既有tests/conftest.py的局部故障fixture，未改元数据筛选/测试框架；三测试文件沿用模块原标签。随后相邻/core/CPU Flow、最终head和实际dev全部实际CI验收，正常关闭Issue与清本地/remote主题分支；无main/tag/GPU/测速/正式Study或布局改变。


## B109 / #146：Study comparison PNG剩余直写点（2026-10-16）

跟踪源码savefig清单中，库curves写入已由PR141验收，但studies/main_exp/flow/process/curves.py的plot_report仍直接写historical_comparison.png且仅成功后close。实际Study process入口run.py→curves.main调用该helper。仅AST抽取helper、用合成report/reference的4探针：渲染/部分保存/取消各泄漏1个自有figure，后两者把2398字节旧有效PNG覆盖成11字节；正常保存76906字节且不泄漏；peer figure均保留。不运行Study main/门限、数据/模型/历史归档、checkpoint或训练。

Issue146后6ab3b74先提交25正式节点23 failed/2正常控制passed，0error/skip。采用库PNG已批准合同的最小本地实现：保持原figsize=(14,5)、dpi160和1×2 axes，先取得figure即进入finally所有权范围，再fig.subplots；绘图主体仅缩进、原行保持。同目录UUID.tmp以xb排他取得，向句柄以显式PNG格式savefig、关闭后既有atomic_replace；正常close失败严格、不发布，已有保存错误/取消时close OSError不遮首错。保留原目标mode、新目标umask；只有自有普通tmp尽力清理，只读自有tmp可恢复0o600重试一次；未取得所有权的碰撞不删，peer figure保留。不抽新通用I/O层/模块/依赖，不改变helper返回None、文件架构、最终路径/格式、科学门或阈值、数据与坐标/标注。

测试在既有flow/process镜像目录AST读取当前Study代码，不依赖正式Study输入/结果，不启动main。覆盖新旧目标、axes/render/save/cancel/replace/create、碰撞、清理拒绝、原生Windows只读、close二错和正常PNG/图像数据控制；25新节点均需执行，随后相邻PNG、core/CPU Flow与最终head/实际dev全部实际CI。冻结修前与修后对同一组合成输入比较PNG字节，确认科学比较/读取/gate函数原字节块保持。正式未来受影响Study复跑按新version/实际provenance和既定前置门，不将本次图像工程检查冒充科学实验或重写旧结果。原始探针/ZIP/hash/红绿在.tmp/audit109不随clone；无GPU/测速/main/tag/数据删除。


## B110 / #148：不可解析provenance的冻结拒绝分类（2026-10-16）

实际JSON解析12000层metadata数组触发RecursionError，check_frozen仅归一化OSError/ValueError使其逃逸；实际Runner outer gate与study phases关闭时的native prepare gate均在零data/model构建、零execute调用时，把旧succeeded结果覆盖为failed。新Run也写failed但缺少既有冻结拒绝类型。初2000层被本机Python3.13接受，只触发普通changed-provenance拒绝，作为无效反例原脚本/log保留；更正12000层不调递归限额，原始深层JSON/四新旧路径证据在.tmp/audit110，不随clone。

Issue148后dadb558先提交12项正式回归5 failed/7有效控制passed、0error/skip；覆盖strict check_frozen、outer/native prepare新旧结果与普通坏JSON对照、freeze关闭不读坏文件、strict load_provenance仍抛真实RecursionError、非递归RuntimeError程序错不归一化。方案只在既有check_frozen异常元组补RecursionError，仍通过原raise ProvenanceChangedError from exc保留cause；不改strict loader/json解析/递归限额、schema、freeze真假判据、Runner现有保全决策表或真实业务phase失败规则。调用方Runner/prepare/launch/Study process继续使用同一公共边界，不复制guard或增加模块/依赖/新路径。新测试没有执行data/model/algorithm，仅native prepare到既有冻结门即拒绝。

随后provenance/freeze相邻、core/CPU Flow和最终head/实际dev全部实际CI分别验收；保留坏输入原字节及新旧结果边界，不以吞程序错误或放宽冻结检查通过。无GPU/测速/正式Study、main/tag、数据/Run/provenance删除或布局变化。


## B111 / #150 与 B112 / #151：读取边界集中里程碑（2026-10-16）

遵循18:53新频率规则，两个同类缺陷及§174系统读取点清单在唯一codex/optional-read-boundaries分支本地累积，完成全里程碑后才统一PR；不按单行文档或单个反例拆PR。已先Issue150/151，再8062f71提交44正式节点12 failed/32有效控制passed、0error/skip；c79b17b补4计数显示节点1 failed/3有效控制passed、0error/skip，两份原红分开保留。

可选结果读取：_load_result已将缺失、语法损坏或打开/读取IO作为不可用Run，但stat在try外、非object的CorruptArtifactError和JSON RecursionError逃逸，阻断有效peer。方案只把原is_file查询纳入原try，并沿scheduler._run_result现有模式补CorruptArtifactError/RecursionError；不可用行仍status None/metrics空，n仍仅有效succeeded，n_planned仍包含所有计划Run，complete仍false，peer均值/标准差公式不改。strict load_result原样拒绝，正常结果/计数、指标转换/科学门及所有Run/index原字节不改；不把坏metrics mapping或非有限数归一化，本批不改聚合公式。

严格CLI：status/logs/report/compare四个既有错误出口只补已知RecursionError，仍拒绝坏权威index/process/result、返回2并输出原诊断，不把strict loader降级为默认数据，不写旧NUMBERS/compare结果。可选_print_launch_counts则在其既有best-effort元组补同一解析错误，保留语法失败不输出、有效计数照常输出。非递归RuntimeError程序错误仍传播。仅改既有aggregate.py与cli.py，不加通用helper/框架/模块/依赖、schema或路径，不修改checkpoint/RNG恢复、Study科学审计或JSON限额。

44主回归覆盖6解码/缺失/有效结果、stat/read的permission/IO/disappeared/编程错误、strict公共loader、实际process partial/complete、四CLI的深层/语法/非object、正常输出和编程错误；补4计数显示深层/语法/正常/程序错。全部用合成临时输入，不跑data/model/algorithm或正式Study；compare正常控制仅CPU序列化空checkpoint字典。随后可选读取及strict readout相邻、core/CPU Flow、最终head与实际dev全部实际CI分别验收。原探针/源码ZIP/hash、44红与4补红在.tmp/audit111不随clone；不删除原失败/校验拒绝。


本机实施与验收：00f3ddf仅修改上述两个现有文件，9行增加/8行删除；48新节点全部执行/通过，core1834 passed/1既有平台skip，CPU Flow247 passed。其他aggregate函数原源码及CLI五个异常元组之外AST均保持。§174清单中的两个待修项已本地实施，最终head/实际dev CI仍待统一里程碑PR验收；不是待设计批准。详record§175，原红/绿/hash在.tmp/audit111不随clone。


## B113 / #153：同一读取里程碑补齐 Run 级回读（2026-10-16，已批准合同内实施）

继续源审发现individual.run的独立读取入口未列入§174原20项清单。实际FlowRunner(process-only)六探针中，非object、深JSON和stat PermissionError三类使派生失败且经既有Runner失败回退改写原结果；语法/读Permission/有效输入三个控制按预期保全。正式14项先红4 failed/10有效控制passed，0error/skip，含新增stat IO；原始坏文件及被覆盖后证据在.tmp/audit113保留，不随clone。

沿Run process已有缺失/坏语法/read IO的可选降级合同，只把is_file移入原try并补CorruptArtifactError/RecursionError，保留ctx.state有效mapping优先、无index要求、原metrics转换与history读取。严格公共loader和Runner通用失败/旧成功规则原样，不吞任意RuntimeError，不改路径/schema/恢复/科学门。此为#150相邻遗漏消费者，加入同一PR152，不另开小PR；旧head CI不取消，不能代替补齐后的最终head验收。


B113已实施22a4d57：14新回归全执行/通过，重新core1834 passed/1既有平台skip、CPU Flow261 passed。连同B111/B112共62新节点实际执行，21项清单已补齐遗漏入口；仍在同一PR152，最后head与实际dev CI分别验收。旧head证据保留，非待设计批准；详record§176–177。


## B114 / #154：Flow execute 最终写入与成功判据（2026-10-16，按已批准失败写入合同实施）

Flow通用execute在finally无条件吞掉tracker.flush_state的Exception，覆盖了原生train之外的成功收尾合同。实际Runner execute→collect→summarize→write、真实AlgorithmTracker与合成Algorithm入口六探针中，正常算法体加最终发布拒绝/真实JSON编码拒绝仍定稿succeeded且旧空tracker保留，两个反例；正常发布与三种已有body失败控制正确。无data/model/optimizer/训练或科学运行。Issue154后aae4b0e先提交24正式节点6 failed/18有效控制passed、0error/skip。

最小方案：在现有execute局部记录正常体（含现有info日志）是否完成；finally中正常完成后的flush严格传播，已有算法/日志错误或终止时仍用原best-effort flush。这样沿原Runner错误留证阻止后续collect/summarize/write成功，不改变Runner通用失败策略；原execute异常日志、failure_logged、返回值/observations和单次flush次数保持。无新helper/模块/依赖、格式/路径/数值/周期flush或checkpoint/RNG改动；原生train已实施的成功严格/失败保首错规则不动。候选单行文档不推送，完整失败写入里程碑一次PR→dev。


B114已本地实施82890ce，8行增加/3行删除；24新节点全执行/通过，core1846 passed/1既有平台skip，CPU Flow273 passed。SOURCE_CHECKED精确原字节替换证明算法/日志/Runner/数值等边界未变；最终head/实际dev全部实际CI待完整里程碑验收。详record§180；当前不是待设计批准。


## B115 / #156：不可变历史源码的排他创建与首错保全（2026-10-16）

main_exp.export_source和main_probe.MainArchive.__init__均以exists→mkdir→write_bytes写固定归档。八个隔离合成Git blob探针用真实线程落盘复现两处竞态覆盖；六个全新/既有相同/既有不同控制正常。此为确定性交错，不声称正式归档曾损坏；未加载任何历史源码、bootstrap或科学运行。正式0383bd1先红28项12 failed/16有效控制passed、0error/skip：四竞态（含相同peer的竞争失败后可重试复用）和八个原写错被close IO替换反例。最初夹具Git tree闭包被AST变量覆盖的28失败为无效，单独保留，不当库失败。

沿原“不同已有源码不得覆盖”合同，两处现有else分支改为xb排他创建，不新增文件/模块/依赖/通用IO层。失去创建所有权时原生FileExistsError拒绝，不删除或截断peer、不写新manifest；胜者完成后相同内容按原exists/read校验复用。自己已获所有权的部分写入/终止保留诊断字节，close仅OSError不得替换原错；成功体close失败严格传播，manifest只有全部blob正常后才按既有原子writer写。既有相同/不同判定、原Git批协议/固定commit、manifest键/hash/文件名和所有归档路径保持，不改科学算法/阈值、checkpoint/RNG或恢复合同。两处统一一个主题里程碑，后续先本机/CI验收再合dev；不执行或覆盖正式Study归档/数据。


B115本机验收：28新节点全执行/通过，core1874 passed/1既有平台skip、CPU Flow273 passed，0failure/error。两文件精确原字节证明仅替换各一条write_bytes，22增/2删；原红12失败/16控制、无效夹具/初稿拒绝均保留.tmp/audit115不随clone。无正式Study或归档执行，最终head/实际dev CI待完整里程碑验收，详record§183。


## B116 / #158：只读日志按实际时间排序（2026-10-16）

event_lines文档与既有测试要求timestamp order，Logger原生输出RFC3339毫秒及UTC偏移。原排序键直接用墙钟字符串，六个实际CLI探针中DST回拨/跨日/跨偏移三反例返回0却晚事件在前；三个同偏移控制正确，输入/索引/日志字节全不变。Issue158先于正式28红8 failed/20有效控制passed、0error/skip，补库/CLI两入口与Z/不同小数精度。

只改logs.py排序键：已识别且带时区、在datetime精度内的时间用标准库转UTC，并以固定微秒精度字符串比较，不用浮点epoch、不改原行文本/Logger或输入格式。缺失、坏/无时区时间、UTC转换溢出继续原文本/9999 fallback；高于微秒的旧文本保持原排序键，避免datetime截断造成新的顺序损失，不宣称为其新增UTC语义。相同键仍沿Python原稳定排序。event选择/traceback筛选、path归属与可选IO guard、strict index/CLI拒绝、状态/聚合/科学门均原样。无新helper/依赖/配置/路径，不设全局时区。

整批回归/源码保全/本机与最终head/实际dev CI验收后统一PR，不因几行设计另开PR。无GPU/测速/正式Study、数据/Run/provenance改写或布局变化。


B116已本地实施ba665de：28新节点全执行/通过，core1902 passed/1既有平台skip、CPU Flow273 passed，0failure/error；原字节证明仅导入/排序键变动。超微秒旧文本不新增UTC排序合同，fallback保留，严格恢复/科学门不变。最终head/实际dev CI待完整里程碑验收，详record§186；原证据.tmp/audit116不随clone。


## B117 / Issue160：同进程数据端点绑定的并发所有权（2026-10-16，已确认实施方案）

`bind_endpoint` 临时修改第三方数据集类的 url / mirrors。DataFactory 的 cache_lock 保护各自资产根，无法保护两个不同根共用的同一个 Python 类。合成类、两个线程与事件调度复现两次上下文读错 URL，退出后还残留国内 URL；不访问网络、真实缓存或 torchvision。先 Issue160，再 9c95667 正式红回归：8项中4 failed/4有效控制 passed，0error/skip，覆盖CIFAR URL/MNIST mirrors、正常/原错退出和同线程嵌套恢复。

方案在既有 bind_endpoint 这一共同边界使用一个进程内可重入锁，持有范围覆盖保存原字段、绑定、整个构造上下文与 finally 恢复。普通互斥锁会破坏现有同线程嵌套，因此用标准库 RLock；不把锁放在每个资产根，也不新增每类锁注册表/依赖/模块。不同进程仍沿既有磁盘 cache_lock，不宣称进程锁替代磁盘锁。端点定义、调用签名、yield endpoint、正常和异常恢复、数据构造内容、下载/hash/布局与科学门保持。锁只解决原本不可并行安全的进程全局类字段，非测速/性能优化。

调用方只有原生 _build_torch_vision 中 train/test 两次构造的同一个上下文；既有调用方无需改。apply_model_origin 是显式进程环境设置，非临时绑定合同，不把本方案扩展成配置新语义。临时原ZIP/hash/probe/红XML在.tmp/audit117不随clone，正式回归按source镜像放tests/rpipe/structure/test_origin.py。生产修改后复查实际Factory路径、首错/嵌套/恢复及相邻origin，再做core/CPU Flow；同分支积累完整里程碑后再PR dev。

实施补记：fffd872已按方案实施；测试改名test_origin_concurrency.py避免pytest同名模块收集冲突，首次收集拒绝保留。新旧端点13全通过，core1910 passed/1既有skip、CPU Flow273 passed，新8全部执行；实际Factory双根原版反例/当前恢复已核对。最终head/实际devCI待完整里程碑验收。


## 190. B118 / Issue162：共享准备的临时环境所有权（2026-10-16）

同类全局状态复查确认prepare_shared_data在独立项目缓存根下并发保存/设置/恢复TQDM_DISABLE，后一个_prepare会看到None/调用方原值，全部退出还残留1。第一次探针误用共享同一根的两个Study，现有cache_lock使人为交错等待超时；INVALID_PROBE_NOTE保留，不计库失败。改用两个独立项目根后实际公共prepare入口/真实cache_lock+合成_prepare稳定复现，无数据下载。先Issue162，再b04a5cd红9项4 failed/5有效控制passed、0error/skip，覆盖初值缺失/自定义/已为1、正常/原错退出和同线程嵌套。

方案仅在prepare.py增加标准库RLock，以独立的进程内环境所有权范围包住原保存、设置、cache_lock内_prepare和finally恢复；必须在原资产根锁之前取得环境锁，维持一致顺序。同线程嵌套仍可重入，异常和返回值原样。与bind_endpoint锁分别保护不同全局对象，不引入通用全局状态框架、配置或路径，不改_prepare/body、下载/hash、数据/结果/科学门。与B117统一归入当前PR161临时进程状态并发里程碑，原已触发CI保留，新增代码后以新最终head重新验收；不因观察超时取消或重跑CI。

B118实施补记：0aaae0b已按方案实施；与B117统一里程碑的新旧相邻27项全执行/通过，合批core1919 passed/1既有skip、CPU Flow273 passed，新17全执行。两个原始生产文件各自精确字节核对，仅锁和原上下文范围改变；不改body/端点/下载/hash/格式/布局或科学门。最终head与实际devCI仍待分别验收。


## 195. B119/B120 配方临时导入状态所有权方案（2026-10-16，实施方案）

目标同时修同目录helper串用和执行时缺失recipe模块身份，保留现有load_recipe/apply_recipe调用、路径/来源清单/注册时机与正常异常行为。只在原recipe.py复用标准库contextmanager/RLock建立私有临时导入上下文，不新增架构模块、公开配置或产物目录。

进入load/register现有sys.path范围时，在同一个可重入进程锁内登记当前目录可导入的本地顶层模块/包名；仅暂存并移开这些名字及子模块的现有cache项，执行结束清理该范围新项、原对象原样恢复，路径亦恢复。内建/标准库名字保持原Python优先级，不批量清空sys.modules或第三方cache。其它非冲突第三方模块不进入所有权范围。锁在当前模块保存/移开/导入/恢复全程持有，支持同线程嵌套。

load_recipe在上述上下文内执行spec前，将自己的既有模块名临时注册sys.modules；无论exec/校验成败都恢复此前同名项（包括不存在），使dataclass可解析定义模块。load后返回ModuleType、注册后已捕获的函数/模块全局引用继续有效；测试明确核对两个Study注册函数各自返回原值和既有外部模块身份。register时的同目录import同样走该上下文。保留当前load使用recipe父目录、register使用Study根的路径行为；不把此修复扩展为新公共namespace API、清空外部模块、任意后端的序列化保证或跨进程解释器改造。

按先红与方案后改生产，完整测试须包含正常/原错、模块/包、load/register、外部cache保全、注册引用和dataclass根/嵌套/eager/future，以及已有Study hooks/来源冻结/core/CPU Flow。现有Study phase私有namespace只作设计参考，不为了复用反向依赖flow层或搬文件。当前仍本地积累，不立即新PR；进一步反例保留并修合同，不能以测试全绿替代边界核对。

实施前边界补充：模块身份须在load exec与register调用两个上下文均可见，不只加载时；中间候选26项通过后，register内dataclass新4项仍2失败/2控制，此反例RED_REGISTER保留。统一由同一私有上下文保存/注册/恢复当前ModuleType，保留每次上下文前的同名cache项。生产提交在此修正和验证之后。


## 197. B119候选晚导入回归：不接受31项基线替代新边界（2026-10-16）

候选da6d0b0在31项定向测试、core1950 passed/1既有skip、CPU Flow273 passed上通过，但随后的注册对象生命周期复查确认：配方顶层已导入helper，注册的builder稍后执行时再次from helper import，会因上下文已清cache而ModuleNotFoundError。4a53165新增模块/包2红均failed、0error/skip；这是新候选引入的支持行为回归，不计原始14/4红，也不以先前全绿接受候选。两个新节点不在此前CORE/FLOW收集清单中，当前33项边界未通过，不能冒称整体全绿。候选及红测试只在本地，未推送、未开新PR、未影响dev8480d79。

§195暂存/恢复设计须继续覆盖注册函数晚导入的生命周期，不能简单清掉本地cache就作为最终实现，也不能改成禁止已支持的late import或粗暴清空外部模块。下一步先扩展私有导入绑定/模块所有权方案，保留这两个反例及原始预期，再修代码并重新执行当前全部节点/相关Flow。不是外部审批/网络阻塞，不因实现复杂而暂停；继续按20:56在当前唯一未合分支合批本地积累，不立即推PR。原始与候选失败、XML/hash均在.tmp/audit119/120不随clone。


## 198. 加载隔离生命周期与checkpoint兼容方案修正（2026-10-16，本地里程碑）

隔离namespace原型在.tmp中通过现有33项（非生产验收），但真实System save/load探针发现必要兼容边界：原裸helper类的model._extra_state可原生写读，namespace原型注册成功后读旧bundle却ModuleNotFoundError。首次探针误把device传SystemConfig构造器，另存无效夹具；改用既有from_mapping后复现。随后正式Flow prepare→System旧状态/新状态换Study根恢复两回归均红，不能靠只通过纯import测试接受namespace方案。

修正方案仍只改既有文件内容、不增模块/产物布局：recipe.py按物理Study根复用独立私有包，标准库source loader给该包的Python helper绑定本地import；普通import/from/包相对导入与注册后的晚导入都解析该Study，不清外部裸名cache。每Study固定包/recipe键复用，不按每Run无限增新包；不同Study物理根分离。finder只处理该私有前缀，不触碰第三方模块的__getattr__；load失败恢复此前recipe模块项，正常Python模块生命周期承载注册对象和dataclass定义模块。既有Run对象仍由Factory逐次构造，不把module代码cache当data/model/RNG对象cache。

私有类模块身份会影响含自定义Python对象的pickle全局引用，因此同步提供限定当前Study的兼容Unpickler：裸本地helper或历史私有包前缀映射当前Study同名helper/recipe类，非本地/标准库走原pickle查找。system_api新增私有桥，仅在标准Run assets位置且该Study recipe包已加载时提供reader；System.load_checkpoint全部已有torch.load点和compare checkpoint reader通过此桥取可选pickle_module，默认无recipe时kwargs/行为保持。System层不反向import make，仍经API跨层。Flow既有prepare注册顺序保持，不新增配置/公开选项/Run字段/序列化版本，不重写旧checkpoint；原torch.save、piece/bundle/diagnostic路径及恢复/科学门不改。

该桥须验证旧裸helper对象、不同物理根的新私有类型对象，以及外部cache保全；比较reader同步兼容，不把修改限于刚好测试的System入口。外部绕过RPipe直接反序列化自定义类仍需加载其定义代码，不能宣称任意plugin/宿主均可无上下文反序列化；native RPipe读取合同必须兼容，未通过之前不接受整批、不推PR。当前按20:56同分支本地积累，进一步反例先保留再修，任何旧绿色检查不代替新边界。


## 199. namespace候选的兼容与所有权反例（2026-10-16，本地）

命名空间原型先通过33项，但旧helper自定义model._extra_state的真实System兼容探针失败；39cacdb补Flow prepare→System旧类/迁移后类两正式红均failed。当前候选的私有recipe模块曾用合法helper名_recipe，7e7ad8f红复现其遮蔽Study现有_recipe.py；内部身份改为非Python标识符<recipe>，不新增用户文件名保留限制，标准注解反射与pickle回归验证可用。

进一步给合成Study加未被配方导入的torch.py，并在原checkpoint同时保存Tensor，暴露兼容reader按文件名重解释全局torch._utils：6项都红，RED_PICKLE_OWNERSHIP保留。已限定bare引用的已加载模块所有权与完整module spec，未被namespace接管的既有全局模块走标准Unpickler；不是把所有同名文件当pickle类来源。外部缺失类按原严格错误，不吞恢复失败。system_api桥只查询已加载recipe模块，不因普通System IO额外安装recipe import机制。

当前49项定向节点全通过（43源镜像unit、6实际Flow prepare/System/compare集成）：含legacy bundle、pieces/model.pt/meta.pt、payload.pt，自定义类和Tensor、搬到不同Study根后的类型归属、只读compare reader、helper内部晚导入/包相对导入、相同Study basename不同项目、重复加载模块键不逐Run增长、失败替换保全旧module、注解反射/pickle、双线程及原错退出。源码尚未提交生产新修复；不是完整core/Flow或CI验收，旧31项全绿不能替代本批。当前仅本地合批，不开新PR。

当前实施/验收补记（record§201）：§195的短暂清cache候选已由§198/200的模块生命周期与兼容reader方案替代；当前core1964 passed/1既有平台skip、CPU Flow279 passed，新51节点全部执行/通过。只4个既有源码文件，生产写入/数学门/布局原字节核对保持。这里只是本机验收，尚无本批PR/最终head/实际devCI，不表示163/164已关闭或已发布，也不等待人工设计批准。


## 202. B121 / #166：动态导入继续绑定所属Study（2026-10-16，PR165候选回归）

最终验收前复查确认：recipe在register内用importlib.import_module加载同目录helper，实际dev8480d79的独立子进程返回37，d385c410候选报ModuleNotFoundError。原导入隔离方案仅绑定Python import语句，漏掉标准动态入口；PR165不合并，保持同一分支补齐，不单独拆PR。最初探针驱动漏import sys、未调用库，单独保留且不算库失败。

方案：复用每Study私有包的导入归属判定，在recipe/helper导入importlib时提供仅当前私有代码持有的模块视图，import_module将绝对本地名映射到自己的私有空间；相对名和非本地名委托当前原生importlib.import_module。视图复制原模块属性，不修改sys.modules['importlib']或其函数，不改变其他调用方；importlib子模块继续使用原模块。Python import语句、标准库/已加载运行时优先级、外部导入hook、旧/迁移checkpoint读取合同不变。动态导入类与普通导入类使用同一稳定身份。

先提交10节点红回归：2种importlib取用方式×加载/注册/注册后/辅助模块4时机为8失败，2个非本地json对照通过。修复后运行当前全部新增集合与core/CPU Flow，再推同一个PR165重验最终head；此前d385c410的CI即使通过也不能替代新head。原始红/前后子进程/无效驱动证据在.tmp/audit121和.tmp/audit119（不随clone），portable回归已入库。不改变文件架构/产物布局，不等待设计审批。


2026-10-16本机实施：9166cbd在同一recipe文件补动态入口绑定；当前core1974 passed/1既有skip、CPU Flow279 passed，新增61全执行。该importlib视图仅属于私有代码执行环境，不替换全局模块身份；原生子模块与当前外部动态函数继续委托。最终PR165新head/实际devCI仍待验，不是等待设计批准，详record§202。


2026-10-16已实施并验收：B119/#163、B120/#164、B121/#166统一PR165，最终head3a74728与实际dev70a8750全部实际矩阵/聚合CI均成功；三Issue已关闭且已合主题本地/remote清理。动态入口、私有模块生命周期、旧/迁移checkpoint兼容的候选反例与方案修正均保留；这不是待审/待修状态，不改变第七节未实施能力。详record§203，后续三类复查仍先确认真实反例再同主题合批。


## 204. B122 / #167：Runner末条日志纳入同一失败留证边界（2026-10-16）

第一类新反例：最后logger.info('succeeded')位于run既有try/except之外。真实Logger的末条stdout或文件write被拒时，原异常虽传播，却绕过_log_failure/_write_failed_result；普通collect新Run无failed、旧Run残留succeeded，process旧成功按原规则应保留但也没有原错诊断。四个原生Runner/Logger合成探针确认stdout路径，正式12节点（stdout/file-write/正常×collect/process×新旧结果）先红8失败/4正常对照，4ac6883已提交。与B101启动日志处不同：原修复保留末条时机，本批保持该时机，只补末条异常范围。

最小方案：将末条info原语句缩进到现有try内、仍在全部阶段/Study hook之后；已有except原样记录并按当前最后phase调用_preserve_success。process旧成功仍逐字节保全；其他普通阶段仍failed，首错对象/既有诊断fallback/failed写二错规则不变。没有新catch、helper、phase名、依赖、结果字段、路径、文件或目录。日志本身失败仍对调用方失败，不吞错或伪造成功。只在同主题下一里程碑分支积累，先本机定向+core/CPU Flow，后续完整批次一个PR验最终head/实际dev；不为单个相邻bug立刻开PR。


2026-10-16本机已实施B122：761700f仅原末条日志缩进到既有try，原捕获/保全规则及全部其他源码原字节不变。Runner49/core1974 passed+1既有skip/CPU Flow291 passed，新增12全执行。#167仍OPEN，等待同主题完整里程碑最终head/实际devCI，不是等待设计批准；不单bug立即开PR。详record§205。


## 206. B123 / #168：中断日志尾行与下一条事件分隔（2026-10-16，同一失败留证批次）

真实Logger在无行尾的旧run.log后追加ERROR，将两条native header连成一行；event_lines将新错误读成旧flow内容，_note_from_log(errors_only=True)得到'-'。原始字节未丢但事件身份丢失，原生合成Run/Logger/index/readout确认，无训练或科学运行。#168后66d3526先提交14节点红6失败/8有效控制，后补单独CR有效行尾对照，完整16红仍6失败/10控制；ASCII/UTF8半字节、同一Logger/重新打开和真实只读回读均覆盖。

最小方案在现有Logger._emit：stdout输出仍原位置；以单独rb句柄检查可读文件最后一个字节，仅非空且既非LF也非CR时，在同一次原a文本write的record前加入一个newline。保留全部旧字节，不重写/截断/迁移文件。原append模式、编码/newline映射、权限/umask、write/flush/close及首错规则保持；有效空/LF/CRLF/CR日志原字节不变。二进制尾读不解码破损UTF8，不改变status/logs的解析或科学门。

尾读仅用于可读尾行修复：OSError时继续原append行为，不能因为新增read要求阻断原来可写但不可读的日志。此时无法保证中断尾行修复，明确限制与回归保护，不把可选探查失败当作写入成功证据；原write/flush/close失败依然严格。保留constructor不重开既有证据、peer、合法记录stdout/文件对应、其他调用方及Tracker恢复协议。不是新日志框架，不承诺任意多写者同Run事务。

与B122/#167同一codex/correctness-next-milestone本地主题积累，先红/设计/最小生产修复，再合批当前core/CPU Flow及最终head/实际devCI；不单独开一个bug PR，不等审批、不改文件架构/产物布局或运行GPU/测速。


B123补强与本机实施（2026-10-16）：3ad90d3在原Logger/已有B122 Runner上先补四项真实部分写入→Runner→原生readout联动，完整20红10失败/10控制，再97a1443实施原_emit可读尾行分隔。当前298相邻/core1988 passed+1既有skip/CPU Flow297 passed，32新全执行；仅两个现有生产文件最小改动，其他源码/格式/append权限及首错规则原字节保持。准备与B122统一完整里程碑PR；167/168仍OPEN，最终head/实际devCI待验，不是待设计审批。详record§207。


2026-10-16已实施且双侧验收：B122/#167、B123/#168统一PR169，最终head05341f8及实际devbef7de9所有实际矩阵/聚合CI均success；两Issue已关闭且已合主题本地/remote清理，详record§208。现行可读尾修复/原append-only限制、原错误优先级与布局合同继续有效，不是待审或待修项；后续真实同类反例按新Issue/红回归、完整主题里程碑推进。


## 209. B124 / #170：合法多行消息逐行保留Logger原生记录身份（2026-10-16，本地设计）

原生Logger.info的合法多行字符串仅第一行带完整header，后续[error]正文变成独立错误事件，errors_only读出一个从未由Logger.error发出的错误。REPRO使用真实Logger/index/logs/status，原始ZIP/hash/log保留.tmp/audit124（不随clone）；事件闭集与有效Run身份仍按既有合同，不把无效event或Run配置扩展成支持能力。

先开#170，8ca7187提交14项正式红回归11失败/3正常对照：info/warning/error、exception多行上下文/异常值、report extra六条真实调用路径，加CRLF与Unicode行分隔；原生Runner collect→Logger→logs/status三条集成确认事件不能被正文改名。普通/空消息和原f-string对象只格式化一次是兼容对照。均合成CPU/IO，无训练/GPU/数据或性能运行。

方案沿已有exception traceback逐行_emit规则：只在原_emit外加一层消息行循环，使用原f-string转换一次后splitlines，空消息保留一次原记录；每行复用原完整emit主体。正常单行记录字节/事件/级别/Run身份/编码/newline/权限、末条失败边界、可读尾行分隔及readable限制、write/flush/close首错规则均保持。多行内容不压平/转义、不通过修改只读parser猜事件，逐行保留文本及原拥有事件/级别；失败仍传播，不吞消息或放宽科学门。

不新增helper/模块/文件/schema/配置或磁盘位置，不改变Tracker/checkpoint与各Study方案。代码只做一层循环及既有主体机械缩进，精确字节证明除缩进外主体不变。当前先本地原分支积累字段边界主题，不立即为单bug开PR；本机定向和完整core/CPU Flow后，后续完整里程碑仍须最终head/实际dev全部实际CI再验收，不等待设计批准。


## 210. B125 / #171：可选activity在两个展示点共用单行投影（2026-10-16，本地设计）

合法非空Unicode phase及字符串detail包含换行/制表符时，announce原样打印成额外行列，status同样在权威summary/header前输出多行提示。真实announce/read_activity/index/CLI探针已复现：输入mapping合法、统计仍planned=0、原activity字节未改，错误仅在可选提示的显示身份，不称为Run结果损坏。原始REPRO/log/ZIP/hash在.tmp/audit125（不随clone）。

#171后c2dfbdb先提交18红16失败/2正常对照：phase/detail×LF/CRLF/Unicode行分隔/tab的producer8项，status的phase/detail×LF/tab×缺index/正常peer8项，以及正常Unicode/空detail2控制；原mapping、peer与全部输入字节/目录清单锁定。

最小方案在现有activity.py内加私有_format_activity，仅将已拼接提示的splitlines用空格拼回、tab换空格；announce与CLI两输出点复用。不能反向import readout（现有设计第四节），不搬_fmt到新模块；此helper只服务当前两个重复显示点，不是新框架/公开schema。read_activity及announce持久化主体原字节保持，自定义phase/Unicode/None或缺省detail规则、正常行空格和stdout时机保持。CLI继续原detail-or-empty及rstrip约定，producer正常空detail继续原尾空格。

提示只做显示投影，不改存储值、不拒绝合法字符串、不增加phase枚举/ownership/TTL协议；有效hint缺index仍exit0，无hint缺index仍exit2，mode/正式table及peer不变。与B124/#170作为同一字段边界里程碑在原分支本地积累，不单项PR。先定向再当前core/CPU Flow；后续完整里程碑仍须最终head/实际dev全实际CI，Issue未验收不关，无需等待设计批准。


## 211. B126 / #172：原生日志按已知Run身份定位真正event字段（2026-10-17，本地设计）

只读两函数在整行search所选tag，会把有效Run名字或正文中的[error]当事件：真实warning/split提示替换原错误note，合法单行Run目录name [error] Traceback marker使原生ERROR/FLOW整行被误当traceback过滤。真实Logger/Tracker/index/result/status/logs探针均复现，原始证据.tmp/audit126不随clone。此问题在单行消息也存在，独立于B124；没有扩展无效mode/event或多行log路径。

#172后4fcca9c先提交28项红回归20失败/8所选事件控制：普通/空格/含tag的合法Run组件，默认与自定义受限log路径，native warn/split/flow/error加metric/time/ckpt/resume；原生Runner/peer、错误note、默认独立note、过滤政策与全部输入字节锁定。

最小方案在现有status.py共享_event_match：识别原生时间/级别头后，以当前计划run_dir或原生'-'身份的完整字面前缀找到实际event起点，所选_EVENT只在该处match；已确认原生owner而event不在原集合时返回None，不能再从payload寻找。未知/legacy行继续原search，不猜旧文本、不重写文件。logs复用此house helper；status列表把已有run_dir传给_note_from_log，自定义log无需路径猜测；独立默认note可按固定runs/id/assets/logs路径字面取得上下文，缺上下文沿原兼容回退。

原选择集合、traceback抑制、timestamp/sub-microsecond排序、path guard、严格index/config/result/数值门与行输出原字节保持；不新增模块/文件/schema或改变Run目录/日志格式，不扩大为任意坏文本认证器。仅两个既有readout文件，先红后改，再与B124/B125统一当前本机core/Flow及后续完整里程碑最终head/实际devCI；不单bug PR、不等待设计审批。


## 212. B127 / #173：traceback抑制只匹配标准行形态（2026-10-17，本地设计）

两reader的startswith('Traceback')/startswith('File ')会丢失独立业务ERROR，例如File cache missing、File "data.csv" is missing、Traceback writing failed。原生Logger/index/result/status/logs五探针前三普通错误均note为'-'/日志无行，标准Traceback头和File帧两控制同样过滤但本来应如此。原始ZIP/hash/REPRO在.tmp/audit127不随clone；发生在B126事件字段修复后，是独立的正文分类过宽，不改原生owner匹配。

#173后正式18红12失败/6控制：六种普通或近似File/Traceback消息×native/legacy；标准Traceback头、带/不带函数名的quoted File/数字line三形态×2控制。原生Runner/peer与所有输入字节/目录清单锁定，非科学运行。

最小方案：在既有status.py共用_traceback_detail，只将完全相同的标准Traceback头或File "...", line 数字（可带, in 函数）的整行识别为细节；logs复用此helper。其他含File/Traceback首词的正文仍是原error记录。保留原选择事件、native/legacy匹配、时间排序、文件/只读边界与其他逻辑，不做traceback状态重建、不增加结构化格式或重写旧日志。与标准细节形态完全相同的非结构化文本仍按原显示策略略过，不承诺无法辨别的来源归属。

仅两个现有readout文件，把共同规则收敛一次而非在两个入口散改；先红后最小修，再与B124/B125/B126统一当前core/CPU Flow和完整里程碑最终head/实际devCI，未验收四Issue不关闭，不单bug PR、不等待设计批准。不改schema/布局、生产者或数值/科学门，不测速/跑GPU/Study。


2026-10-17本机已实施B124–B127：38d1e09/1a4cce4/62d0f47/a94622d在五个既有生产文件完成逐行身份/提示投影/native字段/标准细节规则；相邻420/core2009 passed+1既有skip/CPU Flow354 passed，78新全执行。原legacy/日志字节不重写，未知归属不猜，完全同形的细节文本仍按既有政策；未改变科学门或文件/产物布局。四Issue待完整里程碑最终head/实际devCI，不是待设计批准。字段展示点清单及原字节核对见record§213，不逐bug拆PR。


### B128 / Issue175：空共享锁初始化须在 OS 所有权内（2026-10-17，实施方案）

PR174原最终head85826fe的Windows3.10 core在既有双进程测试失败：shared.cache_lock第72行初始化flush抛PermissionError。两个进程可同时看到空.rpipe.lock，随后一方已取得byte0锁，另一方仍在锁前写/flush，绕过竞争重试和TimeoutError。真实空文件持锁子进程可确定性复现；不是Logger/readout改动，也不能用重跑抹去。

最小修复：移动既有三行空文件初始化至OS锁成功且release_lock已登记后、yield前。仍用原a+b句柄与同一inode，Windows可锁EOF后的字节；不换锁文件、不补全新协议、不改变deadline/timeout、线程重入、异常优先级或进程退出释放合同。初始化自身失败时先按既有ExitStack释放锁再关闭句柄，原异常保留。零预算和有限预算等待者在未获锁时不写任何字节。

回归使用原生msvcrt/fcntl子进程持有空锁，验证竞争超时且空字节不变、释放后正常初始化、重入、inode和再获取。Windows另开读取句柄同样受本进程字节锁限制，持锁断言只stat，释放后再读取；错误探针留.tmp，不当库失败。先红5822a41/c7eef67两项均在真实锁前flush失败，再最小生产修。当前PR174合批处理并重新完整验收；旧失败和被跳过Flow原样保留。


B128实施状态：f5b3591已按上文移动原三行初始化，无其他shared代码变化；两项真实竞争与四项初始化失败/取消清理控制均通过。相邻123/core2015+1既有skip/CPU Flow354全绿，PR174更新后等待新最终head和实际dev全部实际CI。原85826fe实际Windows3.10失败不撤回，见record§214–215。


PR174验收状态（2026-10-17）：B124–B128均已实施并合dev9d78fb4，最终head5dab50a及实际dev所有实际矩阵/聚合成功；五Issue170–173/175已验收关闭，主题本地/remote清理。上文“待CI”保留为当时历史，勿作为未实施/待批准事项。原Windows失败不撤回，完整依据record§216。下一B129/#176是新确认的失败元数据隔离缺陷，尚未实施，不把其方案与本批验收混为一事。


### B129 / Issue176：失败回写的可选shared元数据隔离（2026-10-17，实施方案）

实际dev9d78fb4上原生Runner复现十场：坏YAML、非mapping、坏shared/根、解码失败使原始异常已被捕获/记log后，_write_failed_result为补paths.shared重新解析同一坏声明，整体best-effort块提前退出。fresh无failed result，旧success也可能保留；paths.setdefault即使已有shared仍eager求值。c76e8fd先提交36项红24失败/12有效控制，补充read IO拒绝及fresh/旧success/已知draft路径四状态，验证原异常、peer与声明字节。

方案只在Runner失败写回局部：已有shared键直接保留；缺键时尝试取得现有layout.shared_dir，若Exception则略过此可选元数据，继续原Run/config/assets/result路径与failed/error写入。使用现有失败边界的Exception范围，不吞BaseException取消，不猜造shared根，不改Result schema或正常write.run严格shared求值。成功/有效配置输出保持，原异常同对象向上传播；实际result写入失败仍按既有best-effort静默，冻结初始拒绝与process旧成功保全完全不改。

影响仅FlowRunner._write_failed_result一个既有调用点；已查正常write.run和shared_paths调用方及readout消费者，失败Result合同只要求status/error、已知路径可尽量补齐。单Run/peer状态与文件/产物布局保持，不引入新helper/配置/字段/依赖。先方案再最小实现，本地相关/完整CPU检查后在当前下一主题积累同类合同，完整里程碑再PR，不为几行文档另开PR。


### B130 / Issue177：launcher失败失效化不依赖共享布局建立（2026-10-17，实施方案）

与B129同类，schedule._fail_result先读原Result后，通过artifact_layout间接ensure_study_layout/shared_paths，坏声明阻断已知Run result路径的可写failed证据，旧success留存。原生helper和公开launch_jobs的六场隔离REPRO四失败/二有效控制；子进程仅stub CPU，没有任务/GPU运行。27d263d红27项18失败/9控制，再906b72b加严格发布错误控制后红29项19失败/10控制，原首场不覆盖。

最小方案：_fail_result已先调用_run_result，其validate_run_directory仍原字节执行；复用该函数既有Path(study_dir)/runs/run_id/result.json表达式，直接交给原write_result，不调用不相关的完整layout建立；删仅用于此处的artifact_layout import。Result旧body/metrics/paths仅status/error更新保持，原子发布/父目录建立仍由既有writer负责。所有rerun、依赖失效化、blocked与非零退出调用点统一经过原helper，不改启动/重试/console/env/训练屏障，不猜shared根或复制输入，不吞实际write_result错误。

这是失败留证与配置元数据独立的同批合同，和B129保持同一未合主题，完成完整本机与远端验收后统一PR；不改make/prepare正常共享布局创建、文件路径/schema或科学门。输入声明/peer/config/index字节保留，非法Run组件仍在_run_result前置拒绝。已有路径消费者不依赖失败失效化额外建立shared目录。


B129–B130本机实施状态（2026-10-17）：3743b59与1b6885c已按上文最小实施，合批272相邻/core2044 passed+1既有skip/CPU Flow390 passed，新65全执行，原独立红和源码字节证明保留；两Issue176/177仍OPEN，等待完整主题PR及最终head/实际dev所有实际CI。上文方案已实施，不再等待设计批准；真实存储不可写仍遵循原best-effort/严格writer边界，不承诺不可写介质必有结果。见record§219。


PR178验收状态（2026-10-17）：B129–B130已实施并正常合dev53ae142，最终head26131eb及实际dev全部实际CPU/安装包矩阵及聚合成功；176/177验收关闭，主题本地/remote清理。上文待CI保留为当时历史，勿当未实施/待审批。完整来源/hash/限制见record§220。下一B131/#179是新确认的Windows同文件别名原生event身份边界，尚未实施；不改变此前规范或重做已验收任务。


### B131 / Issue179：原生event所有者采用平台组件身份语义（2026-10-17，实施方案）

接受dev53ae142上Windows原生samefile/log_path均确认有效Run别名，仅Owner→owner等大小写拼写差异导致_event_match精确字符串前缀不命中而回退；正文/Run名tag抢占，split误进logs，list_runs与standalone note不一致。两别名失败/两原拼写控制留.tmp/audit131；1cfad38先提交44项红17失败/27控制（12单测+32原生集成）。集成含正常/含tag名称、原拼写/别名、默认/custom日志、split/warn/flow/error、peer及全部输入字节/目录清单。

最小方案只改status.py共用_event_match一处条件：已知identity非None时，用既有Path对record相同长度的所有者组件作平台比较，并仍要求紧邻的原' ['边界；事件起点/正则/选择集合/返回偏移与原legacy搜索回退保持。Path组件比较只做文本路径语义，不resolve/stat/samefile，不给每行增加文件系统I/O；Windows匹配同文件大小写别名，POSIX大小写不同组件保持不同。run_dir/producer/log字节和磁盘路径不规范化，不扩大mode、未知外来legacy身份或更改日志schema。

两个既有消费者_note_from_log与logs.event_lines共用此helper，无其他调用者需改；原log_path的所有权/路径守卫、readonly模式、排序与traceback政策全部原字节保持。12单测显式模拟PureWindowsPath/PurePosixPath语义，不能当POSIX实机；32集成在Windows验证实际别名，在POSIX分支创建大小写不同实体并验证foreign日志不被借用，没有新增平台skip。实际POSIX证据须后续CI；先本条方案再最小实现，同主题本地积累后完整里程碑PR。


B131本机实施状态（2026-10-17）：86f6a86已按上文单处条件替换实施，450相邻/core2056 passed+1既有skip/CPU Flow422 passed，44新全执行。Windows原生别名与两平台组件语义已本机覆盖，POSIX原生实体隔离尚待后续CI，不能提前验收。#179仍OPEN，本地读取身份主题继续积累、未开PR；上文方案已实施，无待设计批准。原始与原字节证明见record§222及本机.tmp/audit131，不随clone；未知legacy回退、真实不可用存储/任意外来身份不增加保证。


### B132 / Issue180：报告Run ID代码跨度保留合法反引号（2026-10-17，实施方案）

现有Run目录校验允许反引号，NUMBERS.md却固定单反引号包ID，真实CommonMark解析使合法含反引号ID丢字符或没有ID code span。原生render_numbers五场3失败/2正常控制，原输入字节/90.0000/编码log href不变；先Issue180再a1e2f97红24项18失败/6控制，覆盖9类特殊ID/3正常ID×只读渲染/原子发布，无新增测试依赖。

依据[CommonMark0.31.2 §6.1](https://spec.commonmark.org/0.31.2/#code-spans)：代码跨度分隔符长度须匹配，边缘反引号可用空格分隔且规范会移去一对padding；代码内反斜线不作为转义。实际阅读限该节及相关示例，未引入整套富文本策略。已安装markdown-it-py仅本机解析探针，不加到project/dev依赖，portable测试固定协议输出与原数值/链接/peer/输入字节。

最小方案仅在原report.render_numbers现有Run行循环内：先用原_markdown_cell得到原显示文本，从单反引号开始延长分隔符直到不出现在正文；含反引号时添加一对必要padding，普通单反引号定界保持原字节。仍写入原id列，不新增helper/import/配置/字段或渲染框架；原空白投影、Markdown其他单元格、_log_links/URL编码、计划绑定、统计/精度、header/列/路径及write_numbers原子写不变。两现有调用者render/write与CLI继续原路径；不能拒绝合法ID掩盖问题。

与B131平台event身份同一只读展示身份主题合批，先本方案再最小实现；修后实际Markdown解析复核和相邻/core/CPU Flow，最终head/实际dev全部实际CI后再关闭。科学输入/门限、文件架构与产物布局不变。


B131–B132本机实施状态（2026-10-17）：86f6a86与acdd3e7按两节最小方案实施；624相邻/core2080 passed+1既有skip/CPU Flow422 passed，68新全部执行。实际CommonMark解析12类ID/peer及旧renderer字节比对通过；parser仅本机额外工具，未入依赖。当前两个Issue179/180仍OPEN，完整主题准备一个PR，最终head/实际dev实际矩阵及POSIX实体分支尚待，未提前验收。方案均已实施不待设计批准，原始/源码字节证明见record§224及本机.tmp/audit131/132，不随clone。


PR181实际验收状态（2026-10-17）：B131–B132已正常合dev593385f，最终head7e231ca与实际dev全部实际CPU/安装包矩阵和聚合成功，原生Windows/Ubuntu分支已执行；179/180验收关闭，主题本地/remote清理并完成基线161后五PR周期盘点。新基线181计数0/remote47，详record§225。上文待CI是当时历史，方案已实施不待审。下一B133/#182与B134/#183是新确认的计划校验缺陷，尚未实施，不混为已验收任务。


### B133 / #182、B134 / #183：启动计划条目与原生 Run 所有权统一校验（2026-10-17，实施方案）

现有 load_launch_plan 仅在显式 batches 非空时进入 wait_groups，非 object 条目被后续过滤丢弃，缺失 ID 的行为随 include_done 改变。原生 CLI 复现返回 0 而 process.complete=false/pending1，未触发重建。Windows Owner/owner 同一实体可绕过字符串重复检查，两个 Popen 均先于等待发生；仅用替身，不声称已造成实际损坏。原始证据在本机 .tmp/audit133/134，不随 clone。

复用既有 wait_groups 作为共同信任边界：每个声明 job 必须是 dict，run_id 使用已有 validate_run_directory 原类型校验；真实生产者始终写字符串，数字/空/缺失/不安全 ID 属坏计划，不补字符串转换。重复所有权使用 Path(run_id) 的当前平台组件比较，Windows 大小写别名拒绝，POSIX 不同大小写实体仍独立。不增加文件系统探测、全局锁、junction/短名解析或新 helper。原 batches 全字段多重集匹配、顺序/资源/训练评估屏障不变。

缓存读取在 include_done/readiness 前无条件调用此边界，仅原 KeyError/TypeError/ValueError 返回 None 请求重建；不吞 RuntimeError 或调用参数错误，不修改或部分修复缓存。write_jobs_json 无条件复用边界，在旧 jobs.json 发布前拒绝坏计划；render_bash、launch_jobs 已进入边界，移除启动处重复的字符串校验。合法空计划、缺可选字段的 legacy 计划和成功过滤保持。脚本/配置/结果/peer 原字节拒绝前后不变；不改空队列 CLI 退出策略、schema、文件架构/磁盘布局、producer 或科学门。

先 Issue182/183，再 e86727a 红39项28失败/11控制与 8b99bc1 红20项12失败/8控制，之后本方案，再最小实现。原生 Windows 别名/字面重复/独立 Run、显式/隐式批次与缓存/启动/脚本路径、POSIX 独立大小写实体均由同一测试在实际 CI 平台执行，无新增 skip。两项同主题合批，一个完整里程碑 PR→dev；最终 head/实际 dev 全部实际 CI 后验收，不待设计审批。


B133–B134本机已实施（2026-10-17）：6349765按本节统一边界完成，230相邻/core2137 passed+1既有skip/Flow424 passed，新59全执行；POSIX实体分支及双侧实际CI待PR，不提前验收。详record§227，原始坏缓存与重复派发证据保留。方案已实施，不待设计批准。


### B135 / #185：异常 wait 分组保全本次已启动子进程的收尾（2026-10-17，实施方案）

PR184已正常合dev28f3dde，实际devCI尚待；本项在其后唯一未合codex/launch-failure-ownership本地推进，不追加到已合PR。当前_run_groups先启动整组再等待：后续spawn、输出、env、失效写入或wait/退出诊断抛错时，已启动peer不再wait。真实临时CPU子进程只sleep2秒，第二spawn注入OSError后launcher已返回异常而child仍存活，探针随后等其自然退出0；不声称实际研究损坏。六失败入口替身反例同根因，合为一B，不另拆Issue/PR。

先Issue185，再f9ce20d红27项25失败/2正常批次控制通过（26unit+1原生CLI integration），0error/skip。首次RED与加强secondary-wait分支的RED2均留存，未改生产代码。首gh search参数被CLI错误解析，保留失败，再用71条全状态Issue清单去重成功；不是库或CI失败。证据.tmp/audit135不随clone，便携回归已提交。

复用_run_groups原有procs列表与等待循环：同组启动/等待放入同一try；记录已成功wait数量（在退出诊断前递增），异常时只对尚未成功wait的本次owned handles依次尝试wait，再原样raise最初BaseException。secondary wait异常不遮蔽primary且仍尝试其余peer；已成功wait不重复，正常路径与codes/配对/重试/波次顺序不变。取消也不遗弃已知owned child；等待自然退出，不kill/suspend任何进程，不增全局锁/进程管理框架/timeout配置或磁盘产物。

若OS wait本身持续不可用，仅能保证尝试所有已知句柄，不能声称子进程已终结；最初异常仍严格向上传播。本项不承诺强制取消、解决hang子进程或跨独立launch互斥。无schema/文件架构/产物布局、科学门或GPU/性能变化。按本地主题commit积累完整失败所有权里程碑，再考虑单PR→dev，遵守少量PR频率；不立即为单B另开PR，不待设计审批。


B135方案修正（2026-10-17，先补回归再实施）：首个仅补wait候选257相邻通过，但审查指出其遗漏既有非零退出→成功结果纠正。232ff08新增红1/1：已启动peer在wait中写succeeded后返回7，后续spawn原错传播而结果仍succeeded；这是本地候选不完整，未提交生产/未推送/未验收，不另立B或PR。WAIT_ONLY_CANDIDATE.py、首GREEN与EVIDENCE_RED保留于.tmp/audit135，不能被后绿覆盖。

改为继续复用同一原wait/非零结果纠正循环，不另造wait-only收尾分支：捕获组启动最初BaseException后仍进入原等待循环；每个wait/结果纠正/退出诊断也保留首个异常并继续其余已启动peer，循环结束再raise原异常对象。正常等待返回/结果处理不变，成功wait仅一次；OS wait持续拒绝仍只有尝试保证。两处try与一个primary变量位于原_run_groups，原spawn/非零纠正正文不丢失，无新helper/import/进程杀停/全局锁/超时/布局。先记录本修正，再替换未提交候选；此前成功wait计数方案属于被拒候选，不作为最终已实施描述。


B135本机实施状态（2026-10-17）：最终ba04ae3按修正方案复用原循环实施，258相邻/core2164 passed+1既有skip/Flow425 passed，新28全执行。仅wait候选及补红保留，未冒充验收；185仍OPEN且未推送/开PR，按少量PR规则本地积累。PR184已双侧实际CI验收清理，详record§229–230；无待设计批准。


B135二次复查修正（2026-10-17，仍未推送）：ba04ae3对启动错误已收尾peer，却在wait自身首次短暂失败时直接跳到下一peer，没有给该句柄恢复等待机会。真实仅sleep CPU子进程两探针（首wait KeyboardInterrupt/OSError、下一wait可成功）均保留首错但只wait1次、返回时child仍存活；随后探针等自然退出0。1215806先提交加强红28项8失败/20控制，含4原生CLI（首错两类×真实child exit0/7）及原等待类增强；原先28新已绿证据只属该不完整候选，不替代本边界。

保留同一B135/#185范围及分支，不新立B/PR。原每child的wait与int code转换单独try；首次wait抛错时仍保存最初异常对象，并对同一已知owned句柄追加一次恢复wait。恢复成功继续原非零退出纠正/诊断；恢复仍失败则保留首错并尝试其余peer。正常wait只1次、完成后诊断异常不重复wait，不增加公开重试配置或全局进程策略；该一次恢复是异常收尾而非重跑训练。不能保证持续OS wait拒绝或真实hang已完成，源码标出边界。原启动body、结果纠正、codes/资源/波次/既有训练失败重试、布局/文件架构均保持。先本修正再最小实现，后重新完整检查；不拿旧绿冒称完成。


### B136 / #186：sibling身份与调度依赖共用当前平台Run组件（2026-10-17，实施方案）

原生Windows Eval/eval同实体，canonical能找到Train、旧成功在parent failed时False、能选best路径；alias却parents空/dependency None/readiness True/checkpoint None。直接alias eval在parent failed时Popen替身被调用。只读各文件字节不变，无真实子进程/权重加载/训练/GPU。72条全状态Issue去重，区别于已修183重复计划及179日志归属；初探针漏failed.error被严格writer拒绝，保留driver拒绝而不当库失败。原始ZIP/REPRO在.tmp/audit136不随clone。

先Issue186再7ae8a6d红30项10失败/20正常控制，0error/skip，均已执行。覆盖sibling IDs/依赖/checkpoint选择、failed/newer/valid parent×include_done、eval拒绝、train别名rerun失效及重试耗尽屏障；原生POSIX实际创建不同大小写独立Run作为有效控制，不新增平台skip。本机Windows证明别名反例，POSIX结论仍待实际CI。

最小方案不改产物/metadata字符串：resume.sibling_train_ids原同Run判断用Path(rid)==Path(run_id)；现有schedule.dependencies临时mapping以Path(run_id)为key，三处查询使用Path，遍历时转回原字符串传既有安全validator/结果写与诊断，train rerun比较也按Path。父ID仍用index原字符串，不改seed/factor匹配、current-index范围、外部/本地显式checkpoint独立控制、mtime新鲜度或训练评估波次。无新helper/import/schema/全局lower-case/任意symlink或短名探测，不扩大跨调用互斥。

全部调用者已核：sibling_train_checkpoint和sibling_train_dependency共用ID选择；run_succeeded/plan/cache/CLI经原依赖入口，launcher依赖lookup/旧eval失效/失败重试过滤用同平台key。与B135异常子进程所有权同一启动正确性里程碑、本地同分支累积，再完整验证；不另开一个bug一个PR。先本方案再最小修，不待设计批准。


B135–B136本机实施状态（2026-10-17）：0df9943/3594c1e按最后修正方案实施，295相邻/core2194 passed+1既有skip/Flow429 passed，新62全执行；先前候选失败及原生首wait反例均保留。186不改变metadata/布局，只在原两文件统一平台组件身份；185/186仍OPEN、未推送/未开PR，当天4PR后继续本地积累，完整主题后统一PR。双侧实际CI及POSIX实体分支尚待，不提前验收。详record§231–233，无待设计批准。


### B137 / #188：可选Git元数据不因文件名解码失败中断（2026-10-17，实施方案）

隔离真实Git仓库仅tracked ASCII输入与untracked研究-λ.txt，repo-local core.quotepath=false；本机cp65001/UTF8mode0正常head/dirty=true。只将真实subprocess文本decoder选为ASCII模拟非UTF8父locale时，Windows reader thread抛UnicodeDecodeError、stdout变None、公开capture_git再AttributeError。没有声称本机默认locale失败；73条Issue去重，独立于既有status非零退出码保全。首driver只捕UnicodeDecodeError漏真实WindowsAttributeError，已保留DRIVER_CAPTURE_REJECTED，未重建/删除仓库。原始输出bytes/异常thread stderr/正常控制/ZIP在.tmp/audit137不随clone，零GPU/训练/测速/正式Study。

先Issue188再dbab1ff红10项2失败/8控制，0error/skip；真实Git×ASCII/UTF8 decoder×ASCII/Unicode文件名×capture/publish八项，加两种stage程序RuntimeError严格控制。发布控制stub设备环境采集，实际source/plan/Git/atomic provenance链仍执行；不probe GPU。输入与peer字节不变，坏旧provenance仅fixture用于验证成功发布。

最小方案仅capture_git的两个现有subprocess.run加errors='replace'，继续text=True与原默认locale，不改encoding、命令、cwd、10/30秒timeout、退出码检查/捕获异常/返回schema。只保存ASCII HEAD和dirty布尔，不需要返回文件名；不可解码文件名字节替换仍是非空status，不能误报clean；失败命令/timeout仍原{}。这从源头避免reader-thread失败/None，不捕AttributeError或吞RuntimeError，不加wrapper/配置/依赖/架构/路径，不改冻结digest或source/plan范围，不写回旧provenance。原stdin/输出不对外传文件名，合法文本/locale正常路径结果不变。

先本方案再两处最小参数修、完整本地检查。PR187已正常合devcd0a8a0、实际devCI尚待；本项位于其后唯一未合codex/optional-metadata-correctness，旧PR/head不追加，下一可选元数据主题本地积累再里程碑PR，不马上单B开PR。无设计批准阻塞，不合main/tag/删数据。


### B138 / #189：国内下载入口（2026-10-17，已授权实施方案）

03:16用户新增明确要求，区别于旧版仅hf-mirror的合同。Git跟踪Python没有现成HF模型或datasets下载实现，只有注册builder；不凭空新增模型/数据类型。现有origin.py集中实现download_model与load_hf_dataset，并从原model_api/data_api暴露供注册builder复用。ModelFactory和DataFactory在builder之前应用显式origin，原builder签名、config对象、缓存锁和目录不变。

模型入口默认domestic：原样repo ID及revision先交ModelScope snapshot_download，失败（包括未安装可选SDK）保留其异常上下文后仅向显式endpoint=https://hf-mirror.com的HF snapshot_download兜底；两边失败报告RuntimeError并保留异常链，不转海外、不重试循环。cache_dir必须由调用方传原shared/model；本地已存在路径直接返回，不下载/搬迁。ModelScope和HF仓库名不相同时由builder明确选正确ID，不推断映射或静默替换模型。SDK可选，现有纯torch安装不强加依赖；使用优先能力需安装modelscope，HF入口沿可选hf依赖。

数据入口在导入datasets之前设HF_ENDPOINT，并检查已导入datasets.config/huggingface_hub.constants：若缓存端点仍指海外则明确报错，要求新worker在导入前选domestic，不reload/猴补第三方全局常量。模型HF兜底另显式endpoint，防已有import忽略环境。torchvision沿原CIFAR10/100/MNIST国内镜像和校验，SVHN/FashionMNIST无登记镜像继续拒绝，绝不悄悄转官方。foreign旧显式接口兼容保留；本会话实际下载禁用，模板后续使用domestic，历史Run/结果/快照原样保全。外部baseline自定义HTTP/第三方脚本仍需其作者逐项审地址，不声称环境变量是网络防火墙。

先Issue189→03bb7ce红回归：7新节点均失败（builder时序2、ModelScope优先/镜像兜底/双失败3、datasets路由/旧import拒绝2），5旧控制通过，原始XML/log在.tmp/audit138不随clone。无真实下载/训练/GPU。与本分支#188合批，完整本机和双侧实际CI之后一个里程碑验收；不改layout、持久化格式或正式实验数字。

依据：ModelScope官方snapshot_download源码 https://github.com/modelscope/modelscope/blob/master/modelscope/hub/snapshot_download.py （实际阅读签名/cache_dir/revision）；HF datasets官方config.py https://github.com/huggingface/datasets/blob/main/src/datasets/config.py （实际阅读HF_ENDPOINT/HUB_DATASETS_URL导入期定义）。SDK当前主分支阅读不等于安装版本实测，回归使用下载替身，真实服务可用性不由本轮保证。


B138完整core首候选修正：7failed/2209passed/1既有skip，通用apply_model_origin检查已导入海外端点过宽，阻挡纯torch/CPU。现仅在实际load_hf_dataset下载边界导入前/后检查；模型fallback使用显式endpoint，不受旧HF默认常量影响。所有factory仍在builder前设置环境，不假装能约束任意自定义HTTP。新增测试以独立模块状态控制缓存端点；第一次替身不完整导致真实datasets初始化失败亦保留，修正后67定向通过，重新完整core执行中。上述限制替代原方案“通用origin拒绝”表述，不吞实际HF入口违规。

B138补强：HF_ENDPOINT不会重写load_dataset显式path/data_files URL。a597b54先红两项均实际到达禁止下载替身，现进入datasets前递归核对这两项（split字典/列表），仅允许https://hf-mirror.com和hf://标识，其他远程源明确拒绝要求本地准备；本地路径/Hub ID不改写。不替任意第三方代码审网络。无真实请求。


B137–B138已实施并实际验收（2026-10-17）：PR190 finalhead6f66f37及actualdev e31fbf5全部实际CPU/包矩阵和聚合成功，188/189关闭，旧主题本地/remote正常清理。26新节点全执行，本机core2220passed+1既有skip/Flow429passed；真实datasets离线链亦核对，真实下载/ModelScope服务未测。原候选过宽guard/URL绕过失败保留，最终检查只限实际HF数据入口，不误阻纯torch。上文实施/待CI为各时点记录，无待批准事项；详record§239，布局/科学门不变。


### B139 / #191：只读展示标签的UTF-8边界（2026-10-17，实施方案）

实际dev e31fbf5相邻复查：ASCII JSON中转义的未配对代理字符可被现有json.loads正常解析；相同合法ASCII Run ID和数值，普通metric label能写Numbers且含peer，换一个转义high-surrogate label则write_numbers真实UnicodeEncodeError，整份新表不发布。旧表、index/process字节仍保全，atomic writer严格编码拒绝是正确的，不改其合同。75条全状态Issue去重，既有111/B093修测试证据JSON，非本次展示消费者；本次191先登记，原始REPRO/ZIP在.tmp/audit139不随clone。

a55f38a先红36项18失败/18控制、0error/skip：Numbers factor-key/factor-value/metric-key十八项、status两类factor十二项、compare label六项。高/低/中间隔开的混合代理分别失败，ASCII/中文emoji/字面反斜杠均控制通过。Numbers实际UTF-8发布与peer两行、status/compare输出UTF-8编码与状态/peer、源JSON字节/临时清理均检查；不依赖Windows console的errors=replace把故障藏掉。没有改Run ID/路径、producer、schema或科学数字。

全部caller已核：report._markdown_cell/_factors、标题及指标/Run展示；status.format_status的study及各单元格；compare.format_compare的标签，均复用report._text_cell。最小方案只在原_text_cell完成现有换行/tab归一后，加UTF-8 encode(errors=backslashreplace)/decode roundtrip：合法Unicode/ASCII/字面反斜杠保持，只有无效代理code unit显示为可打印反斜杠诊断。既有Markdown反斜杠/管道转义、Run fence、URI guard、计数/数值格式、sort和行顺序原样。不修写回源数据，不在atomic writer加errors宽容，不吞UnicodeEncodeError或任意程序错误；严格write_result无效Unicode仍照原测试拒绝/保旧目标。此为显示文本容错，不声称诊断转义是可逆的科学字段或规范身份编码。

采用现有B093测试证据的backslashreplace策略，不加helper/依赖/配置/路径；只补同一显示边界的一行。参考RFC8259§8.2明确指出语法可容纳无法表示Unicode字符的孤立代理、互操作行为不可靠（https://www.rfc-editor.org/rfc/rfc8259#section-8.2，实际阅读此节）；本地json解析/UTF-8失败是独立实测。该规则只在展示消费端，不改变格式规范。先本方案再生产最小fix，完整本机检查后继续相关主题本地累积，一个完整里程碑PR；不单B立即开PR，不等待额外设计批准。新B140，无GPU/测速/下载/正式Study/main/tag/布局变更。


B139同类消费面补全（2026-10-17）：4926b81单处_text_cell已使808相邻通过，初core2256passed+1既有skip；继续沿共用readout发现实际compare_runs→write_compare仍把同一source metric key放入items.metrics.keys，派生JSON序列化后真实UTF-8写入失败，正常label/peer控制通过、旧目标/输入不变。已在191原body一次追加此边界，仍同一B139/同一分支，不另Issue或PR。c0dc401先6项红3失败/3控制（高/低/混合，ASCII/中文emoji/字面backslash控制），验证完整json.loads roundtrip等于原report，不将科学key替换成诊断文本。

补全方案只在write_compare现有json.dumps(...ensure_ascii=False)之后加UTF-8 backslashreplace encode/decode，再交原strict atomic_write_text。只转义无效code unit为JSON合法转义；有效Unicode/字面backslash原输出字节及json.loads后的原始key保持，现有finite清理/字段/数值/顺序/indent2/路径全不动。不复用_text_cell处理整份JSON（那会改换行/tab），不改generic Result/config/provenance encoder或writer的严格拒绝。原始36红及所有初轮绿/完整检查保留为partial-stage证据，最终两处消费修复需重新完整本机验证；继续本地积累，不单B立即PR。此补全取代最初“只需_text_cell一行即可覆盖所有readout发布”的范围判断，未放宽严格输入/身份或科学门。


B139可选activity消费面补全（2026-10-17）：继续按同类UTF-8输出扫描，_format_activity是未复用_text_cell的独立归一出口，caller仅CLI status提示与announce。原生main配真实strict UTF-8 TextIOWrapper、外包不带reconfigure的输出流，正常hint可打印peer/计数，JSON转义surrogate detail会在权威表之前真实UnicodeEncodeError；不声称默认Windows replace控制台同样崩溃，POSIX原生行为尚待CI。activity/index/result字节保持，ACTIVITY_REPRO/ACTIVITY_ORIGINAL.zip本机留存；仍一处191 body追加，无新Issue/PR。

0b061f9先红14项6失败/8控制：phase/detail各6（高/低/间隔混合失败，ASCII/中文emoji/字面backslash正常），另2个announce真实UnicodeEncodeError严格拒绝、旧activity保全控制。方案只给既有_format_activity归一返回加同样UTF-8 backslashreplace roundtrip，调用/导入/格式/字段/路径/类型校验不变；announce仍先strict JSON写入再print，不能借display fallback写入坏source。原子writer/Result/config/provenance严格合同不动，不捕输出流BrokenPipe等真实错误。两处readout阶段已完整2262core+1既有skip/429Flow保留为此补全前证据，最终三处源码要重新完整验收。先本方案再最小修，同B139继续合批，下一B140仍未用。


B139已实施并双侧验收（2026-10-17）：PR192 finalhead0ac238b/actualdev8d3e1b3全部实际CPU/包矩阵与聚合成功，191关闭、原正文保持/一次验收评论、旧主题本地+remote普通删除。56新全执行，本机878相邻/core2276passed+1既有skip/Flow429passed；scope限制和部分阶段失败照原记录保留。无待设计审批，详record§244。

### B140 / #193：Logger独立双输出与完整故障上下文（2026-10-17，实施方案）

在Logger未变的PR192基线上真实Runner→execute→Logger复现：OS管道reader在算法原异常前关闭，旧run.log只有79字节启动行、无primary/traceback；open-reader控制922字节且两者都有。闭stdout TextIOWrapper同样阻断可写磁盘；只读临时log文件时健康stdout也只有错误头、无完整traceback。原始算法异常同对象和failed result、peer均保持，临时mode已恢复。符合README终端同款必写run.log及flow失败traceback合同，区别于138调度非零纠正或170行身份。76全状态Issue去重，先193登记；原始REPRO/SINK_VARIANTS/ZIP在.tmp/audit140不随clone，无训练/模型/数据/GPU。

eba0d3a先红32项24失败/8控制、0error/skip：Logger18个真实OS管道/关闭流与正常控制，8个文件open/write/flush/close×多行/真实异常，1双IO首错/owned close，1非IO stdout程序错原样；另4真实Runner链覆盖normal/pipe/closed/file。复用stream_close_fault保已有descriptor关闭/异常优先级；file故障单测为可定位注入，原生只读文件有独立证据，不混称全部为真实存储故障。

所有caller已核：info/warning/error/report走_emit，exception先error头再各traceback行；execute/Runner以既有best-effort保护原phase异常，failure_logged去重保持。方案只改logger.py两个既有方法，不改backend/interface/字段/格式/时间/行序/路径或科学状态：

1. _emit保留原print→tail探查→append/write/flush/close顺序；在现有message逐行循环外记录首个已知IO/编码/closed-stream错误，在每行分别尝试stdout和文件两出口，任一已知失败不跳过另一个及剩余行；全部尝试后仍raise首个IO对象，不能把后续成功当质量通过。不增加每行重试/sleep，原单次append与flush次数正常路径不变。
2. 只在已知(OSError, ValueError)边界收集（UnicodeError属于ValueError）；非IO RuntimeError等程序故障以及BaseException取消按原方式直接传播。原handle.write/flush失败时close、close IO不遮首错/程序close优先的代码原样保留，不以捕获IO吞掉内部程序错。
3. exception在原error头及原traceback逐行_emit边界同样记录首个已知IO，继续尝试完整原始格式化traceback，最后严格raise首错；不伪造无__traceback__异常的stack，不改原格式化/事件/Run身份，普通完成无新增记录。
4. 不改execute.failure_logged或Runner旧成功/失败回写策略，不迁移logger选型，不碰tracker/checkpoint/sampler/RNG/恢复、发布或布局。无法写入的sink不保证落盘；只保证健康peer的尝试与已知首错保留，未知程序故障/取消不做延迟协议扩展。

先本方案再最小实现，复核原生三种反例、正常stdout/文件字节对应及既有close/tail/多行身份，再完整core/CPU Flow。当前唯一未合codex/logger-sink-evidence，本地按主题提交，完整类别里程碑后统一PR→dev；下一合入是基线181后约第5个，届时全面盘点。无设计/用户批准等待，新B141。


B140本机实施完成（2026-10-17）：2fa9f0a只改既有Logger两方法，ADJ257/core2304passed+1既有skip/CPU Flow433passed，32新全执行，原生pipe/closed/readonly三反例转绿；首错/取消/原close/execute去重合同保持。当前待整类里程碑双侧实际CI，193保持OPEN，非待设计批准。详record§246。


B140已实施并双侧验收（2026-10-17）：PR194 finalhead26dd6d2及actualdevf2c286d所有实际矩阵/聚合success，193已按授权关闭，旧主题本地+remote普通删除。无待审/待CI；单writer checkpoint/程序错/取消等原合同不扩大，详record§247。


### B141 / #195：独立raw准备入口的国内下载边界（2026-10-17，实施方案）

用户03:16国内下载规则也覆盖baseline/手动脚本。PR190/B138只修库factory及HF/ModelScope入口，独立studies/main_exp/flow/prepare_data.py仍硬编码ossci S3/toronto。77条全状态Issue去重、原Issue189范围核对后先195登记；调用实际prepare_mnist/cifar并在urllib传输前拦截，两个海外首请求确认。另用真实urllib重定向流程配合合成HTTPS响应确认国内起点302可继续发往海外；未发真实网络，不声称现有国内镜像实际这样重定向。源码ZIP/REPRO在.tmp/audit141不随clone，正式Study/历史manifest/归档原字节不动。

80009d7先红20项11失败/9有效控制、0error/skip：两默认路径、六越界authority、HTTPS/HTTP/FTP海外redirect；正常大小写/default443、同国内redirect、缓存/坏缓存、长度/MD5拒绝保partial、verify-only保原下载来源为控制。仅网络transport替身，urllib请求/redirect/哈希/文件发布实际执行，socket.connect禁止作为后备。

最小方案只改现有手动prepare_data.py，不改src/rpipe原有foreign显式兼容：MNIST_URL/CIFAR_URL复用domestic_endpoint已有登记镜像，归档/MD5及raw SHA256原样。一个私有URL校验点要求HTTPS、hostname属于这两个已登记endpoint、端口缺省或443、无userinfo；有效host大小写不误拒。校验仅在真实下载前（有效缓存继续不联网复用，即使旧来源字符串是海外），并用标准库HTTPRedirectHandler薄子类在redirect_request进入下一transport之前复用同一校验，覆盖HTTP/FTP重定向逃逸。使用本次download独立build_opener，不install全局opener，不加配置/依赖/重试/海外fallback。

保留既有60秒timeout、headers、Content-Length/MD5校验、.part证据、同目录发布、cache_lock、raw路径与manifest字段；拒绝时要求本地预备已校验raw，不换海外源。记录新实际国内URL；verify-only仍保留原downloads/reused_caches来源，不就地重写历史证据。国内镜像可用性/下载数据一致性尚未实测，不拿mock代替；源码冻结后实际复跑须新version，当前不运行正式Study/GPU/训练/测速。此变更只落实已授权下载合同，不扩展科学目标/布局。

完成该入口后继续同类/相关失败留证复查，本地按主题累积到完整里程碑再PR，不为这个脚本单开小PR。当前唯一未合codex/correctness-milestone-next，195待最终里程碑本机/双侧CI，下一B142；无设计审批等待。


### B142 / #196：手动raw复制类的首错与owned退出（2026-10-17，实施方案）

B141路由修9e2bcd2之后，继续同脚本复制失败边界：实际download流/gzip解压/tar提取配合真实.part部分写入，再在真实descriptor关闭后注入OSError，三条均把首个write OSError改成secondary close OSError。partial/peer/自有文件关闭虽保全，调用者首错身份丢失。是可定位I/O注入，不声称真实存储坏；源ZIP/REPRO在.tmp/audit142不随clone。原144仅四普通artifact/logger/tracker writer，142是锁退出；登记196收这组三binary raw helper，不重复旧Issue。

bce353a先红35项14失败/21控制、0error/skip：download/mnist/cifar各destination/source退出，以及cifar外层archive退出七边界×write-error/cancel/normal-close/programming-close/ok。复用stream_close_fault真实落3字节再拒绝，源用实际GzipFile/ExFileObject/TarFile与合成HTTP响应；全部应检查原异常对象、owned close一次、源archive/peer字节、partial及成功发布。normal archive close失败时raw已按原顺序发布，控制保持，不伪造回滚。

最小方案在同手动模块内放一个私有owned-stream上下文包装，因七个既有owned流边界重复此语义，标准contextlib.closing会同样遮首错，不够。使用标准ExitStack，先保留原manager.__enter__返回/失败语义，再注册原__exit__调用：仅在已有body异常且__exit__另抛OSError时保留原错；正常body的close失败继续传播，非IO程序close错误和原有__exit__抑制语义保持。保留LIFO退出、外层源关闭与目标打开失败的行为，不重试close、不删partial、不新增全局I/O框架/模块/依赖。

只包装download响应和partial、MNIST gzip和partial、CIFAR TarFile及extracted source/partial共七边界。digest/split_counts普通读取非本已复现copy范围，仍可继续相邻审计，不以本批声称所有读取已修。原60秒网络预算、国内初始/redirect guard、缓存/hash/发布顺序/manifest/布局/单writer合同保持。未知错误不得只藏__context__。本地先上述方案再修，再完整55新回归与相邻/核心/CPU Flow；此前B14120新节点的core2324+1既有skip/Flow433仅中间阶段证据。195/196同分支合批、不逐bug PR，无设计审批等待，下一B143。


B141–B142本机实施检查点（2026-10-17）：9e2bcd2/4954933在同一手动raw模块完成上述路由/七owned copy退出；55新全执行，最终166相邻+1既有skip/core2359passed+1既有skip/CPU Flow433passed。无设计等待，195/196仍待完整批次PR双侧CI；接续相邻严格读取验证，不扩展未测合同。详record§251；真实下载/服务/GPU/实验未执行。


B142同类严格读取补全（2026-10-17）：原digest及split_counts四个显式binary reader（MNIST images/labels、CIFAR batch/meta）同样close IO遮住原read/struct/pickle错误，归196原body一个追加区，保原文不另Issue/B编号。实际digest及精确读取AST块配真实文件/解码、close后注入OSError，五处均确认；不是完整科学数据验证。第一候选meta夹具误指input.bin，FileNotFoundError已拒绝；更正为batches.meta后五处重验，拒候选/log及Issue正文校验更正说明保留，不当库失败。

正式补红25项10失败/15控制、0error/skip：五读取边界×原IO/解码错、取消、正常close拒绝、程序close错误、成功。输入/peer字节、owned descriptor/close一次及真实解码控制均核对。沿已批准同一私有_owned_stream，仅给这五个既有with表达式加包装，不增helper/读fallback/宽容解码，复用原__enter__/__exit__语义及IO首错策略。完整显式binary stream共12边界统一；cache_lock沿原OS协议、隐式Path.read_text等标准库内部I/O不冒称全覆盖，既有字段/字节/数值校验与布局保持。此前4954933完整2359core+1/433Flow为copy七边界阶段，补后80新节点需重新完整验收。仍195/196同分支一个手动数据准备类别里程碑PR，无设计等待。


B141–B142完整类别本机验收（2026-10-17）：最终84389b0含12显式owned流与既有路由修，80新全执行，191相邻+1既有skip/core2384passed+1既有skip/CPU Flow433passed。所有原AST/hash/strict gate核对，三批red/拒候选保留；无设计等待，准备一个完整类别PR，195/196待双侧实际CI。细节与限制见record§253。


B141–B142已实施并双侧验收（2026-10-17）：PR197 finald824e14/actualdev e8cebcb所有实际矩阵/聚合success，195/196已验收关闭、旧分支本地+remote普通清理，详record§254。后续B143/198确认SDK redirect边界独立于手动urllib，尚在primary源码/兼容审计，待设计写明后先红/实施；不把PR197的完成说成所有SDK transfer已受guard。无设计审批等待。


### B143 / #198：SDK 实际传输约束（2026-10-17，红回归完成，实施中）

不是新的设计审批门。国内源优先级、公开函数签名、本地输入、revision/cache 身份、布局以及取消传播保持既有合同；ef3c500 先提交 4 个真实 SDK 重定向反例与 2 个国内/共享客户端控制，扩展取消控制后 7 项 4 failed/3 passed。生产代码尚未修改，不能把入口 endpoint、最终 URL 或仅模拟 snapshot_download 函数当成传输约束已生效。

实现边界：每次模型/数据下载及重定向的目标在传输前核验；响应元数据中选出的下载 URL 同样核验；来源未获国内许可时在发请求前失败并保留双源故障说明。复用 SDK 现有认证、代理、缓存与连接生命周期，不关闭或替换其他调用者的共享客户端。回调只清理本次拥有的对象，普通成功、失败、KeyboardInterrupt 与并发调用均须核验。HF 0.x 每线程 requests Session 与 1.x 共享 httpx Client 不能用同一个未经验证的全局 factory 替换方案；Xet/native、datasets/fsspec/aiohttp 和 streaming 延迟读取另按实际调用路径验证，不能把 Python HTTP hook 自动视为 native 约束。不可保证国内传输的 SDK 路径须明确报错，不静默转海外，不伪造可用性；能力边界写入使用文档并以控制用例验收，不任意改依赖版本掩盖兼容问题。

本批仍在一个 domestic-sdk-transfers 分支累积，未完成原生/旧版/数据读取验证前不声称 #198 修完，也不另开细碎 PR。源码依据为 HF 版本化 utilities 文档及实际安装源码，ModelScope/ModelScope Hub 官方源码；.tmp/audit143/PRIMARY_SOURCE_NOTES.json 保留阅读范围和来源，临时材料不随 clone。没有真实模型/数据下载或科学实验，未新增布局/公共配置字段。


B143 模型阶段实施选择（2026-10-17）：在既有 origin.py 内用 ContextVar 标记本次来源策略；只在同步 SDK 调用期间安装、退出时还原 requests.Session.send 与 httpx 单次传输检查。每次重定向或元数据选出的 URL 都在该次 transport 进入前核验 HTTPS、无用户信息、默认 443 和来源 host；HF 只允许 hf-mirror，ModelScope 允许 modelscope.cn 子域及 OSS 的 cn 区域 host，不允许泛 aliyuncs 或 modelscope.ai。SDK 使用的 ThreadPoolExecutor.submit 复制本次上下文到文件 worker，包括已存在的线程；无标记的其他线程按原逻辑运行。临时安装由独立 RLock 管理，同类入口串行，worker 不取锁；不重置 client factory、不关闭客户端、不触碰认证/代理/hook 列表。

HF 文件下载模块的 native availability 与配置读取按上下文隔离：国内调用使用标准 HTTP，Xet/旧 hf_transfer 不接管；其他调用读原函数/原常量，不改全局 flags/env。这样 cache-tree 的 Xet 快捷路径也不能绕过请求检查。以真实 SDK Xet 元数据选择的红回归 6621e12 先证（扩展后 8 项，5 failed/3 passed），修后需控制并发/取消/异常还原。ModelScope、旧 HF 及数据 eager/lazy 消费仍须分别补证；该选择不是任意自定义 builder 的网络防火墙，也不以模型阶段 green 声称 #198 全面完成。


B143 数据阶段相邻反例与实施选择：f174b8c 先提交 eager / streaming 两个请求逃逸回归（2 failed/0 error/skip），均使用 SDK 调用/迭代边界替身加真实 requests 栈，不冒称真实 datasets 远端集成。真实 datasets 5.0.0 本地两行 JSON 另核对：streaming 根为 ArrowExamplesIterable，保有 generate_tables_fn、kwargs、分片/恢复状态。load_dataset 调用期使用同一 mirror 上下文；返回的原生 streaming 对象不换类型、不重建 schema/info/state，只给根 generate_examples_fn / generate_tables_fn（及适用的 generate_more_kwargs_fn）绑定可 pickle 的模块级 callable，每次创建/next/close 期间启用同一传输策略、yield 交还用户前退出上下文。这样保留原生 shuffle/shard/format/batch/state 的构造关系，且不把国内标记泄漏到消费线程的其他调用。正常关闭失败仍报告；已有异常/取消不被 close OSError 覆盖，未知程序错误保持原语义。

aiohttp 的每个 ClientRequest 构造前和实际 redirect response 的 Location 选择处补同类核验，前者早于 connector/DNS/连接；不只检查最终响应。多进程 download_config.num_proc、格式/media 延迟访问、线程预取生命周期仍须据实际 SDK 执行分别验证，不以根生成器绑定冒称任意第三方处理代码全被沙箱化。整个 #198 仍同分支累积，无新 PR/关闭/全量验收声明。


B143 数据多进程补全：024b20d 在真实 datasets 5.0.0 的 download_manager.map_nested / multiprocessing.Pool(spawn) 运行 2 个进程的 4 项合成任务；子 PID 确认不同于父进程，子函数使用本地合成 requests adapter，国内策略为空且海外 transport 被进入，1 项红失败、0 error/skip。不是下载服务不可用或夹具失败。保持 download_config.num_proc、原函数/输入/顺序/返回值：只把国内调用传给 download_manager.map_nested 的任务包装为模块级可 pickle callable。子进程入口自行安装同一 guard；已受父调用生命周期管理的线程任务只设置/还原策略，不重取父线程持有的安装锁，以免 joblib/thread backend 等待死锁。无标记的非国内 map 不替换任务。传输补丁是否已安装的进程内标记只在受锁的安装周期有效，不把策略永久留在 SDK 全局或客户端。


B143 媒体与后台生命周期补全方案（2026-10-17）：0582ab5 已先红，真实 Dataset / IterableDataset 的 Image 格式化解码在返回后越界（4 项2failed/2passed）；4837cc6 再确认 ThreadPoolExecutor 后台任务在父 SDK scope 返回后虽保有 ContextVar，却失去实际传输补丁（1 failed）。阶段5452322的 core/Flow green不覆盖这两处。

以最后一个实际消费者结束为卸载条件：安装/还原仅在 RLock 下操作，实际 SDK 工作不持该锁；各下载/decoder/文件读/后台 worker 分别持计数作用域。共有补丁按 owner/attribute/当前对象去重，新导入 SDK 模块进入时补装，计数归零才按拥有关系还原。不改共享 client factory/session/auth/proxy；foreign 无标记调用仍委托原方法。spawn 自安装；fork 先清理继承的 RPipe 补丁/计数和来源标记，防止持久 worker 误继承父 scope。线程任务本身持作用域，不只复制上下文，允许父调用先结束。该方案替代前一模型阶段“整个调用持锁串行”的实现，以消除原生 decoder 读取回调线程等待父锁及后台提前卸载风险；不是性能优化或测速。

媒体保留 SDK 原 schema、原生 Dataset/IterableDataset、已给 features、格式化/缓存与原数据字节：在返回对象实际拥有的 features decoder 绑定已有 domestic callable（原 unbound method+schema，不以 bound-method 循环破坏 pickle）；嵌套 dict/list/sequence 同步处理。仅在有国内上下文时，SDK features 的 xopen 及中央 xopen 返回文件加一个透明只读协议代理；打开仍走原 SDK，后续 read/seek/迭代/close 委托前持同一 guard。这样 Audio/Video/PDF 原生对象保留 seek/range 读取及所有权，无整文件预下载、无新缓存目录，也不把 decoder 构造完成当作 I/O 结束。Image/PDF/Audio/Video 等真实 SDK 使用 xopen 的入口逐项核对；本地路径/内嵌 bytes 不额外下载。派生 view、features deepcopy/序列化、外部无关 consumer、异常/取消/延迟读取/并发均补控制。SDK transport 内部入口已实测 HF0.34.4/1.33.0 与 datasets5.0.0，升级须重核；不声称任意第三方自定义代码获得通用网络沙箱。无设计审批等待，同一#198里程碑，不提前PR/关闭。


B143 实际客户端与站点选择补全（2026-10-17）：52b3f72 先红 ModelScope 已支持 endpoint 却默认选择 .ai 的调用；a43405b 再红实际 HF SDK 配置的客户端子类覆盖 transport 后绕过基类检查。ModelScope 仅在公开签名包含 endpoint 时显式传 https://modelscope.cn，旧签名保留原参数，不改全局站点或吞 TypeError 重试。HF 已加载模块的 get_session/get_async_session 别名委托原 getter，返回相同 client，并仅在国内上下文为实际 client class 的单次请求方法补检查；保留 SDK factory、认证、连接和关闭所有权。未知客户端没有已支持的单次传输入口时明确失败，不静默绕过；异步方法保持 await 语义。所有补丁仍按实际 owner 还原，未标记调用按原 getter/方法工作。测试同时控制合法自定义客户端、并发 foreign、取消和旧版 requests 路径。

媒体路径在 decoder 调用前检查远端协议/host，嵌入 bytes 与本地路径保留；不把 Json 文本当 URL。延迟文件代理只委托原 read/seek 等协议；依据 TorchCodec 官方 [_decoder_utils.py](https://github.com/pytorch/torchcodec/blob/main/src/torchcodec/_core/_decoder_utils.py)、[AudioDecoder](https://github.com/pytorch/torchcodec/blob/main/src/torchcodec/decoders/_audio_decoder.py) 与 [VideoDecoder](https://github.com/pytorch/torchcodec/blob/main/src/torchcodec/decoders/_video_decoder.py) 的 file-like 接口源码核对。当前实际 datasets 媒体 feature 配协议 backend 替身验证，未运行本机未安装的 TorchCodec C++/FFmpeg 或 pdfplumber 实际解码，不将其表述为原生 codec 集成验收。


B143跨Python CI拒绝与补全（2026-10-17）：PR199首head3639ae0的实际Linux3.10矩阵在已提交媒体文件pickle控制中失败（_pickle.UnpicklingError: state is not a dictionary）；Linux3.13控制通过。Python [pickle官方合同](https://docs.python.org/3.10/library/pickle.html#object.__getstate__) 与 [3.11变更](https://docs.python.org/3.11/library/pickle.html#object.__getstate__)表明object默认__getstate__自3.11才存在，3.10会走代理__getattr__委托到底层BytesIO的非dict状态。最小修：文件代理显式返回自身__dict__作为pickle state，不委托底层stream状态；不新增磁盘格式/重建Dataset/要求Python升级。原回归在真实3.10 CI已先红；本地扩四个原生BytesIO支持的pickle协议（2–5）控制，再重核所有最终本机与远端矩阵，不把3.13绿代替3.10。原失败run/job/log保留，不通过rerun原head或删失败获得验收。仍同一PR199/Issue198，不另PR。


### B144 / #200：整类owned读取退出保全（2026-10-17，红回归完成，实施方案）

425cd72先红五个显式库reader（31项14failed/17passed），接着盘点全部src/rpipe，19个Path.read_text/read_bytes隐式owned读取同样会在close IO后丢原KeyboardInterrupt。实际Result/Config/activity/history/log入口20探针确认：前两个抛secondary IO，后三个返回fallback；原字节/peer和owned close一次保全。e4ab3e4再提交扩展69项28failed/41passed、0error/skip，含真实UTF8解码、binary provenance hash和whole-file读取入口清单约束；31原红保留。首扩展候选因fixture不接受Path.open(mode=...)关键字而52失败已拒绝，修正夹具后重验，不把TypeError误归库缺陷。临时原证据.tmp/audit144不随clone。

五显式点：origin._md5（archive）、shared._digest（shared/hash）、index.load_index（strict JSON）、aggregate.load_curve_series（optional JSONL）、Logger._emit的binary tail。Logger/Tracker两处空append acquisition没有body读取，原关闭语义保留。19个whole-file点清单如下；严格/可选性质以现有调用方合同为准，不扩大catch列表。

| **现有模块** | **whole-file点数** | **读取职责/保持的合同** |
| --- | --- | --- |
| system/factory.py | 3 | checkpoint目录meta/tracker/logger JSON，保持strict加载与原始staging诊断 |
| make/schedule.py | 2 | result查询与已写schedule恢复，保持现有optional回退 |
| artifact/provenance.py | 2 | 原始source字节SHA256、provenance JSON验证，保持strict身份 |
| artifact/shared.py | 2 | study shared YAML、已准备输入manifest，保持strict hash/cache合同 |
| artifact/config/io.py | 1 | strict config UTF8/YAML |
| artifact/result/io.py | 1 | strict result UTF8/JSON |
| artifact/activity.py | 1 | optional活动提示 |
| artifact/readout/status.py | 1 | optional日志提示，errors=replace保持 |
| artifact/readout/logs.py | 1 | optional事件读取，errors=replace保持 |
| artifact/readout/compare.py | 1 | strict comparison JSON输入 |
| flow/process/aggregate.py | 1 | optional tracker state |
| make/expand.py | 1 | strict study YAML |
| data/prepare.py | 1 | 已有ready marker来源身份 |
| data/profile.py | 1 | strict profile YAML |

最小公共实现放在现有artifact._atomic，不新增模块/公开export/配置/目录：一个私有_owned_stream，标准contextmanager+ExitStack先调用原manager.__enter__并登记原__exit__；只有body已有异常且exit再抛OSError时保留原body异常。正常body的close IO仍失败；未知close程序错继续传播；原manager抑制语义、enter失败和LIFO所有权保留，不重试close。

同模块加最薄_read_text（UTF8，只有既有readout所需errors参数）与_read_bytes，均复用_owned_stream。19个既有Path read表达式逐点替换，不把parse/hash挪进with：原顺序是读取并关闭后再JSON/YAML/hash，继续保持。五个显式with只包现有manager，不改body。私有helper不全局monkeypatch Path，不吞read/parse的正常错误，不把optional IO改成strict；origin只在MD5函数内部导入，保留本地模型输入/可选SDK导入边界。文件API不变、成功字节/文本newline与编码行为、schema/cache/revision/科学门/布局不变，无性能工作。

本批覆盖库内五显式+19whole-file owned读取，不声称覆盖第三方SDK内部所有IO或独立Study自有实现。正常/失败/取消/程序错/owned关闭、actualUTF8解码/JSON解析、不可变输入与peer、tolerant读出和strict验证分别回归；新增helper的enter失败/原exit抑制另补小控制。再完整相邻/core/CPU Flow和最终head/actualdev全部实际CI验收，一个library-reader-exits里程碑PR，不逐点拆PR；无设计审批等待，200保持OPEN。
