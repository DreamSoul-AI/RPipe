# RPipe

RPipe（Research Pipeline）是研究项目的代码与执行模板，用声明组织比较因素和seed，把一次Run的有效配置、执行事实、结果与跨seed分析连接起来。Python包名为 `rpipe`，中文名为“研究流水线”。

项目仓库：[DreamSoul-AI/RPipe](https://github.com/DreamSoul-AI/RPipe)。MIT许可见 [LICENSE.md](LICENSE.md)。源码包版本仍为0.4.1；本修复分支从dev的 `8a19c9a8018e1bdc196345aba0f2aba39710fd66` 派生，恢复structure/flow两柱边界，保持Draft等待审查。

## 阅读入口

| 内容 | 唯一维护位置 |
| --- | --- |
| 项目协作、规范入口和硬规则 | [AGENTS.md](AGENTS.md) |
| 技术机制、现有方法、历史验证、来源和未知 | [研究流水线 通用调研](<docs/研究流水线 通用调研.md>) |
| 质量场景、模块合同、状态、运行取消、测试和CI/CD | [研究流水线 架构设计](<docs/研究流水线 架构设计.md>) |
| 目标目录、每个Python文件、依赖规则与测试镜像 | [研究流水线 代码结构设计](<docs/研究流水线 代码结构设计.md>) |
| 项目研究问题、Study实验设计、数据/模型/算法/指标和停止条件 | [研究流水线 研究计划](<docs/研究流水线 研究计划.md>) |

历史债务分支只作参考，不是本轮基线。旧逐事文档、报告、JSON和图片已归并或清理，Git历史中的结果只支持其固定提交和条件，不能当作新分支验收。进度维护在PR与提交说明，不继续增加报告文件。

改版前完整文档见[固定版本docs](https://github.com/DreamSoul-AI/RPipe/tree/ed0d50be4328af3480b54de31243bf507901d033/docs)：包含概念、structure、flow、layout、测试、CI/CD、研究和发布等33份正文。按用户要求已原样恢复一套独立本地快照，连同当时README、AGENTS共35个文件，来源与字节校验清单随快照保存；交付链接见本次PR。历史正文保持原样，旧CLI和运行目录不作为当前使用合同。

## 当前使用边界

源码包位于 `src/rpipe/`，声明位于 `studies/`。纯 `rpipe.api.plan(study_dir, run_root=...)` 展开声明，不装载recipe或Torch、不写盘。CLI的 `plan/status/logs/report/shared` 为读取；`make` 发布配置与索引，`run/launch/run-one` 显式执行，`process` 写派生摘要，`data/tensorboard` 是显式输出操作。所有Study命令要求 `--run-root`，运行根不得包含源码或位于源码内。

```console
rpipe plan studies/_template --run-root <external-run-root>
rpipe run studies/_template --run-root <external-run-root>
rpipe status studies/_template --run-root <external-run-root>
rpipe process studies/_template --run-root <external-run-root>
```

默认每Run独立科学子进程，启动前核源码摘要、配置和自己的train依赖。任意外部data.path/stats_path引用的内容漂移未纳入通用身份保证，原有显式checksum/pin校验继续保留。已有成功不原地重跑，换显式version；未确认停止保持unknown/busy。内存硬限制及同进程注入载荷的硬超时不受支持，启动前拒绝；Windows带recipe/数据worker的硬超时也拒绝。没有自动GPU分配或旧队列恢复。status/report当前展示正式result与busy，未发布结果时显示missing；失败、取消、unknown的原始事实保存在attempts/<uuid>/outcome.json，不按mtime或UUID猜哪次是最新。

库内实现只分两柱：`structure/`提供配置、产物、读取及data/model/algorithm/system组件，`flow/`组织默认六阶段、原生/HF组装和所属进程。旧 `structure.api/*` 等内部导入不自动恢复；调用方改用 `rpipe.api`、纯 `structure.contracts` 或明确structure领域模块。公开`run(..., payload=...)`参数保持调用兼容，不代表第三个源码层。旧Study内运行目录不会自动迁移/补签；目录对照须明确 `compare --legacy`，其Tensor/轨迹比较保留但来源标为未验证。旧GPU调度/阶段裁剪/隐式cache迁移参数不再接受；先按当前声明和显式根重新计划。精确API与兼容表见代码结构设计。

本轮验证以PR中固定HEAD的CPU合同、有限原生/HF数值和离线构建记录为准；没有运行MNIST/CIFAR正式矩阵、GPU、性能或历史长曲线验收。

## 宿主项目如何接入

下游固定完整上游提交，整体同步 `src/rpipe/`，记录来源、清单/哈希并保留许可证。dayan、SD、量化内嵌副本不得自行修改。问题先在RPipe修复、验证，再整体同步一个明确新固定提交，不用浮动dev或逐文件补丁冒充同一版本。

宿主科学包使用自己的公共接口和领域结构。只有 `studies/` 采用研究模板的声明与薄适配，默认六阶段语义为prepare、execute、collect、summarize、write、process，有实际需求才增加hook，不创建空树。宿主不导入Study，RPipe不静态依赖某个正式Study。新公共API与迁移策略见代码结构设计；下游固定副本尚未同步。

真实数据、权重、日志、trace、图、checkpoint及源码快照保存在显式外部运行/共享输入根，不进Git。当前main_exp保留的两个静态JSON是科学输入，暂不移动原路径。测试只测本仓行为，下游不复制RPipe内部测试和辅助脚本。

## 资料与交付

DreamSoul项目与规范从 [组织入口](https://github.com/DreamSoul-AI)、[公司资料入口](https://dreamsoul.feishu.cn/drive/folder/HFFFfNlvelicdsd46PCcrVZ2n4g) 及 [规范索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 定位，具体链接以AGENTS的当前入口为准，不依赖某台机器路径。文档设计采用 [CPU内存清理样板](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/6938f10447b250958856a845fc5448aa1185b52d) 的粒度，从质量场景推导职责和文件边界。

工作分支经Draft PR指向dev，代码试验等待Enmao审查，不合并dev/main、不强推、不自动启用autoMerge。未验证的平台、GPU、真实研究矩阵和性能保持未验证。
