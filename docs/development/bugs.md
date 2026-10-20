# Bugs

> 这里只保留尚未解决的缺陷。编号只增不复用；修复并验证后移除条目，开发证据留在 [record.md](record.md)，实验依据留在对应 Study 报告。正常差异或决定不修的事项不列为开放缺陷。权威仍是 [concept.md](../code/concept.md) → [layout.md](../code/layout.md) → 代码。

更新日期：2026-10-20。

## 开放

旧索引 B-135 / B-136 已关闭，修复与双侧验收见 [record.md §235](record.md)。本次未重复关闭 Issue，也不把索引纠正计为新增偿还。

2026-10-20 的 R1–R5 技术债修复位于独立 Draft 分支，尚未集成，执行和验证边界见 [record.md §299](record.md)。旧条目的历史状态保留在对应 Git 提交及开发记录中。 R1 的 make 配置写入者补充及 458 项相关回归见 [record.md §299.2](record.md#2992-r1-make-写入者准入)；该补充尚未集成，不重开旧已关闭项。R1 未选中 stale eval 锁忙时的纠错拒绝传播及 264 项窄回归见 [record.md §299.4](record.md#2994-r1-未选中-stale-eval-的纠错拒绝传播)，同属此 Draft 的未集成补充。R3 的 xfail 阶段分类补充及 89 项最终测试设施窄回归见 [record.md §299.5](record.md#2995-r3-已知失败的阶段证据与报告分类)，仍未集成，不另增主债项。

B133–B134/#182/#183已随[PR184](https://github.com/DreamSoul-AI/RPipe/pull/184)最终head及实际dev28f3dde所有实际CI验收关闭，主题本地+remote已清；详[record§226–229](record.md)。

B131–B132/#179/#180已随[PR181](https://github.com/DreamSoul-AI/RPipe/pull/181)最终head及实际dev593385f全部实际CI验收关闭，主题已清，五PR周期盘点完成；原独立红/解析/字节/限制见[record§221–225](record.md)。

B129–B130/#176/#177已随[PR178](https://github.com/DreamSoul-AI/RPipe/pull/178)最终head及实际dev53ae142全部实际CI验收关闭，主题本地/remote已清；原红/验证/限制留[record§217–220](record.md)。

B124–B128/#170–#173/#175已随[PR174](https://github.com/DreamSoul-AI/RPipe/pull/174)最终head与实际dev9d78fb4全部实际CI验收关闭，已清本地/远程主题分支；原Windows失败与回归证据留[record§209–216](record.md)。

B122/#167、B123/#168已PR169双侧全实际CI验收关闭并清本地/remote，详[record§208](record.md)。

B119/#163、B120/#164、B121/#166已PR165双侧全实际CI验收并关闭，已合主题本地+remote清理，详[record§203](record.md)。

B117/#160、B118/#162已PR161双侧全部实际CI验收关闭且主题本地/remote清理，第5批周期盘点见[record§193](record.md)。

B116/#158已由PR159双侧全部实际CI验收关闭并清本地/remote，见[record§187](record.md)。

B115/#156已PR157双侧全部实际CI验收关闭并清本地/remote，见[record§185](record.md)。B114/#154由PR155验收关闭，见[record§182](record.md)。

B101/#130已PR131合dev并验收关闭，正式记录与分支清理见[record§154](record.md)。

前一轮已关闭26个已处理Issue（24正确性+2初步调研），完整清单与旧反例见[record§150–151](record.md)及[清理前索引](https://github.com/DreamSoul-AI/RPipe/blob/2008753c3c2b11321a3da3811102cf5c20b646ff/docs/development/bugs.md)。关闭不等同main发布或授权探索后续实施。


## 暂缓（不进入正确性修复队列）

### B-065：单次来源清单采集对重叠输入反复解析、读取和哈希

- 状态：deferred；按最终仅做正确性的要求已用新普通提交撤回ad67f74次数去重优化，原复现与性能记录仅作历史保留，不进入本轮修复队列。见[范围收缩记录](record.md#57-按最终范围暂缓-b-065-性能去重)。
- 来源：source_files 经 study_phase_files、flow/structure 自动扫描、recipe 与 provenance.include 收集同一路径，最终 mapping 覆盖重复键，但此前每次均执行 _inside/读取/哈希。生产调用方为 write_provenance 和 provenance_changes，后者供 launch/prepare/Runner/Study process 冻结检查。
- 期望：同一次清单采集每个解析后 Study 文件只读取/哈希一次；重复原始字符串不反复解析；新一次调用仍完整发现文件并重读，保持变化检测、路径边界和读取错误。
- 实际：两项真实读取计数用例失败，flow 开关关闭时两个 flow 文件各读2次、helper4次，开启时 flow各3次、helper4次。32个合成源文件、2个声明及1个隔离库文件共35个唯一输入，被读取/哈希100次；三次小场景结果相同。
- 证据：.tmp/phase065/red.log/red.xml 为 2 failed /1 passed，0.20 s；before.json/日志逐层记录 total/resolve/glob/open/read_bytes/hash，源码原字节在before.zip；BEFORE.json/before.json大小写别名覆盖的原hash索引缺口及事后重建SNAPSHOT_FILES.json已在record §52如实记录，不随clone。当前量化冻结 helper 的162文件单调用仅诊断其内部阶段，不等于原480s现场，也不是本问题的同一函数。
- 最小方案：仅在一次 source_files 调用内按首次出现去重原始声明，再按已验证的解析 Path 去重 Study 文件；不缓存跨调用内容/清单，不减少任何独立 freeze 检查，不改库文件集合、格式、Run或下游业务。补修改后下一次调用、读取拒绝、空匹配和越界回归，保留 add/edit/delete 旧门。

## 集成与历史说明

[归档快照](maintenance_archive_2026-10-15.md)明确区分合入前与后续状态；PR #46 的最终工作提交 `90b7d75323d266c4d272f786c28f4c00959d0432` 与实际 dev `5ee1214` 分别验证，不能互相替代。归档前条目中的 open / 草案文字不再代表当前代码状态。B-053（跨 Study 输入共享）和 B-054（关闭扩展时的初始冻结拒绝保全）此前经 [PR #39](https://github.com/DreamSoul-AI/RPipe/pull/39) 合入 dev `614d0a6`，证据见 [集成记录](record.md#39-共享与三个反馈修复合入-dev)。

B-055 的原生 launch 重试关闭入口已通过 [PR #42](https://github.com/DreamSoul-AI/RPipe/pull/42) 合入 dev `8c15cd84f8a47d5bfeb80edfa69bc5c370998e74`。最终工作提交 `8269bff` 的三项必需检查及实际 dev 的 [Unit Tests](https://github.com/DreamSoul-AI/RPipe/actions/runs/38557561151) / [Package Check](https://github.com/DreamSoul-AI/RPipe/actions/runs/38557561212) 均通过，包含 Windows / Python 3.13；开发证据见 [记录 §40](record.md#40-原生-launch-显式关闭失败重试41验证通过)。默认重试仍保留；#41在v0.4.0发布后经本次明确授权关闭，而非默认自动关闭。

原v0.3.1未包含这些修复；截至v0.4.0，#36—#38及#41已在main发布并经明确授权关闭。历史dev验证与各自发布版本仍分开记录。没有开放软件条目不代表所有后端、设备或研究条件均已验收。

此前撤回的范围外反例保留历史事实，未自动恢复为本目标的实施队列；接续以新的实际反馈和已确认设计为准。旧收缩依据见[当时记录](record.md#32-按用户要求收缩到本机适配与三个下游-issue)。

B-002、B-021 / B-024及B-025至B-029的既有修复证据仍在 [record.md](record.md)。B-022 / B-023 的代表性容量、精度合同与探索边界保留各自报告；本轮新的设备 / 并发结果见 [本轮执行记录](../../studies/main_exp/docs/v030_execution.md)。未实测支持范围不是已通过，缺环境依赖的导入失败也不自动列为库缺陷。

已使用至 B-102，编号不复用；下次新增问题从 **B-103** 起编号。历史段落的旧“下一编号”仅表当时状态。

新增条目一条一事，记录编号、open / investigating状态、复现条件、期望 / 实际、影响落点、方案和实际验证证据。


## 2026-10-16 最新状态补记

B102/#132 已经 PR133 合入 dev 并全 CI 验收关闭，上文待验收文字仅历史。B103/[Issue134](https://github.com/DreamSoul-AI/RPipe/issues/134) 为当前开放项：共享锁路径别名自争用，四红后最小修复，本机共享72/core1697+1既有skip通过，待Flow及最终/实际dev CI。详[record§157–158](record.md)。


B103 本机补记：CPU Flow 207 passed、0 failure/error/skip；最终 head 与实际 dev CI 仍待验收。


## 2026-10-16 B104 当前开放项

B103/#134 已PR135实际dev验收关闭并普通清分支；当前B104/[Issue136](https://github.com/DreamSoul-AI/RPipe/issues/136)为迁移多根锁顺序受namespace拼写影响，2红/2控制后最小修复，共享76通过，待完整本机及CI验收。详[record§159–160](record.md)。


B104本机验收补记：shared76/core1701/CPU Flow207 passed，core另1既有平台skip；新四节点全执行。最终head/实际dev CI待验收。


## 2026-10-16 B105 当前开放项

B104/#136已PR137实际dev全CI验收关闭。B105/[Issue138](https://github.com/DreamSoul-AI/RPipe/issues/138)：stdout诊断拒绝会跳过非零子进程退出后的既有success失效写回。6红/2控制后最小顺序修复，调度88通过，待完整本机及CI。详[record§161–162](record.md)。


B105本机验收：调度88/core1709/CPU Flow207 passed，core另1既有平台skip；八新节点全执行。最终head与实际dev CI仍待验收。


## 2026-10-16 B106 当前开放项

B105/#138已PR139实际dev全CI验收关闭并完成周期清理。B106/[Issue140](https://github.com/DreamSoul-AI/RPipe/issues/140)：savefig直接写旧PNG时部分失败破坏旧图、遗留自有figure；19项先红17/2，最小局部原子发布/释放修复已本机19绿、core1728+1既有skip。待Flow与两侧CI；见[record§163–164](record.md)。


B106本机验收：19新节点/core1728/CPU Flow207 passed，core另1既有平台skip；含真实Windows只读清理的19新节点全执行。最终head/实际dev CI待验收。


B106同批补充确认关闭IO二错可遮原保存错/取消；6项先红4/2后修复，当前25新节点绿、core1734+1既有skip，最终Flow/新headCI待验收。仍仅Issue140/PR141。


B106含流关闭补充的最终本机验收：25新节点/core1734/CPU Flow207 passed，core另1既有平台skip；25新节点含原生只读清理全执行，PNG字节仍一致。更新后最终head/实际dev CI仍须验收；旧9da02b4绿仅历史。


## 2026-10-16 B107 当前开放项

B106/#140已PR141更新head/实际dev全CI验收关闭并清分支。B107/[Issue142](https://github.com/DreamSoul-AI/RPipe/issues/142)：共享锁unlock/close IO可遮准备首错/取消；25项先红19/6后最小退出回调修复，共享101通过，待core/Flow/两侧CI。见[record§165–166](record.md)。

B107 / Issue142本机补充：共享101、core1759 passed/1既有平台skip、CPU Flow207 passed，25新回归全部执行；最终head/实际dev CI待验收，见record §166。


## 2026-10-16 B108 当前开放项

[Issue144](https://github.com/DreamSoul-AI/RPipe/issues/144)：四个文本/日志/tracker写入口的close IO替换首错或取消。dcd4e2d先30项16红/14控制，383d68c设计、2c33a65最小四写入块修复；30新节点全绿、相邻98 passed/1既有平台skip、core1789 passed/1既有skip；Flow和最终head/实际dev CI待验。原写入格式与字节通过冻结旧源码对照，临时原证据.tmp/audit108不随clone，见[record §167](record.md)。前序Issue142已PR143合dev，实际dev验收尚待完成，不冒充关闭。


B107/#142已PR143最终head与实际dev全CI验收关闭；主题本地/remote已清。B108/#144最终本机30新回归全执行通过、相邻98+1既有平台skip、core1789+1同一skip、CPU Flow207 passed；0failure/error，最终head与实际dev CI待验，保持open，见record §168。


## 2026-10-16 B109 当前开放项

[Issue146](https://github.com/DreamSoul-AI/RPipe/issues/146)：剩余Study plot_report直接保存PNG，部分保存/取消覆盖旧图且render/save/cancel泄漏自有figure。6ab3b74先25项23红/2正常控制，70cad05设计，1db97b1仅现有helper按已实施PNG所有权合同修复；新25与相邻PNG67全绿，core/Flow/最终head/实际dev待验。有效PNG76906字节旧新同SHA、绘制主体/科学函数保持；只用AST+合成输入，不跑Study，见record §169。前序PR145已head全CI合dev，Issue144仍待实际dev验收。


B108/#144已PR145最终head与实际dev全CI验收关闭，主题本地/remote普通删除。B109/#146本机25新节点全执行、PNG相邻67/core1814+1既有平台skip/CPU Flow207 passed；0failure/error，最终head/实际dev CI待验，保持open。见record §170。


## 2026-10-16 B110 当前开放项

[Issue148](https://github.com/DreamSoul-AI/RPipe/issues/148)：深层JSON的RecursionError逃逸冻结拒绝分类，使outer/native prepare零构建仍覆盖旧成功。初2000层无效反例保留，更正12000层真实解析四新旧路径确认；dadb558先12项5红/7控制、32f7a54设计、e99691d单行既有except补RecursionError，12新节点与相邻204全绿。strict loader/限额/schema和真实phase失败规则保持，core/Flow/两侧CI待验。见record §171。


B109/#146已PR147最终head与实际dev全CI验收关闭，主题本地/remote普通删除。B110/#148最终本机12新节点全部执行（core4/Flow8），相邻204、core1818 passed/1既有平台skip、CPU Flow215 passed；0failure/error，最终head/实际devCI待验，保持open。见record §172。


### B138 / #189 国内源下载路由（2026-10-17）

按用户03:16新增合同登记：旧ModelFactory在builder后才设HF_ENDPOINT，DataFactory不设置；没有共享ModelScope优先/镜像数据入口。03bb7ce先红7失败/5旧控制→c97eb6d方案→c859b8e生产。完整core发现通用origin拒绝旧HF导入过宽，7失败保留并收回到实际HF数据入口；修正定向67通过，完整重验中。188/189保持OPEN、当前分支未开PR，合批里程碑验收。原始.tmp/audit138不随clone，零真实下载/GPU/训练，服务可用性未验证。


2026-10-17最终本机验收补充：#188/#189合批core2220passed+1既有skip、Flow429passed、路由21passed，26新节点全执行/0新skip，0fail/error；无活本机句柄，仍待同一里程碑PR双侧全部实际CI，Issue保持OPEN。详record§238；原候选拒绝/红回归保留，不作删除或服务可用性证明。


2026-10-17验收补充：B137/#188及B138/#189已PR190合dev e31fbf5，最终head/实际dev所有真实矩阵与聚合通过；两Issue各一次验收评论关闭、原正文保留，旧主题本地/远端正常删。详record§239；不表示发布/真实下载成功，历史失败不删。当前开放Issue为空，下一编号B139，未确认新反例不制造任务。


### B139 / Issue #191：可解析JSON中的surrogate标签阻断readout发布（2026-10-17）

真实write_numbers和compare_runs→write_compare：正常label可发布peer，ASCII JSON转义的未配对代理label可解析却真实UnicodeEncodeError，旧目标/输入保持。75全状态Issue去重（111是测试证据JSON，非readout）；191一处范围追加保原文。先红a55f38a36项18失败、c0dc4016项3失败，方案f2bf035/0cfea56；4926b81修共用display文本、9b2246c修派生compare JSON，strictResult/config/provenance及atomic writer不变。814最终相邻passed，42新全执行；原生roundtrip/正常字节/peer保持。最终core/Flow进行中，未推送/PR，保持OPEN。证据.tmp/audit139不随clone，详record§240–241。


B139同类补全（2026-10-17）：可选activity提示亦真实strict stdout编码失败遮住status权威表；默认Win replace控制台不作同样崩溃声明。0b061f9先14红6失败/8控制→ce9e716方案→d10e759最小显示修；仍191一类，不另Issue/PR，announce严格写入控制保持。最终相邻878passed，新56全执行，三个最终源码全检进行中，详record§242。前两处完整绿属于历史阶段，不冒充最终三处CI或本机验收。


2026-10-17 B139本机最终验收：三消费面合批相邻878passed、core2276passed+1既有skip、CPU Flow429passed，56新逐XML全执行/0新skip，0fail/error；64004/25458及所有旧本机句柄已终结。完整类别里程碑准备统一PR，191仍OPEN，远端CI尚未执行；详record§243，原始.tmp/audit139保留且不随clone。


### B140 / Issue #193：Logger单路拒绝阻断健康日志证据（2026-10-17）

实际Runner→execute→Logger，真实OS pipe reader关闭/closed stdout使可写run.log缺原原因和traceback；只读临时log使健康stdout缺完整trace。先eba0d3a红32/24失败/8控制，再3dbd9a6方案、2fa9f0a仅Logger独立双输出和完整诊断尝试；首IO对象/程序错/取消/close策略保持。相邻257、core2304passed+1既有skip、CPU Flow433passed，32新全执行，原生三反例转绿。详record§245–246；原始.tmp/audit140不随clone。待同类里程碑最终head/实际dev全部CI，193仍OPEN，不等设计审批。


2026-10-17 B140验收：PR194 finalhead26dd6d2/actualdevf2c286d全部实际CI成功；193原body保持、一次评论后关闭，旧主题本地/remote正常删，详record§247。上文待CI为历史阶段，非当前阻塞。无新开放Issue，不代表所有可能故障已排除。


### B141 / Issue #195：独立历史raw下载脚本绕过国内源（2026-10-17）

PR190库入口不覆盖手动prepare_data.py。实际prepare_mnist/cifar在传输替身处暴露S3/toronto首请求，真实urllib重定向流程配合合成响应亦可从国内起点继续请求海外。零实际网络/下载，不声称实际镜像重定向。77全状态Issue去重后195登记，80009d7红20项11失败/9控制，设计见artifact_consistency_design末B141；先复用已有domestic_endpoint并在初始/redirect传输前统一校验，缓存/hash/raw/layout/manifest schema不改。原始.tmp/audit141不随clone，正式数据/归档/报告不动，继续本地合批而非单bug PR。


### B142 / Issue #196：raw下载/解压的close IO遮首错（2026-10-17）

download/prepare_mnist/prepare_cifar真实.part部分写入+注入close OSError，三条caller均收到secondary而非原write error，原文件close/partial/peer保持。区别144普通writer/142锁退出，先登记196再bce353a红35项14失败/21控制，覆盖七owned退出×原错/取消/正常close/程序close/成功。设计见artifact_consistency_design末B142；同195路由修复在当前唯一分支累积，未推送/PR。证据.tmp/audit142不随clone，零实际网络/模型数据/GPU/训练，保持raw/layout/发布顺序。


2026-10-17 B141–B142本机检查点：195/196同分支路由9e2bcd2及owned退出4954933已相邻166passed+1既有skip/core2359passed+1既有skip/Flow433passed，55新全执行；原生反例转绿，未推送/PR，仍OPEN。继续相关读取边界复查后统一完整里程碑，不单bug PR。详record§251，临时证据不随clone；无真实下载或服务可用性声明。


B142同类读取补全/本机最终验收（2026-10-17）：196原body追加digest与四strict reader范围，首个meta夹具路径拒绝及过早范围表述已明确更正并留证；2d018de先25红10失败后84389b0只给五with复用guard。与195合批共80新全执行，191相邻+1既有skip/core2384passed+1既有skip/Flow433passed。当前尚无PR，195/196保持OPEN，详record§252–253，原反例及校验拒绝均不删。


2026-10-17 B141–B142验收：PR197 finald824e14/actualdev e8cebcb全部实际CI成功，195/196原body保持、各一次评论后关闭，旧主题本地/remote正常删；详record§254，之前待CI为历史。

### B143 / Issue #198：真实HF SDK镜像参数不约束海外transfer（2026-10-17）

实际download_model→HF snapshot/file client1.33.0/httpx0.28.1，ModelScope失败替身后mirror响应可使后续HEAD/GET访问huggingface.co，仍返回成功的合成config snapshot。国内控制3请求皆mirror，反例3中2海外；transport/repo tree合成、socket禁止，零真实网络/模型下载。79条去重后198登记，区别189初始选择/195手动urllib；原始.tmp/audit143不随clone。当前唯一OPEN，尚无测试/生产修改，先SDK兼容public transport设计和红回归，不全局关闭共享client、不扩布局/科学门。详record§255。


2026-10-17 B143本机实施进展：真实SDK重定向/native选择、eager/streaming根、spawn任务、返回后媒体解码与后台提前卸载已统一在原origin.py修复；另先红52b3f72（ModelScope显式CN endpoint）、a43405b（配置客户端子类越界）、fd90f37（退出后继承属性残留）后补全。28b107f已针对104passed、HF0.34.4兼容24passed；完整core2384passed+1既有skip，CPU Flow516passed/83新SDK全执行。198保持OPEN，未PR/双侧CI验收；旧“尚无测试/生产修改”仅为初次复现阶段记录。详record§258；未真实下载或改变产物布局。


B143 PR199首head3639ae0的真实3.10 CI拒绝：文件代理pickle state错误，两3.13矩阵通过。先方案后69ec7e4显式自身state，原失败保留；107针对性通过，最终本机/新head双侧CI仍待完成。不是可忽略的CI噪声，198未关闭；详record§259。

### B144 / Issue #200：库显式reader退出遮首错、可选reader吞取消（2026-10-17）

三strict入口（archive MD5/shared digest/index JSON）在read error/cancel后close OSError覆盖原对象；两个optional入口（aggregate JSONL/Logger binary tail）吞原KeyboardInterrupt而正常返回。20探针真实临时文件/公共入口，owned close一次，输入/peer字节或Logger旧前缀保全；错误注入不当实盘故障，原始.tmp/audit144不随clone。80全Issue去重，旧144writer/142lock/196手动script不重开。200先登记并一次正文补全Logger边界，排199验收清理后的统一下一类别；尚无portable红回归/生产修复。保optional IO回退、strict成功close拒绝和未知程序错，不改布局/输出/schema/科学门。详record§259。


2026-10-17 B143/#198已验收：PR199 finalfe84bff/actualdev1bf3c85所有实际CI成功，198原body保持、一次评论关闭，旧主题本地/remote普通清理；首3.10红与所有拒绝证据保留。详record§260，未真实下载/原生codec/科学验收。

B144/#200正式红：425cd72的31项14failed/17passed、0error/skip，五显式reader的首错/取消问题确认；当前尚未生产修复，继续归并隐式Path whole-file读同类。200唯一OPEN、唯一library-reader-exits，后续整类一个PR，详record§261。


B144整类实现fdfef59：五显式+19whole-file库读取统一owned首错/取消；原69红28失败后72针对性全通过，相邻2185passed+1既有skip/core2450passed+1既有skip。原验证、编码/错误回退、缓存与字节/布局不变；最终Flow/PR双侧CI仍待，200未验收关闭。16旧故障夹具跟随实际owned-open入口，未放宽旧断言；拒夹具/结构checker保留，详record§263。一个整类里程碑不逐点PR。
