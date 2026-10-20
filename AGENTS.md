# RPipe Agent 工作约定

适用于本仓及子目录。项目为 [DreamSoul-AI/RPipe](https://github.com/DreamSoul-AI/RPipe)，建议中文名“研究流水线”待 Enmao 确认。本文件只维护协作规则，技术事实和设计分别进入下述唯一文档。

## 一、开始任务

1. 当前用户要求优先，先读目标、README、本文件、同目录成果与相关最新交接，再按任务定位规范。旧聊天、历史债务分支和示例不自动构成本次授权
2. 写前重读目标文件和Git状态，核对当前工作树、分支与基线，保留其他任务已有改动。重叠时先比较，不覆盖不明修改
3. 每次从README的项目关联资料、GitHub组织和规范索引定位DreamSoul文档库，确认当前规范正文。不在AGENTS写盘符、本机绝对路径，也不复制规范到仓库
4. 本轮只交付文档设计Draft，审过后才按目标结构重构代码。文档中的目标接口、目录和保证不得写成现有已实现能力

## 二、唯一文档与资料边界

| 文档 | 只维护什么 |
| --- | --- |
| [README.md](README.md) | 项目说明、使用边界和资料入口 |
| AGENTS.md | 任务协作、规范导航与硬规则 |
| [研究流水线 通用调研.md](<docs/研究流水线 通用调研.md>) | 机制、现有技术、比较、来源、历史验证和待核实问题 |
| [研究流水线 架构设计.md](<docs/研究流水线 架构设计.md>) | 质量场景、职责/接口、状态、依赖、运行/部署、取消/故障、测试与CI/CD |
| [研究流水线 代码结构设计.md](<docs/研究流水线 代码结构设计.md>) | 目标目录、Python职责、依赖限制、测试镜像与源码/产物界限 |
| [研究流水线 研究计划.md](<docs/研究流水线 研究计划.md>) | 唯一中文研究计划，所有Study的实验设计与判据 |

不新建修复、实验、进度、审查或交接文档，不在tests、Study或模板内复制README、计划和规则。文档名使用“项目名 + 空格 + 文档类型”。进度写PR和提交说明，图只用文档内Mermaid。法律许可保留，不计作项目技术文档。

证据JSON、日志、trace、图、源码快照、checkpoint与大报告不进Git。运行物保存在调用方显式指定的外部根，唯一很小的历史摘要在通用调研维护，不能靠提交一堆输出证明完成。真正科学参数、静态输入、schema和第三方来源元数据按用途保留，不能把所有JSON误删为证据。历史结果不得因路径迁移回签身份。

## 三、按任务读规范

从 [规范总索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 和 [工具与教程总索引](https://dreamsoul.feishu.cn/docx/VSQ5dlJl3o0hbIxrEhUc8qKEnBc) 定位当前正文，本表提供已知入口，不保证后续路径永不变化。无法取得时说明实际阅读范围，不声称已核实。

| 任务 | 需要阅读 |
| --- | --- |
| 代码、接口、依赖和持久化 | [代码规范](https://dreamsoul.feishu.cn/docx/FKrsdHO9bolq4YxV2KAcosghnme)，以及本仓架构与代码结构设计 |
| 分支、多代理、跨仓合同和PR | [多人协作代码开发规范](https://dreamsoul.feishu.cn/docx/WihCdOtlqoeYjaxrpeXckFomnCh) |
| 测试、隔离、结果协议 | [测试规范 代码](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf)，测试设计只写本仓架构文档 |
| 日志和故障上下文 | [全局 日志规范](https://dreamsoul.feishu.cn/docx/RBlFdcSSBo93q9x6p8kcdU6wnYb) |
| 自动检查、构建、交付和发布 | [CI CD 执行规范](https://dreamsoul.feishu.cn/docx/NrJ4d0WVGo9t4uxAVwScFTBVnRb) |
| 技术机制、方案调查 | [通用调研规范](https://dreamsoul.feishu.cn/docx/YcWIdE4INoLqoLxR5O8cbl7hnTf) |
| 学术证据与论文阅读 | [文献调研规范](https://dreamsoul.feishu.cn/docx/QKXhdAW2doZCvUxHSoFcpT0Mn5e) |
| 研究问题与实验设计 | [研究计划规范](https://dreamsoul.feishu.cn/docx/POdQdHYeqo7ujpxmJH1ctOMPn1g)，保留其建议采用状态；自动执行再定位自动化研究规范草稿、AutoResearch README及巡检调度规则 |
| 飞书正文、链接、上传与同步 | [飞书文档规范](https://dreamsoul.feishu.cn/docx/DdmxdrNvBo9AMXxkAYmc4EJ6nwe)，并从索引定位上传/使用规范；本地改动不自动授权云端同步或发消息 |

架构任务另读关联资料中的“软件架构设计 Software Architecture Design”通用调研、大纲、端侧智能体案例、来源索引。从质量场景推职责、接口、依赖和运行部署，逐模块说明不加会怎样以及增加的代价，不照搬案例或搭建全套UML/C4/arc42/OCL。

## 四、实施与验证

- 先复用现有实现或标准库，改动对应真实调用方、外部输入与失败路径。不为假设需求增加抽象、空目录和辅助框架
- 宿主科学包保持领域公共结构，只有Study采用RPipe声明与薄适配。六阶段默认实现直接复用，实际有不同职责才增加hook
- 测试镜像本仓源码，通过标签表达unit/integration/e2e、location/content/physical、成本、结果类型与重要性。只测本仓合同和失败路径，不复制别仓内部测试与脚本
- 每个测试进程使用独立临时目录，不把真实数据/旧Run当清理fixture。数值对照逐项写明数据、模型/规模、设备、精度、依赖、seed和来源，不用退出成功、通过数或seed相同替代科学判断
- 读取不启动、不清理、不发布。执行完成、质量达标和证据完整分别判断。保留首要异常与清理异常，取消只处理本次拥有的进程/文件
- 静态检查、本地测试、远端CI和真实实验分别报告。当前HEAD变更后刷新受影响验证，不沿用旧SHA的绿项。未运行、跳过、取消和缺失如实写明
- PR通过审查才能按授权集成，当前任务保持Draft，不合dev/main，不改autoMerge。通用规范的默认流程不覆盖当前禁止合并的要求

## 五、固定副本与清理硬规则

1. dayan、SD、量化内嵌的 `src/rpipe/` 不得修改。RPipe修复只在本仓进行，验证后记录完整提交及文件哈希，按固定提交整套同步并保留MIT许可证，不用下游私改补丁
2. RPipe本仓源码、测试和配置的删除或重构需符合当前授权。本轮不改这些文件，文档目标不构成直接实施许可
3. 重复文档及明确运行产物可以按本轮授权清理。其他代码、科学数据、权重和原始输入删除先问Enmao，不因provenance冻结过就把生成物永久留在Git
4. 不合并dev或main，不强推，不花钱。本轮不用GPU，不启动真实实验，不下载大依赖、模型或数据
5. 汇报本次做了什么、提交号、Draft链接、最终文档清单和剩余问题，注明未验证范围。四仓Draft齐全后停下等审，不自行开始重构
