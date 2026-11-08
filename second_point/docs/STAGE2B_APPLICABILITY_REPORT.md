# 阶段 2B：真实种子适用性报告

日期：2026-11-08（Asia/Shanghai）。本阶段只审计和评估现有
`MR_TYPEDEF_INDIRECT_STRUCT_ARG`，未实现新 MR、强化学习或一般诊断比较引擎。

**结论 1：当前实现对本次完整扫描的已有合法种子库没有可用位置，适用率为 0/50 = 0%。**
**结论 2：这批样本缺失 MR 必需的结构体函数指针成员、成员间接调用和结构体 typedef 链，
是零适用率的决定性原因；实现另外存在很强的工程限制，但单独放宽这些限制也不能使本批样本适用。**
不能据此断言所有 CsmithEdge 配置/版本/种子都永远不生成这些结构。

## 1. 阶段 2A 审计与修复

重新阅读了阶段 2A 验收报告、研究设计、MR、见证、18 项 MR 测试及冒烟工具，
并检查阶段 1 的解析、环境和种子验证接口。

### 已确认的成立部分

- 变量、成员、typedef、结构体关联使用 canonical USR、referenced、TYPE_REF 及规范类型，
  拼写仅用于校验 AST token 对应的窄源码形式和构造编辑，不用于判定类型相同或变量相同。
- ByteEdit 只替换选定 typedef 中的类型依赖 token，校验原 bytes，其他源区间不变。
- build_mutation 只解析 P0，以符号图替换推导 P1 类型；不调用编译器、不解析 P1、不读取其诊断。
- 见证包括旧/新依赖、类型声明身份、作用域、完整定义、调用/实参/成员/别名链锚点、源映射及隔离理由。
- 不同 struct 的相同字段布局不会被误判为相同类型；跨作用域同名变量/别名由身份而非名字区分。

### 发现的实际漏洞：重复 typedef 声明

阶段 2A 用 `self.typedefs = {uid: cursor}` 保存声明。相同 USR 的多个声明会被最后一处覆盖，
影响闭合检查又直接略过所有受影响 typedef 节点，未验证其声明次数。

在人工 P0 夹具中重复一行：

```c
typedef Stable ArgumentBase;
typedef Stable ArgumentBase;
```

P0 在 GCC 15.1.0、Clang 22.1.0 均返回 0。旧实现返回 APPLIED，仅改变第二处，
实际得到额外 typedef 冲突：

| 修复前真实执行 | 返回码 | error |
|---|---:|---|
| GCC P1 | 1 | 6:33 typedef 类型冲突；14:28 目标调用类型不兼容 |
| Clang P1 | 1 | 仅 6:33 typedef 重定义，未报告预期的目标调用错误 |

这证明原来“只引入目标调用约束违反”的前置检查不充分，不是编译器缺陷。
原始夹具源文本、命令、返回码和完整 stderr 保存在
`evidence/stage2b/audit_redeclaration_before_fix.json`；临时文件已清理，源文本仍完整保留。

修复：在选择依赖边时，对所有受影响别名按 USR 收集独立声明 extent，
存在多处声明则返回 `AFFECTED_TYPEDEF_REDECLARED`。这是收紧安全性，不是提高适用率。
即使某些下游重复声明在具体情况下可能无害，当前也保守拒绝，未尝试多处修改。
修复后相同 P0 仍双边合法，但扫描/变异均 NOT_APPLICABLE、不创建 P1；见
`audit_redeclaration_after_fix.json`。

### 扫描和见证报告问题

1. 旧 scan_candidates 只检查首个候选，不能代表候选位置总数。现逐一检查主文件所有 CALL_EXPR，
   按不同调用位置返回候选列表。每位置沿用原 MR 的首个安全边/替换选择，不宣称穷举所有编辑组合。
2. 旧接口将 P0_AST_ERRORS 当成 NOT_APPLICABLE。现为 PARSE_FAILED，保留 AST 诊断；
   无文件/内部异常为 SCAN_FAILED，超时为 SCAN_TIMEOUT，工作进程失败为 WORKER_FAILED。
   阶段 2A 对无效 AST 的旧测试断言随之修正，其余既有行为继续回归。
3. 见证重放原来没有交叉检查 changed_declaration 与 dependency_graph；补充该一致性、必需前置条件集、
   error/fatal 策略、锚点文件/边界和字节映射检查。篡改声明、清空前置条件、错误文件/范围、warning 策略均被拒绝。
4. 报告的初始主原因只是类别字典序第一项，不能表示因果。现使用明确的“必要结构优先”顺序，
   原始 summary 保留为 summary_initial.json；仅从不变逐种子记录重算主原因，未改变扫描结果、适用率或耗时。

重放器仍只验证受限见证的内部一致性，不是独立的完整 C11 前端。
Oracle 的“独立”指不依赖被测 GCC/Clang 对 P1 的诊断；P0 类型提取仍依赖现有 libclang，未夸大为完整形式化证明。
两个阶段 2A 的既有见证文件经加强后的重放检查仍然通过，未重写历史证据。

## 2. 样本来源、数量和选择方式

按既有 environment.json 的两个合法目录搜集：

| 目录 | .c 数量 |
|---|---:|
| data/seeds/clean | 0 |
| data/seeds/diverse/clean_diversify_syntax | 50 |

按 SHA-256 去除完全相同的文件后仍为 50 个；源文件头中的 Csmith 随机 seed 标识也有 50 个不同值。
没有将 raw、未筛选的 diversify 目录或同一生成种子的另一加工版本重复计入 100 个独立合法样本。
这是当前配置下全部可用合法种子，故不足 100 时扫描全部 50 个，没有生成新种子。

样本并非未经处理的原始 Csmith 输出：头部标识为 csmith 2.4.0，包含 `--seed ... --safe-math`；
同时含 EDGM diversify 的插入内容。阅读 `scripts/02_filter_diverse_seeds.py` 确认其合法目录来源为
双编译器筛选的 diversify_syntax 输出；本次再次逐文件双编译验证，不仅信任目录名称。
这里没有声称能从当前源码证明历史生成命令的全部执行链。

选择规则：按 SHA256 排序形成池，使用独立 `random.Random(20261108)` 打乱，取前 min(100,池大小)。
扫描本身不随机选候选，按源码 AST 顺序，固定安全边选择。每个文件保存路径、文件哈希和别名路径。
详见 `evidence/stage2b/corpus/selection.json`、`structural_aggregate.json` 的头部元数据。

## 3. 实际执行命令

工作目录：`/home/pyx/edgm-experiment`。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover \
  -s second_point/tests -v > second_point/evidence/stage2b/all_tests.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.applicability_run \
  --out-dir second_point/evidence/stage2b/corpus \
  --limit 100 --random-seed 20261108 --scan-timeout 30 --compiler-timeout 20 \
  > second_point/evidence/stage2b/corpus_stdout.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 second_point/tools/stage2b_finalize.py \
  --corpus-dir second_point/evidence/stage2b/corpus \
  > second_point/evidence/stage2b/finalize_stdout.txt
```

三条命令实际退出码均为 0。重新执行扫描时须使用新的 --out-dir，不能覆盖已有实验。
审计复现、主原因重算、选样复查和单个真实种子重扫另通过本地 Python 标准库及同一模块接口执行，
结果分别保存于 audit_*.json、summary_initial.json 和 reproducibility_check.json；
未进行第二次全库扫描。

每个 P0 的编译 argv 为实际绝对路径：

```text
/opt/gcc-15.1.0/bin/gcc -std=c11 -fsyntax-only -w -I /home/pyx/edgm-experiment/src/csmithedge/csmith/runtime <原始P0绝对路径>
/opt/llvm-22.1.0/bin/clang-22 -std=c11 -fsyntax-only -w -I /home/pyx/edgm-experiment/src/csmithedge/csmith/runtime <原始P0绝对路径>
```

100 次真实编译检查均记录在逐种子 validation.checks 中，包括返回码、耗时、stdout/stderr 和完整命令。
AST 参数仍为现有 `-x c -std=c11 -w -I <runtime>`；未更改环境配置或 runtime。

环境实测为 GCC 15.1.0、Clang 22.1.0、默认 libclang 18.1.3。
50 个 AST 均成功，正向小夹具仍能识别函数指针成员和类型链，未观察到“旧 libclang 无法识别该结构”
导致的假零候选。不能据此证明两个版本对所有 C11/扩展语义完全相同；本次没有升级任何组件。

## 4. 扫描接口、隔离和状态定义

`scan_candidates(path, runtime)` 只读取源文件，返回完整候选位置、AST 结构清单和拒绝原因。
`applicability.py` 在主文件 AST 上收集 struct/union 定义、函数指针成员、成员间接调用、
typedef 声明身份/链/规范根类型；不是搜索源代码关键词。
候选仍由同一个 Analysis.candidate 实现判断，没有放宽布局、初始化、引用闭合或预处理限制。

过滤复用 main_file_nodes：TU 无 file 仍展开，其他节点按完整 resolve 后路径排除头文件子树。
仅源文件中的 typedef/成员算入清单，runtime 中的 uint32_t 定义不冒充主文件候选。
TYPE_REF 的 referenced 可提供终端类型身份，但头文件声明节点本身不加入种子节点数。
所有候选锚点属于主文件；头文件含完整阳性夹具而主文件无候选的测试也明确返回零。

每个 AST 扫描在单独 Python 工作进程中运行，硬时限 30 秒；没有用无法可靠中止 libclang 的线程超时。
完成 AST 解析/索引后发送 ast_ready 里程碑。超时保留部分输出并区分 parse_or_ast_index 与 candidate_scan；
不伪称未知阶段已经解析成功。进程非零、信号终止、输出协议异常独立为 WORKER_FAILED。

有效分母只包含：P0 双边合法、源哈希不变、AST=OK、扫描完整返回 APPLICABLE/NOT_APPLICABLE。
所有失败种子的 candidate_count 为 null 而不是虚构 0；其失败计数单列，不进入适用率分母。
零候选、正常不适用属于成功完成实验，命令正常退出。

## 5. 总体结果

| 指标 | 真实结果 |
|---|---:|
| 请求上限 | 100 |
| 实际不同种子 | 50 |
| 双编译器合法 | 50 |
| AST 成功 | 50 |
| 有效完成扫描 | 50 |
| 至少一个候选的种子 | 0 |
| 候选调用位置总数 | 0 |
| MR 适用率 | **0/50 = 0%** |
| P0 拒绝/验证异常 | 0 / 0 |
| 解析失败/扫描异常/超时/工作进程失败 | 0 / 0 / 0 / 0 |
| 源文件变化 | 0 |
| 真实种子 P1 变异验证数 | 0 |

机器可读总表：`corpus/summary.json`、`summary.csv`。
逐种子全记录：`corpus/records.json`、`records.csv` 和 `seed_001.json` 至 `seed_050.json`。
每行包含 seed_id、SHA256、准入、AST 状态/节点数、候选列表/数量/链长度、类型与函数指针关系、
状态/原因及阶段耗时。候选为零时列表为空，不伪造不存在的类型或位置。

没有真实候选，因此不提供“成功的真实 P0/P1 对”，也没有启动实际真实种子编译变异分支。
`real_candidate_validations.json` 为 `[]`。最多三种子的条件分支仅以明确标识的小夹具测试了上限及证据保存，
不将这些人工回归样例算入真实适用率。

## 6. AST 结构和不适用原因分布

累计扫描 1,490,981 个主文件 AST 节点。清单包括：

| 结构 | 数量/分布 |
|---|---:|
| struct 完整定义 | 30，分布在 15 个种子 |
| 至少两个不同完整 struct 的种子 | 10 |
| union 完整定义 | 28 |
| typedef 声明 | 1,301 |
| 规范根为整数的 typedef | 1,251 |
| 规范根为枚举的 typedef | 50 |
| 链长度 1/2/3 的 typedef 声明 | 467 / 417 / 417 |
| 根为 struct 且链长度至少 2 | **0** |
| 结构体函数指针成员 | **0** |
| 对应成员间接调用 | **0** |
| 当前受限布局模型接受的 struct | 0 |

30 个 struct 合计 194 个字段，其中 123 个位域；还存在不同整数成员类型和嵌套记录。
因此当前“直接、非位域、无限定的 int 字段”限制确实排除了现有结构体。
但另外三个必要语义结构均为零，并不能把零适用率仅归咎于这个布局限制。

不适用原因采用**每种子多标签**统计；同一种子可以同时缺少多个前置条件，不能相加当种子总数：

| AST 依据的原因 | 种子数 |
|---|---:|
| NO_STRUCT_FUNCTION_POINTER_MEMBER | 50 |
| NO_MEMBER_INDIRECT_CALL | 50 |
| NO_RELEVANT_STRUCT_TYPEDEF_CHAIN | 50 |
| NO_DISTINCT_COMPLETE_STRUCT_PAIR | 40 |
| CURRENT_LAYOUT_RESTRICTION | 50 |

主原因采用报告公开的结构必要性优先顺序，50 个种子的主原因为不存在结构体函数指针成员。
没有证据的类别，例如多处引用不安全或找不到可解析身份，不因为“可能发生”就计入本库失败原因。

调用级的原 MR 短路检查共拒绝 20,137 个调用：REQUIRES_ONE_ARGUMENT 19,640 次，
NOT_MEMBER_INDIRECT_CALL 497 次。它们是调用级首个失败门，不是 20,137 个独立种子原因，
也不能据此断言“如果放宽单实参限制就可用”。独立 AST 结构清单已经确认没有目标函数指针成员。

## 7. 是生成器结构问题，还是 MR 太苛刻？

本批零候选的直接证据是必要结构缺失。已有长 typedef 链全部落在整数/枚举，
不能把“有 typedef 链”当成“有结构体实参依赖链”。缺少函数指针成员意味着即使支持更多布局、参数或初始化，
也没有这条 MR 要操作的原始调用。

辅助阅读的本地源码与这个现象一致：

- `src/csmithedge/csmith/src/Type.h:59` 的普通随机类型模型包含 simple/pointer/union/struct。
- `Type.cpp:1752` 的 Type::Output 按这四类输出，没有在该路径构造函数原型类型。
- `FunctionInvocationUser.cpp:442` 的普通用户调用输出函数 name/alias_name 后跟实参，是直接调用路径。
- EDGM 的 `diversify_syntax.py` 中 _BASE_TYPES 是整数类型列表，_gen_global_typedef/_gen_stmt_local_typedef
  添加的是这些类型的别名链，与 AST 中 1,251 个整数别名的观测相符。

这些是对本地已查看实现和当前样本的有限解释，不证明所有 CsmithEdge 扩展、历史构建或选项都绝不支持函数指针。
本次没有运行生成器，也未把关键词搜索结果替代 AST 扫描结论。

方法层面，本 MR 同时要求“别名链 + 两个结构体 + 回调成员 + 按值间接调用 + 安全隔离变更”，
语义组合本身就较专门。若目标种子来源长期不提供回调成员，说明 MR 与该来源不匹配，
而不是增加强化学习预算就能解决。当前最小样例成功只能证明构造机制，不证明真实 CsmithEdge 适用性。

## 8. 正确性必要条件与工程限制

必须保留或以等价证明替代的条件：实际声明身份/类型兼容关系可靠，目标结构体完整且不兼容，
调用有可检查的原型，形参路径不随实参路径一起改变，依赖影响范围闭合，
变更不引入别名重声明/可见性/初始化等额外约束冲突，主文件字节编辑与锚点精确，
Oracle 在 P1 编译前确定。这些不应为适用率直接删除。

当前可研究推广的保守选择：仅直接 int 字段、禁止位域/嵌套记录、只许一个参数和 int 返回值、
只许一个局部变量和一次引用、严格 `{0}` 初始化、只支持点成员调用、
发现任意预处理指令即拒绝、遇到所有受影响别名多处声明即整体拒绝。
其中全文件预处理禁令对含 csmith.h 的输入也很严格；今后可研究只针对编辑/调用区域证明无宏映射问题，
不能简单移除检查。多引用、多声明推广也需要逐位置独立验证和一致源映射。

**本阶段未实现这些推广。即使将上述工程限制全部放开，本批样本仍因没有函数指针成员而无适用点。**

## 9. 性能和可复现性

完整 50 种子批次 wall time 为 **168.75 秒**：

| 阶段 | 累计秒 |
|---|---:|
| 双编译准入 | 5.73 |
| 隔离工作进程（含其内部阶段） | 160.66 |
| libclang parse | 3.48 |
| 主文件遍历/过滤/游标物化 | **122.10** |
| 类型/别名索引 | 10.29 |
| 结构清单 | 2.95 |
| 候选遍历及拒绝记录 | 16.13 |

工作进程和其内部阶段是包含关系，不能相加计算总时间。
主文件遍历阶段约占完整批次 72.4%，为实测主要瓶颈；其中涉及 libclang 游标访问和逐节点完整路径过滤，
没有进一步微基准把全部耗时精确归因于某一个 API。未擅自为性能重构路径配置。
独立进程启动、协议序列化和证据写入还有额外开销。

固定配置重新生成的选择列表与已保存的 50 项逐项相等；另外只对首个真实种子重复扫描一次，
去掉计时和进程转录字段后的结果逐项一致。见 `reproducibility_check.json`。
完整语义结果 SHA256 为 `6b7a33858699eb82e86cfbf598ac0480ce3e0d562e5432acf48540c540bf3446`。
重复扫描不被加进分母，不声称执行了第二次全库实验。计时本身自然不会逐次相等。

## 10. 测试、项目保护与产物

最终执行 **46 项 unittest，全部通过，无 skip，耗时 1.973 秒**，见 `evidence/stage2b/all_tests.txt`。
包括原有 29 项（无效 AST 状态断言按要求修正）和新增 17 项：

| 新增测试覆盖 | 结果 |
|---|---|
| 两条独立 typedef 分支的多个调用位置完整枚举，锚点属于主文件 | PASS |
| 四层 typedef 链 | PASS |
| 重复兼容 typedef 的额外错误漏洞被拒绝 | PASS |
| 同名变量在不同块作用域不串联 | PASS |
| 局部同名 typedef 遮蔽不串联到全局别名 | PASS |
| 共享别名影响额外使用点时拒绝 | PASS |
| 只读扫描前后 bytes 一致、重复结果相同 | PASS |
| 固定随机选样及内容哈希去重 | PASS |
| 实际 AST error 和内部异常分别记录 | PASS |
| 模拟候选阶段超时保留 ast_ready、排除统计分母 | PASS |
| 实际极短进程时限与模拟崩溃均不伪装不适用 | PASS |
| 头文件中的阳性结构不计入主文件候选 | PASS |
| 零候选正常退出、CSV 存在、无变异、结构优先原因 | PASS |
| 验证失败不进入适用率分母 | PASS |
| 篡改声明/前置条件/文件/范围/严重性策略被拒绝 | PASS |
| 夹具上的条件验证分支保持输入和预先生成见证 | PASS |
| 四个不同人工夹具最多执行三个验证 | PASS |

超时部分同时有真实极短 deadline 测试和可控模拟里程碑测试；崩溃为模拟，
不声称本批实际遇到编译器或 libclang 崩溃。人工夹具不计入真实种子统计。

新增模块：`applicability.py`、`applicability_run.py`；新增汇总工具 `tools/stage2b_finalize.py`、
测试 `test_stage2b_applicability.py`、本报告和 `evidence/stage2b/`。
现有 MR 仅增加重复声明安全检查、明确 AST 异常和扫描统计接口；见证增加一致性检查。
没有为当前种子放宽前置条件，没有新 MR 或语义模板注入。

`readonly_check.json` 复核旧代码/配置/测试/种子 468 个文件无改变、删除或新增，
50 个输入哈希全不变；stage1 环境模块、种子模块、AST 模块、环境配置和原基础测试
与阶段 2A 的实现 manifest 相同。保留原有 git 删除记录和未跟踪 backups，不把它们算成本次变更。

## 11. 后续方向和明确停止点

当前 MR 值得作为独立 Oracle/依赖图/精确源映射的正负例基准保留，
**不宜以它作为当前 CsmithEdge 种子池后续大规模缺陷实验的唯一主 MR**。
优先设计贴合实际数据的另一条深层关系，比单纯放宽本条限制更有价值。

本库实测含大量整数别名链、位域/限定字段及普通调用。建议下一阶段先做小规模 AST 可行性分析，
考虑跨作用域别名限定符传播与明确写入位置间的约束，或既有结构体/普通调用的跨声明类型依赖。
必须与 EDGM 的正则加 const/交换实参算子区分，通过声明身份和依赖闭合证明单一目标约束；
这些只是建议，当前没有实现，也没有声称其适用率已验证。
若要继续当前回调 MR，应另行评估原本就含回调/结构体接口的合法真实语料，
不能向任意 Csmith 程序补固定模板后宣传为自然适用。

对强化学习的直接启示是先检查结构可达性和非空候选动作集；本 MR 在所有样本上的动作掩码均为空，
不存在可学习的有效位置选择。将解析失败当低收益、将不适用当可变异位置，都会污染状态和反馈。
未来状态可记录实际别名根种类/链长度、声明引用闭合、作用域和字段特征，但应先有有效 MR/Oracle，
不能用本阶段零候选数据启动训练。

阶段 2B 已完成，到此停止；没有新 MR、RL、模板注入、大规模模糊测试或原 EDGM 修改。
