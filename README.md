# RPipe

**从一个可运行、可修改的研究流水线开始你的课题。**

RPipe（Research Pipeline）是一个研究项目的代码起点：用配置描述实验条件，连接数据、模型和训练方法，再执行实验、保存结果并做比较。你可以复制或 fork 本仓，先跑通小实验，再直接修改代码适应自己的课题，后续由该项目自行维护。

[开始使用](#开始使用) · [设计](#两柱设计) · [文档](#文档导航) · [参与开发](#参与开发)

本轮版本为 **v0.5.1**，包含外部 data/output 链接配置、Windows 取消收尾修复、GitHub 文档整理和研发约定更新。[发布状态与附件](https://github.com/DreamSoul-AI/RPipe/releases)从 GitHub Releases 核对；旧发布制品保留原内容。Python 包是本模板的安装和运行方式，不承诺跨项目稳定 API。

## 开始使用

1. 复制或 fork 本仓，在本地打开仓库目录
2. 准备 **Python 3.10+** 的独立环境，按 [pyproject.toml](pyproject.toml) 安装依赖
3. 按下方 CPU 示例完成训练和评测，再换成自己的数据与方法

在仓库根目录安装并查看命令。安装可能联网下载 PyTorch 等依赖，先核对已有环境：

```console
python -m pip install -e ".[dev]"
python -m rpipe --help
```

**第一次运行从 [CPU 最小研究](<docs/使用指南 Usage Guide 研究流水线.md#从模板开始一个小研究>)开始。** 指南提供两份完整配置和数据构造代码，用 8 个合成样本训练两步，再单独加载该训练选出的最佳模型进行评测。依赖就绪后，示例不需要下载数据或使用 GPU。

`studies/_template` 原始配置是 **MNIST / CUDA / 20 epoch**，直接运行它会进入不同的实验。先完成 CPU 示例，再按自己的研究计划准备真实数据和设备。

### Study 使用与执行边界

一个 **Study** 是一组相关实验，`study.yaml` 描述比较条件和随机种子（seed），`experiment_config.yaml` 保存基础参数。同一组条件下，每个 seed 对应一次独立运行（Run）。

CPU 示例会带你设置源码与输入目录之外的绝对输出路径，再用 `plan --show-config` 预览每次运行的有效配置。预览不加载研究代码、不下载数据、不训练，也不写运行产物。核对配置后再执行。

完成 CPU 示例配置后，常用流程是：

```text
plan → make → launch → process → report
```

| 你要做什么 | 阅读位置 |
| --- | --- |
| 完成第一次训练与独立评测 | [CPU 最小研究](<docs/使用指南 Usage Guide 研究流水线.md#从模板开始一个小研究>) |
| 配置外部 data/output 入口 | [本机链接配置](<docs/使用指南 Usage Guide 研究流水线.md#配置本机-dataoutput-链接>) |
| 改条件、查看结果或读取旧版本 | [命令与执行合同](<docs/使用指南 Usage Guide 研究流水线.md#计划发布与执行>) |
| 设置监管、理解取消与资源限制 | [资源监管](<docs/使用指南 Usage Guide 研究流水线.md#资源监管与当前限制>) |

数据、权重、缓存和运行产物放在源码树外。仓内链接只提供访问入口，不构成写保护。改变科学代码或条件时使用新 version，保留旧结果和来源。执行完成、科学质量与证据完整分别判断。

## 两柱设计

| 部分 | 负责什么 |
| --- | --- |
| **structure** | 实验条件、数据、模型、算法、系统部件，以及配置、快照和正式结果的产物机制 |
| **flow** | 装配、阶段执行、取消、资源收尾、执行权和唯一正式结果写入 |

执行阶段为 `prepare → execute → collect → summarize → write → process`。worker 返回草稿和收尾观察，由持权的 run 经唯一 write 定稿。研究者可以直接调整部件和流程，不必先建立插件或兼容层。

详细职责与取舍见[架构设计](<docs/架构设计 Architecture Design 研究流水线.md>)，目录、接口和依赖见[代码结构设计](<docs/代码结构设计 Code Structure Design 研究流水线.md>)。两份设计各保持一份完整正文。

```text
src/rpipe/
  structure/    # 条件、科学部件与产物机制
  flow/         # 生命周期与执行流程
studies/        # 可修改模板、main_probe、main_exp
tests/          # 镜像测试、统一执行器与安装验证
docs/           # 使用、设计、计划及日期记录
```

## 文档导航

按你当前的问题选读，不必从头遍读研发历史。

| 文档 | 回答的问题 |
| --- | --- |
| [使用指南](<docs/使用指南 Usage Guide 研究流水线.md>) | 怎样配置、运行、改研究代码和查看结果？ |
| [架构设计](<docs/架构设计 Architecture Design 研究流水线.md>) | 职责、流程和质量目标如何组织？ |
| [代码结构设计](<docs/代码结构设计 Code Structure Design 研究流水线.md>) | 目录、接口和依赖如何对应架构？ |
| [开发计划](<docs/开发计划 Development Plan 研究流水线.md>) | 当前工程工作、依赖和验收是什么？ |
| [缺陷记录](<docs/缺陷记录 Bug Report 研究流水线.md>) | 哪些已确认的问题仍待处理？ |
| [通用调研](<docs/通用调研 General Research 研究流水线.md>) | 工程机制、方案和来源是什么？ |
| [文献调研](<docs/文献调研 Literature Review 研究流水线.md>) | 已有研究和证据支持什么？ |
| [研究计划](<docs/研究计划 Research Plan 研究流水线.md>) | 科学问题、实验协议和判据是什么？ |
| [开发记录](<docs/开发记录 Development Record/>) / [运行记录](<docs/运行记录 Run Record/>) | 具体版本改过什么、实际运行结果和限制是什么？ |

已有 CPU 使用旅程、CUDA 短配对和固定历史版本的完整研究验证。它们各自绑定代码、环境与协议，不代表当前任意改动已取得科学验收。采用范围从[使用走查记录](<docs/运行记录 Run Record/2026-11-02 模板使用走查.md>)和[外部路径验证](<docs/运行记录 Run Record/2026-11-04 外部路径链接与CPU旅程.md>)追溯。

## 参与开发

从 [AGENTS.md](AGENTS.md) 读取本仓约定与共用研发资料入口。本仓采用 workspace 的[研发用模板](https://github.com/DreamSoul-AI/workspace/blob/main/templates/software-engineering/AGENTS.template.md)，对应[《研发用 AGENTS.md》](https://dreamsoul.feishu.cn/docx/QICWdeEokobO9RxYR9zcjE6Pnyg)，不能用 workspace 根通用 AGENTS.md 替代。飞书资料需要相应访问权限。仓库 Markdown 按 workspace 的[GitHub 文档规范](https://github.com/DreamSoul-AI/workspace/blob/main/%E6%96%87%E6%A1%A3/%E8%A7%84%E8%8C%83/%E7%A0%94%E5%8F%91/%E5%BC%80%E5%8F%91/GitHub%20%E6%96%87%E6%A1%A3%E8%A7%84%E8%8C%83.md)维护，平台展示与实际执行分别验证。

测试统一入口为 `python tests/run.py`，选择与改动有关的检查，输出和各进程临时目录放在 checkout 外。[测试说明](tests/README.md)维护实际组织，[使用指南](<docs/使用指南 Usage Guide 研究流水线.md#测试与验证>)提供命令和验证边界。现有 CI 覆盖 Ubuntu / Windows、Python 3.10 / 3.13 的 CPU 检查，以及两平台打包安装，不承担 GPU 科学验收。

工作分支经 PR 进入 dev，main 只接受 dev 的集成 PR。合并、发布和下游同步按各自授权执行。项目经验进入 [Workspace 沉淀](<docs/Workspace 沉淀/Experience.md>)，详细过程归日期记录。个人配置可从 workspace 的[空白模板](https://github.com/DreamSoul-AI/workspace/blob/main/USER.template.md)创建本地 `USER.md`，按 AGENTS.md 核对后使用；该文件已忽略，不提交或打包个人信息。

已有下游的固定源码副本保留各自历史身份。同步须另行确认，保留完整上游 SHA、成员哈希和 MIT 许可，不逐文件补丁后冒充同一来源。

## 许可

[MIT](LICENSE.md)。固定科学输入、来源元数据和第三方许可按各自用途保留。

<a id="数据模型与大产出暂行规范待实测"></a>
<a id="配置本机-dataoutput-链接"></a>
<a id="从模板开始一个小研究"></a>
<a id="计划发布与执行"></a>
<a id="资源监管与当前限制"></a>
<a id="验证与证据"></a>
<a id="安装与入口"></a>
<a id="技术栈与目录"></a>
<a id="文档与阅读顺序"></a>
<a id="项目经验与-workspace-汇集"></a>
<a id="数据模型与大产出本项目约定"></a>
<a id="宿主接入与资料来源"></a>

<details>
<summary>从旧 README 章节链接来到这里？</summary>

完整操作和执行边界已集中到[使用指南](<docs/使用指南 Usage Guide 研究流水线.md>)。研发约定从 [AGENTS.md](AGENTS.md) 进入，历史验证从日期记录追溯。

</details>
