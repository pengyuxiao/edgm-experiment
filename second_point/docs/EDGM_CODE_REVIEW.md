# EDGM 代码阅读报告（阶段 0）

检查日期：2026-11-08（Asia/Shanghai）。仓库：`/home/pyx/edgm-experiment`。
本报告描述本次工作区的代码，不能外推为历史全部实验的行为或结果。
新增工程位于 `second_point/`，旧代码未导入、未执行变异/生成/在线奖励流程。

## 1. 布局、入口和阅读范围

Python 模块在 `src/edgm`，没有仓库顶层 pyproject；脚本通过插入 `ROOT/src` 导入。
`scripts/01_gen_seeds.sh`、`02_filter_seeds.py` 分别负责生成和筛选；
`60_run_arm_one.py` 适配单算子，`70_multi_mutate_one.py` 串联算子；
`80_chain_big_run.sh` 是当前 IDE 中的旧链式入口；`92_corrected_eidetic.py` 是独立在线基线入口。
`48_compile_diff_errors_norm.py` 做跨编译器诊断差分，`83_compile_diff_opt_errors_norm.py` 做同编译器优化级差分。
`49_review_inconsistent.py` 是另一个签名视角的审阅工具。

重点检查了上述文件、`src/edgm/ast/{parse,ast_utils,rewrite}.py`、
`src/edgm/selection_policies.py`、`config/diag_norm.yaml`，以及辅助的
`52_ts_state.py`、`62_pick_arms.py`、`81_process_final_update_ts.py`、`src/edgm/paths.py`。
代表性语义算子阅读范围：`param_delete.py`、`field_delete.py`、
`swap_call_args_mismatch.py`、`insert_duplicate_switch_case.py`、
`insert_typedef_chain_proto_conflict.py`、`add_const_keep_writes_adv.py`、
`insert_switch_goto_vla_adv.py`。没有逐个验证全部算子，不把代表性观察写成全部算子的结论。

相关测试重点阅读 `tests/test_corrected_baseline.py`、`test_selection_comparison.py`、
`test_corrected_observability.py`、`test_policy_reporting.py`，以及 `test_corrected_v2.py` 的
奖励参照集、完整分块和元数据视图测试；其余奖励测试检查了测试入口及覆盖主题。
旧测试没有执行，避免引入旧实验路径写入或 API 行为；本次实际执行的是新工程测试。

初始 git 状态已有 `res`、`test_asan_*`、`test_clang`、`test_gcc` 等删除记录及未跟踪 `backups/`。
这些不是本次改动。原文件内容快照见 `evidence/original_hashes.json`，复核见 `evidence/readonly_check.json`。

## 2. 种子生成与合法性筛选

`01` 使用固定 CsmithEdge 可执行路径
`/home/pyx/edgm-experiment/src/csmithedge/csmith/build/src/csmith`。
每次从 `/dev/urandom` 读取 32 位数，显式传 `--seed`；程序写到 raw，
meta JSON 保存 seed_id/csmith_seed。编号从 0001 重启，重跑可能覆盖旧生成文件，不能在本次调用。

`02` 按文件名排序读取 raw，分别调用 PATH 中 gcc/clang，参数是
`-std=c11 -I<固定 runtime> -fsyntax-only`（此文件没有 `-w`），以两个返回码均为零作为筛选条件，
`copy2` 到 clean。没有超时、逐种子结构化诊断日志、版本锁定或独立语言合法性证明。
因此“clean”是双编译器接受条件，不意味着已证明无 UB 或标准完全符合。

本次实测 clean 目录 0 个 C 文件；`diverse/clean_diversify_syntax` 有 50 个，raw 有 50 个。
新框架选择前者中排序第一的一个重新验证，不把目录名当作可信验证结论。
没有新生成 CsmithEdge 种子。

## 3. AST 解析、遍历与源码位置

`parse.py` 返回 TranslationUnit，固定 c11/runtime，打印诊断但错误也不阻止返回。
`ast_utils.py` 有另一份解析逻辑（文档字符串称根节点，实际仍返回 TU）；
收集函数、for/while/if、调用、变量声明。访问 TU 时仍遍历孩子，避免根无 file 导致整棵树被过滤。
其中 `_is_from_main_file` 只比较 basename：不同目录同名文件可能误入。

`rewrite.py` 的 SourceRewriter 使用 bytes，按半开区间保存替换，排序后检查边界/重叠，再拼接写出。
这是值得继承的字节坐标约定；但调用方仍要验证主文件、宏位置和输出隔离。
`field_delete`、`param_delete`、实参交换中的若干定位函数把 clang 的行列直接换成 Python 字符索引，
非 ASCII 文本下不能保证与字节列一致。

新工程单独建立 AST snapshot，保留 TU 遍历、按 resolve 后完整路径过滤头文件，
从原始 bytes 和 location.offset 计算物理行/字节列；顶层深度为 1，TU 为 0。
它拒绝有 error/fatal 的 AST，不把部分树当成有效 P0 的上下文。
本阶段只提取基本节点与父子关系，没有跨节点依赖图或宏重写能力。

## 4. 代表性语义算子的实际技术边界

| 算子 | 真实 AST 使用 | 源码操作和边界 |
|---|---|---|
| param_delete | 函数定义、USR、CALL_EXPR.referenced；从 func_1 做可达分析 | 找有参数的可达函数，跨同 USR 声明/定义删形参，调用保持；行列转字符串位置有编码风险 |
| field_delete | FIELD_DECL、MEMBER_REF_EXPR/MEMBER_REF、semantic_parent、表达式类型拼写 | 通过 `记录名::成员名` 字符串关联，不是完整引用身份；extent 转文本位置删除 |
| swap_call_args_mismatch | CALL_EXPR、arguments、canonical type 的 scalar/pointer/record 粗分类 | 随机交换不同类别实参；使用 c99 解析而非统一 c11；仅有粗分类不足以证明精确约束 |
| insert_duplicate_switch_case | SWITCH_STMT、CASE_STMT、DEFAULT_STMT 和 token | 用 SourceRewriter 插入重复标签；收集标签时递归进入嵌套 switch，没有按最近 switch 隔离归属 |
| insert_typedef_chain_proto_conflict | 无 libclang 调用 | 正则找单行 static 原型，注入 typedef/宏/typeof 与返回函数、符号种类或参数冲突 |
| add_const_keep_writes_adv | 无 libclang 调用 | 正则/括号计数找 Csmith 风格函数和声明，统计名字对应写入，按分数选候选；不解析声明身份、遮蔽、跨作用域限定符传播 |
| insert_switch_goto_vla_adv | 无 libclang 调用 | 正则/字符括号匹配找 switch 或函数体，注入 goto/VLA 和嵌套 switch；还可能加非法 continue 等多个约束违反 |

这里的“advanced”命名不能作为 AST 精度或 Oracle 正确性的证据。
另外两个自行构造 runtime 路径的 AST 算子使用 `parents[2]` 后追加 `src/...`，
从该文件实际目录推算会落在 `src/edgm/src/...`，并非项目 runtime。
这是静态路径推导发现，本次没有执行这些算子来确认其影响，也没有修改原代码。

上述算子返回 bool 或写出文件，并未在生成时给出独立的精确语言约束 Oracle。
“编译器确实报错”只能作为后验筛选，不能成为第二点的独立预言。

## 5. 诊断解析、规格化和比较的遗漏

`48` 在固定 c11/syntax-only/-w 下，GCC 请求 JSON，Clang 请求 SARIF，
LC_ALL=C；提取第一个 JSON 值以容许尾随总结文本；仅保留 error/fatal。
结构记录 category/sev/file/line/col/code；GCC 取首 location 的 caret，
Clang SARIF 取首 physicalLocation.region 的 startLine/startColumn。
GCC 没有递归读取 children 诊断；源位置单位差异也没有统一证明。

配置按 compiler/field/regex 顺序首匹配映射，默认 unknown，包含消息正则与若干 Clang 数字 ruleId。
加载器是手写 YAML 子集，缺失配置回退 unknown，非法正则被跳过。
这些映射依赖诊断措辞和版本，不能充当与被测编译器独立的语言 Oracle。

`same_errors_doctor` 的实际行为：

- 丢弃无行号诊断；按行聚合类别 set，丢失数量、列、文件、严重性和 code。
- 只要求最早报错行相等，并检查较小行集合在较大集合中存在且类别有交集；多出的行/类别可以被忽略。
- 任意一侧该行含 unknown 就跳过类别检查。
- 两边行集合均空直接 True。即便主流程统计了 json_fail，两个非零返回码配合解析空结果仍可进入 both_err_consistent。
- `--match-col` 仅为 legacy 参数，比较器未使用它。

`49` 的 Counter 签名是 category+sev+line，能看到数量差异，但仍忽略 col/file/code，
并只收集两边都有错误的案例。它和 `48` 的一致性标准不相同。
`83` 复用了相同弱比较结构，但实际参数是 `-std=c11 -w -c -O...`，不是 syntax-only，
且没有指定独立对象输出路径；不能直接作为第二点严格检查入口。

这些是框架比较逻辑缺口，不是本次发现或确认的编译器 bug。
第二点需要从 Oracle 的指定字段精确匹配，unknown/解析失败必须成为不可判定或检查失败，不能算匹配。

## 6. 变异执行、策略和反馈

`60` 优先从 wrapper import 确定入口，否则按函数名猜测；普通分支对返回 None 的老算子适配较弱。
strict 分支拒绝已有输出，要求非空且内容改变，支持 bool/None/带 ok 对象合同，
可传 mutation_seed，并写元数据；require-error 的检查仍依靠 gcc/clang 返回码，不是独立 Oracle。
`random.seed` 不能控制算子自己 `random.Random()` 的无参新实例，不能宣称所有旧算子完全可复现。

`70` 普通模式允许部分链，strict/require-complete 使用 UUID 目录、记录 attempted/applied，
任一步失败不接受最终链。每步种子是 mutation_seed+i。
`80` 使用 shell RANDOM 选择 1–3 个算子、调用 `62` 默认 bootstrap_legacy；
它会清理 final，并加 EDGM_APPLIED_ARMS 头注释，所以本次不能运行这个入口。
阅读 `81` 发现当前实现生成奖励 JSON，却没有调用 `52` 更新状态，尽管脚本名和 `80` 注释称 update TS。
因此不能仅根据该入口文字断言这个旧链流程完成了在线 TS 更新。

`52` 保留滑动 history 的 Beta 采样/更新和 corrected Beta 状态两套路径。
`62` 默认使用 bootstrap 历史重采样均值，带 explore 与空臂优先；显式 beta_ts 才调用 corrected Beta。
`selection_policies.py` 的 beta-ts 调用 `52` 的 beta_topk/beta_update：
初始 alpha=beta=1，采样前 k；chosen 中每个算子 alpha+=reward/len(chosen)，beta+=1-share。
discounted-ts 对全部臂的先验以外证据按 gamma 衰减，random 只记更新次数。
这些状态都是算子级，没有 AST 上下文、位置动作或语义依赖特征。

`92` 每次选择前重载状态，随机选择种子，严格串联变异，通过双编译器检查（至少一个非零）后才计算嵌入多样性奖励，
使用 write-ahead PENDING_COMMIT、原子状态保存及归档追加。反馈对象仍是算子，不是 AST 位置。
它有 legacy/split-v1 RNG、seed/policy/mutation 流分离、配置/源码/种子哈希、编译器身份、
events、诊断流文件、运行总结和 API 用量限制等较强可观测性。
其错误 marker 解析失败虽有记录，但 mutant 接受条件实际仍使用返回码非零；不能等同标准约束验证。
部分编译检查发生在加头注释之前，之后源码行号会改变，也不适合直接作为第二点定位证据。

## 7. 旧测试覆盖与复用决策

baseline 测试覆盖 Beta 分摊、奖励退化、提交 marker、严格返回合同、旧输出拒绝、部分链拒绝和 mock 在线更新；
selection comparison 覆盖遗忘先验证据、抽样复现、三策略隔离；
observability 用模拟进程检查原始输出保存、非零无 error marker、超时及 API 失败记录；
v2 和 reporting 检查完整分块、视图去除算子元数据、覆盖率/熵等。
在检查范围内没有看到严格 Oracle 的行列类别比较、主文件同名头过滤或 UTF-8 字节定位的专门测试。

| 处理方式 | 内容 |
|---|---|
| 可复用实验资产 | 现有种子、固定 runtime、现有编译器；借鉴不可覆盖输出、manifest/hash、原始诊断、独立随机流 |
| 需要扩展 | 以 AST 节点 ID/源哈希稳定关联位置；作用域/类型/引用/控制流关系；位置与编译器坐标单位的明确映射 |
| 必须重新设计 | MR 前置条件及单约束证明、独立 Oracle、严格诊断判定、缺陷候选分类、基于上下文的种子/位置学习 |
| 本阶段实现 | 独立环境探测、只读 P0 验证、基本 AST snapshot；不直接 import 旧模块，避免打印副作用和弱错误合同 |

## 8. 当前环境与实际边界

实际编译器为 `/opt/gcc-15.1.0/bin/gcc` 15.1.0、
`/opt/llvm-22.1.0/bin/clang`（resolve 为 clang-22）22.1.0。
固定 runtime 的 csmith.h 存在。系统 Python 3.12 的 clang 绑定可加载；仓库 venv 缺该模块。
默认实际加载 `/usr/lib/x86_64-linux-gnu/libclang-18.so.18`，版本 Ubuntu clang 18.1.3；
这与被测 Clang 22.1.0 是不同组件。未修改绑定、库搜索路径或安装环境。
阶段 1 能解析现有样本；未来需要先评估 AST 版本对类型/扩展支持的影响，不能假定它等于 22。

验收命令、逐项结果及只读复核见 `STAGE1_VALIDATION.md` 与 evidence。
