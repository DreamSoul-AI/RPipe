# 一、身份、环境与授权

执行及整理日期2026年10月30日，北京时间。候选f64e3d24b90dcc9dc950a85053815e808b6d5810，参考98648f3a5c7db7dccf3ca806410d5b6fdee9484c，source_digest为abd3284fe822a5fab22fc3ac36c5b45341f6a0d8c2041a0c5a9d234f418a5953。version为main-probe-e3-20261030-f64e3d24b90d；用户明确确认原预算下新版本执行，旧失败版本保持，retry未启用。

运行根D:/GitHub/DreamSoul/output/RPipe；中央data根D:/GitHub/DreamSoul/data。16项SHA256和samefile重新核对通过。原解释器D:/anaconda3/python.exe，PYTHONPATH为本轮profile;packages;本仓src；CUBLAS_WORKSPACE_CONFIG=:4096:8、CUDA_VISIBLE_DEVICES=0，HF_HUB_OFFLINE/HF_DATASETS_OFFLINE=1。未安装新依赖。

Python3.13.9、Torch2.11.0+cu130、torchvision0.26.0+cu130、NumPy2.3.5、kornia0.8.3、evaluate0.4.6、datasets5.0.1、CUDA13.0、cuDNN91900；GPU0 RTX5090 D v2，驱动621.01，UUID GPU-f204852e-ebaa-4101-d6b8-d5f309db66c8。实际system为CUDA、seed0、deterministic/cudnn_deterministic true、benchmark及两类TF32 false。实际模型结构与float32参数证据引用[扩展CPU准入](<2026-10-30 首轮Study执行与准入验证.md>)，科学源码244成员未改变。

# 二、命令与外部原件

所有下述外部路径均在D:/GitHub/DreamSoul/output/RPipe-evidence/，不是Git随附下载资源。

| **原件** | **内容与结果** |
| --- | --- |
| e0-main-probe-e3-20261030-f64e3d24b90d/E0.json | 新身份、8规格、原始输入/依赖/实际控制及CPU准入引用复核通过 |
| 同根command-0.json、0.stdout/stderr.txt | python -B -m rpipe make，返回0 |
| 同根command-1.json、1.stdout/stderr.txt | python -B -m rpipe supervise，返回0；UTC16:32:53.433至16:37:43.071，北京时间30日00:32–00:37 |
| e3-main-probe-e3-20261030-f64e3d24b90d/report.json | succeeded，8完成、0未派发，evidence_complete true、tree_empty true、cleanup_errors空 |
| 同根audit/verification.json、before-process.json | 正式读取及实际安全Tensor树复核；41项文件在process前后SHA256相同 |
| 同根audit/status、report、process.command.json及stdout/stderr | 三个正式CLI均返回0，Study终验complete/passed true，8格齐全、无missing |

完整argv见command JSON。make/supervise均显式--run-root及--version；supervise额外--run-seconds900、--batch-seconds7200、--rss-bytes8589934592、--output-bytes21474836480、--poll-seconds0.25、--cleanup-seconds5及全新独立--evidence-dir。整批监管时间289.3768904秒，RSS采样峰3687866368字节，新增输出峰2824260160字节，最大一次采样耗时0.1401727秒；均低于确认停止线，采样不是硬配额，也不是公平性能基准。

# 三、实际数值与样本

两数据集×四模型×seed0，每侧60次更新，第30/60步完整test各10000，best由各自test/Loss选择并各自独立eval10000。每侧实际累计train15000/test20000，独立eval10000；总计960次更新、240000训练样本处理、320000周期test及160000独立eval样本处理。

8格初始化、第30/60步及best权重均精确一致，参数最大绝对差0，优化器状态、scheduler与CPU/CUDA RNG精确一致；实际输入图像、标签及core字节哈希与整数计数门通过。Loss最大绝对差4.440892098500626e-16，低于1e-6；不是全部差0。两侧best均选step60，独立eval Loss差均0，自身best复核及实际样本门通过。

| **组合** | **Run ID** | **两侧best独立eval Accuracy，%** | **30/60步train/test最大Loss差** |
| --- | --- | ---: | ---: |
| MNIST/linear | 7fef3127986fae62 | 90.73 | 0 |
| MNIST/mlp | d687c7ba433f3a34 | 91.82 | 2.22e-16 |
| MNIST/cnn | 6d64a8f2867bfbb7 | 11.35 | 0 |
| MNIST/resnet18 | 2ea60e0c3f23699d | 97.63 | 0 |
| CIFAR10/linear | 2418ecfff408a0d0 | 29.52 | 0 |
| CIFAR10/mlp | db6aaa814b4817b9 | 40.30 | 2.22e-16 |
| CIFAR10/cnn | ff97db8736629aec | 32.89 | 4.44e-16 |
| CIFAR10/resnet18 | 4a14805f797eff4c | 47.73 | 0 |

Accuracy按两位小数展示，原始精度保留在result/OBSERVATIONS。MNIST/cnn短程11.35%等数值原样保全，不调整配方或挑选结果；本实验回答短前缀行为保持，不证明模型效果充分、模型排名或无偏泛化。

# 四、独立回读与完整性

8个正式RunRef的result/config/execution身份一致，quality及执行证据完整为true，worker响应science_closed true、unknown_resources false、diagnostics_complete true，关闭/诊断错误为空，Run owner均释放。实际OBSERVATIONS经weights_only=True安全加载，先核其SHA与保存比较来源一致，再逐项直接复核实际Tensor/优化器/RNG/scheduler树，没有重放训练。

正式status/report/process均成功；summary/PROBE_COMPARISON.json完整8格、passed/complete true，summary/process.json complete true。process只消费已保存结果，index/config/result/execution/worker响应/原始OBS共41项哈希未改变。单Run的full_matrix_complete false只表示该Run选择单组合，整轮结论以Study终验为准。

通用input_provenance.complete在8格均为false，pins只验证EXPECTED_DATA.json文件表，references为空。这不等于全部通用来源都已固定。16项raw身份、完整数据准备、实际输入与模型来源另由E0/PREPARED/归档哈希及观测字节核实；本轮结论明确依赖这些Study专用证据，不回写通用标记为true。

独立只读审查与原始JSON一致，不构成GitHub Approve。旧失败版本仍无科学结果或残留owner，未覆盖其失败。main_exp600步/E4、跨设备/跨版本、更多seed、长程性能、远端CI、新制品安装及发布未执行。
