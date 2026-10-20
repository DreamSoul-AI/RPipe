# RPipe

RPipe（Research Pipeline）是研究项目的代码与执行模板，用声明组织比较因素和seed，把一次Run的有效配置、执行事实、结果与跨seed分析连接起来。Python包名为 `rpipe`，建议中文名为“研究流水线”，待 Enmao 确认。

项目仓库：[DreamSoul-AI/RPipe](https://github.com/DreamSoul-AI/RPipe)。MIT许可见 [LICENSE.md](LICENSE.md)。当前文档重建基线为 `ed0d50be4328af3480b54de31243bf507901d033`，源码版本仍是0.4.1。这轮设计待审，尚未按目标结构重构代码。

## 阅读入口

| 内容 | 唯一维护位置 |
| --- | --- |
| 项目协作、规范入口和硬规则 | [AGENTS.md](AGENTS.md) |
| 技术机制、现有方法、历史验证、来源和未知 | [研究流水线 通用调研](<docs/研究流水线 通用调研.md>) |
| 质量场景、模块合同、状态、运行取消、测试和CI/CD | [研究流水线 架构设计](<docs/研究流水线 架构设计.md>) |
| 目标目录、每个Python文件、依赖规则与测试镜像 | [研究流水线 代码结构设计](<docs/研究流水线 代码结构设计.md>) |
| 项目研究问题、Study实验设计、数据/模型/算法/指标和停止条件 | [研究流水线 研究计划](<docs/研究流水线 研究计划.md>) |

历史债务分支只作参考，不是本轮基线。旧逐事文档、报告、JSON和图片已归并或清理，Git历史中的结果只支持其固定提交和条件，不能当作新分支验收。进度维护在PR与提交说明，不继续增加报告文件。

## 当前使用边界

源码包位于 `src/rpipe/`，声明位于 `studies/`。Python最低版本和依赖以 [pyproject.toml](pyproject.toml) 为准，不因阅读新文档而升级。现有 `python -m rpipe` / `rpipe` CLI提供make、run、launch、run-one、status、logs、process、report、compare等入口。make会写配置和脚本，运行命令会启动载荷，process/report会写分析文件，不能把它们作为只读检查。

本轮只检查文档、依赖与源码完整性，没有运行这些命令或训练实验。新设计要求显式运行根与源码分离，但当前0.4.1仍会在Study下写产物，不能照目标目录直接运行。现有Study引用已移除的报告路径和冻结TARGET文档，旧文档路径测试也需要审后迁移；当前分支并非可直接复用旧冻结Run的交付版本。

## 宿主项目如何接入

下游固定完整上游提交，整体同步 `src/rpipe/`，记录来源、清单/哈希并保留许可证。dayan、SD、量化内嵌副本不得自行修改。问题先在RPipe修复、验证，再整体同步一个明确新固定提交，不用浮动dev或逐文件补丁冒充同一版本。

宿主科学包使用自己的公共接口和领域结构。只有 `studies/` 采用研究模板的声明与薄适配，默认六阶段语义为prepare、execute、collect、summarize、write、process，有实际需求才增加hook，不创建空树。宿主不导入Study，RPipe不静态依赖某个正式Study。精确接入与兼容迁移需待本轮设计评审后实施。

真实数据、权重、日志、trace、图、checkpoint及源码快照保存在显式外部运行/共享输入根，不进Git。当前main_exp保留的两个静态JSON是科学输入，暂不移动原路径。测试只测本仓行为，下游不复制RPipe内部测试和辅助脚本。

## 资料与交付

DreamSoul项目与规范从 [组织入口](https://github.com/DreamSoul-AI)、[公司资料入口](https://dreamsoul.feishu.cn/drive/folder/HFFFfNlvelicdsd46PCcrVZ2n4g) 及 [规范索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 定位，具体链接以AGENTS的当前入口为准，不依赖某台机器路径。文档设计采用 [CPU内存清理样板](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/6938f10447b250958856a845fc5448aa1185b52d) 的粒度，从质量场景推导职责和文件边界。

工作分支经Draft PR指向dev，当前等待Enmao审查，不合并dev/main、不强推、不自动启用autoMerge。未验证的源码运行性、数值、性能和异机情况保持未验证。
