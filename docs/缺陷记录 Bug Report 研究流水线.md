核对日期：2026年10月27日。基线 `96fefa863fafb5eae20e885a4757322c671effbd`。仅登记当前可定位的活动缺陷或合同缺口，设计支持边界与未执行实验分别见开发计划和研究计划。以下为静态确认，未在本轮注入 CI 故障或运行研究。

# 一、活动事项

| **编号与状态** | **触发与期望** | **实际与影响** | **依据及后续验收** |
| --- | --- | --- | --- |
| BR1 CI 失败证据归档缺口，待实施 | CPU 检查失败、超时或后续步骤不执行时，应保留已有 manifest/events/report，关联实际 SHA 与缺项 | unit-tests.yml 没有 artifact 上传步骤，package-check.yml 也没有失败证据上传；远端任务日志不能替代统一结果文件。工作流配置存在不能证明失败归档合同完整 | [unit-tests.yml](../.github/workflows/unit-tests.yml)、[package-check.yml](../.github/workflows/package-check.yml)、架构第十/十一章。DP2 另行授权后实施并验证失败路径 |
| BR2 模板引用已移除指南，待修正 | 用户复制 `_template/study.yaml` 后，应能从根 README 找到注释所指“Study 指南 §1–§3” | 当前 README 已无该章节。模板实际为 MNIST/CUDA、20 epoch，不是离线合成 smoke，配置注释保持待修 | [study.yaml](../studies/_template/study.yaml)、[experiment_config.yaml](../studies/_template/experiment_config.yaml)。DP3 修复引用后核对入口与真实条件，不改变科学参数 |

# 二、已修复与未知事项的边界

已关闭问题的变更与证据入口见[开发记录](<开发记录 Development Record/2026-10-27 文档重构与历史归属.md>)，不在活动缺陷入口保留完成清单。

当前 HEAD 的完整科学数值、真实长程资源、原始历史归档可恢复性以及 CI 矩阵外环境尚未验证。这些缺证据项不自动记成实现错误，也不因没有新增失败就称无缺陷。后续复现记录触发、期望、实际、环境/版本、影响与证据，修复后保留首个反例，不通过放宽研究门关闭问题。
