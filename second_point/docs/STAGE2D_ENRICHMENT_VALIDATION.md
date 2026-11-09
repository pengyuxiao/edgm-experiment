# 阶段 2D：上下文感知合法种子预处理验证

日期：2026-11-09，Asia/Shanghai。本阶段只实现原生关系的只读安全筛选和一种合法 P0 预处理。
**预处理属于 Seed Preprocessing，不是独立的 MR、强化学习或论文方法创新。**
未生成新的 P1、未实现新 MR/Oracle 比较器、未训练模型、未生成新 Csmith 程序。

## 1. 结论与计数边界

| 指标 | 实际结果 |
|---|---:|
| 固定原始种子数 / 合法准入回执且 SHA 一致 | 50 / 50 |
| 主文件 AST 节点 | 1,490,981 |
| 正常完成原生扫描 | 50 |
| 解析失败 / 超时 / 工作进程异常 | 0 / 0 / 0 |
| 原始成员取地址关系 | 2,410，分布在 19 个种子 |
| 取地址涉及的独立主文件字段 | 69 |
| 当前完整检查子集确认的安全原生字段 | **0** |
| 当前确认原生候选的种子比例 | **0/50 = 0%** |
| 当前子集原生候选不足（零）的种子 | 50 |
| 固定选取的真实预处理种子 | 5 |
| 可证明满足本预处理条件 / 实际增强 | 5 / 5 |
| 原始与增强 P0 均双编译器合法 | **5/5** |
| 新增成员→取地址→解引用→原运算关系 | 5，每种子 1 条 |
| 真实种子预处理失败 | 0 |
| 整个小规模实验 wall time | 227.648 秒 |

**原生的 0 是保守实现能完成证明的数量，不是穷尽 C11 后证明绝对不存在安全字段。**
记录联合体、指针/数组传播、全局对象或 sizeof 后直接判成“该语言关系不存在”是不正确的。
这些现有关系被保留在逐字段记录中，等待有能力闭合影响范围时进一步验证；没有丢弃或用增强数替换。
增强后 5 条新关系不计入上述原始天然比例，也不等于后续 MR 已成功。

## 2. 复用、样本与环境

复核阶段 2C 画像、候选设计和阶段 2B 报告，以及共享 AST、ByteEdit 和候选扫描实现。
使用 `evidence/stage2b/corpus/records.json` 原有 50 项顺序、路径、SHA 和双编译器合法性回执。
样本目录为 `data/seeds/diverse/clean_diversify_syntax`，是已有 CsmithEdge 基础程序经 EDGM diversify 后的合法库，
并非本次生成或未经加工的生成器分布。原生扫描逐一重解析并核对 SHA；没有无必要地再重复 100 次原生编译。
真实预处理严格选清单前五项，不按成功率重选；没有第六个真实种子的尝试。

实际环境复测：GCC 15.1.0、Clang 22.1.0、libclang 18.1.3，全部可用。
原 runtime `/home/pyx/edgm-experiment/src/csmithedge/csmith/runtime` 保持。
共享解析器只增加默认关闭的 `detailed_processing` 参数；既有调用行为不变。
本阶段开启 libclang 预处理记录以识别真实宏调用区间，没有另外实现 C 解析器。
`main_file_nodes` 仍遍历 TU、按完整物理路径排除头文件子树；不把头文件定义算成可编辑字段/参数。
预处理指令另供宏安全性检查，扣除后主文件节点数与阶段 2B/2C 完全相同。
没有通过升级 libclang 来解决问题；本次未观察到版本差异造成解析失败或新结构关系缺失。

## 3. 原生位域地址关系的完整检查子集

模块：`src/edgm_second/native_bitfield_candidates.py`，接口 `scan_native(path, runtime)`，只读。
候选单位是一次字段声明编辑，所有受影响地址表达式保存在同一个候选中，不按地址次数重复计数。
通过 `MEMBER_REF_EXPR.referenced` 和 canonical USR 连接字段，不按 `f1` 等名字合并。

当前接受必须同时满足：

1. 主文件完整 struct 中普通 int/unsigned int/_Bool 字段，无 const/volatile，原先不是位域。
   宽度 1 在这些类型上可合法表示；字段类型由规范类型取得，不以拼写不同判断类型关系。
2. 声明 token、extent 和 bytes 一致，无宏区间重叠；支持明确的普通基本类型声明器和独立分号。
   当前不支持 typedef 拼写、属性、多声明器、联合体及记录重声明。
3. 同一记录的其他字段也在简单整数、非位域、非 volatile 子集中。
4. 记录类型的所有主文件 TYPE_REF 只通向普通自动局部对象，不经指针、数组、typedef、形参、返回类型或外部声明传播。
5. 所有此类对象只允许 `{0}` 初始化；对象的所有引用必须直接作为成员访问基对象，不发生记录值/地址逃逸。
6. 按字段身份枚举所有成员使用；只允许明确识别的普通运算、赋值、读写、调用值和取地址上下文。
   所有目标成员基对象必须属于已闭合对象集。未知上下文、宏映射不可靠均拒绝。
7. 当前对全文件 sizeof/_Alignof/_Alignas/offsetof/静态断言/属性/pragma/asm 使用保守布局否决。
   即使 sizeof 与目标无关也拒绝，是有意保守的工程选择，不是必要的 C11 限制。
8. 定义之后继续包含头文件时拒绝，避免头文件过滤掩盖对主文件记录的使用。

在这个有限子集中，初始化保持合法、字段声明可精确修改、影响范围闭合，未来所有非法取地址点都可预先列出。
本阶段没有执行字段改位域，也没有验证其 P1 编译诊断。人工正例证明检查器并非一律拒绝：
`struct A { int x; };` 配合局部 `struct A a={0};` 和两个 `&a.x` 返回一个字段候选、两个地址锚点。

### 真实拒绝依据

69 个独立字段全部保存其位置、类型、记录 USR、对象位置、全部地址锚点和多标签拒绝原因。
其中 50 个字段不满足普通 int/unsigned int/_Bool 且无限定的目标类型子集；另 19 个仍有其他拒绝依据。
以下是字段数 / 种子数，多标签重叠，不能相加作分母：

| 原因 | 字段数 | 种子数 |
|---|---:|---:|
| 目标字段类型/限定符不在子集 | 50 | 17 |
| 声明器不在当前基本类型拼写子集 | 69 | 19 |
| 文件有保守布局否决构造 | 69 | 19 |
| 指针/数组等记录派生使用 | 69 | 19 |
| 成员基对象不属于闭合局部对象集 | 69 | 19 |
| 存在非自动局部记录对象 | 62 | 19 |
| 其他字段超出简单记录子集 | 66 | 18 |
| 记录类型逃逸到局部对象声明以外 | 37 | 15 |
| 联合体或不支持记录 | 29 | 11 |
| 初始化不在 `{0}` 子集 | 25 | 12 |
| 记录值逃逸/非成员使用 | 24 | 11 |
| 未分类的字段使用 | 18 | 13 |

31 个种子根本没有该取地址关系；19 个有入口的种子均被当前安全子集拒绝。
例如阶段 2C 的 `seed_0004_1594631173.c` 中 U1::f1 是规范 INT，但位于 union，
并有全局/数组/指针关系；不能只依据 INT 和一个 `&` 宣布可用。
`SINGLE_PLAIN_DECLARATOR_UNPROVEN` 还包含 typedef 拼写限制，并不意味着真实声明都有语法歧义。
需要的语言性质是合法位域类型、精确编辑和全部影响闭合；禁止 typedef、union、全局/数组对象、
非零初始化和不相关 sizeof 都是可由更强证明替代的工程限制。
因此目前不能把零结果解释为原生 MR 已无价值，更不能强行扩大候选数。

## 4. 唯一的合法预处理机制

模块：`src/edgm_second/seed_enrichment.py`。
`plan_enrichment` 只读生成编辑计划和语义保持依据；`enrich_seed` 将结果独占创建到新路径，随后解析增强 P0 验证关系。
输出状态 `ENRICHED_UNVALIDATED` 表示尚未做目标编译器检查，实验记录中的 `enhanced_validation` 单独报告真实合法性，
不会仅因构造成功就宣称双编译通过。

首版只选已定义普通函数的、规范类型为无限定 int 的按值参数：参数在入口已经初始化。
要求该参数所有引用都是可证明的读，不能取其地址、写入、递增或隐藏在宏中。
这比“初始化过某个局部变量”更保守，避免先实现 reaching-definitions 和完整别名分析。
候选读取必须直接参与原有二元算术/比较运算；整条祖先链排除 sizeof、_Generic、数组界限等未支持上下文。
参数声明/读取/父运算须具有真实 token 与字节位置；函数体内有预处理指令时拒绝，防止头文件隐藏参数写入。
有多个读取时按源偏移选择第一项；没有安全位置则 `ENRICHMENT_REJECTED`，解析失败为 `PARSE_FAILED`，异常为 `SCAN_FAILED`。

实际最小样例只把 `input` 这一个 AST 引用节点替换为：

```c
(*(&((struct edgm_sp_412843c06266_0 {
    int edgm_sp_412843c06266_0_value;
}){ .edgm_sp_412843c06266_0_value = (input)
}).edgm_sp_412843c06266_0_value))
```

原来 `return (input + 3) * 2;` 的其他 bytes 不变。
这段结构位于原表达式的求值路径，初始化数据来自原参数，结果返回原有计算，没有单独拼接测试函数。
标签和成员名依据 SHA 和确定性计数产生，并与主文件声明/token 及包括头文件在内的宏名集合检查冲突。
不迁移原声明，不改变原变量可见性；只引入无冲突的局部新标签和该标签下的新成员。

### 语义保持的独立说明

依据 [WG14 C11 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)
6.5.2.5（复合字面量）、6.7.9（初始化）、6.5.2.3（成员）、6.5.3.2（取地址/间接寻址）、6.3.2.1（左值转换）：

- 原始使用是一个普通、非 volatile、已初始化 int 参数的值读取。
- 新复合字面量在原块作用域拥有自动存储期；其唯一 int 成员从原读取初始化，不发生窄化。
  同一块作用域内复合字面量对象可重复初始化，不声称每次循环都产生不同地址的对象。
- 原参数在实际求值路径上仍只读取一次。短路/条件上下文仍由原有外层表达式控制；没有把读取提前。
- 普通成员的地址只在本表达式内产生并立即解引用，不逃逸；其值转换仍产生相同 int。
  编辑后 AST 额外确认整个替换表达式规范类型为 INT、唯一原参数读取映射到原声明、唯一成员地址边成立。
- 新增的是私有、非 volatile 的自动对象初始化；不加入外部可观察写入、函数调用、分配或重复求值。
  不访问其他对象，不改变原类型布局，不引入新越界/无效指针/未初始化读取。

这是一条受限表达式变换的**局部上下文等价论证**，并不是对任意 Csmith 程序全执行无 UB 的形式化证明。
libclang 用于 P0 类型/身份提取，不等于使用目标编译器返回码证明语义等价。
不支持的参数写入、宏、volatile、_Atomic、指针、未初始化局部和未知上下文均被拒绝。
文献在线工具此次访问原 WG14 PDF 超时；沿用此前阶段已引用的同一公开草案和条款，不伪称本次成功下载。

## 5. 五个真实样例

顺序固定为阶段 2B 清单前五项。位置是原 P0 的物理字节行列。

| 种子 | 原读取 | 声明位置 | 使用位置 | 计划中安全读取数 | 双边原 P0 / 增强 P0 |
|---|---|---|---|---:|---|
| seed_0002_1430124932 | argc | 2253:15 | 2257:9 | 1 | 均 0 / 均 0 |
| seed_0004_1594631173 | p_9，声明拼写 int32_t、规范 INT | 379:70 | 392:622 | 4 | 均 0 / 均 0 |
| seed_0008_2017672589 | argc | 1483:15 | 1487:9 | 1 | 均 0 / 均 0 |
| seed_0045_873011950 | argc | 1822:15 | 1826:9 | 1 | 均 0 / 均 0 |
| seed_0017_722706979 | argc | 101:15 | 114:9 | 1 | 均 0 / 均 0 |

每个种子仅替换一次读取，不把 8 个可选读取当作 8 个已增强样例。
四例的 argc 驱动原有 `argc == 2 && strcmp(...) == 0` 打印控制；一例的 p_9 位于生成的函数计算中。
所以它们都真正参与原计算，但不能把四个入口控制参数样例包装为已覆盖 Csmith 核心算术依赖的广泛结果。
真实程序未执行，避免把编译合法性等同于全程序运行安全；动态验证仅限受控夹具。

每例保存：原始/增强绝对路径、SHA、声明与引用身份、编辑前后 bytes、语义论证、
新字段/地址/原读取/映射后声明锚点、INT 类型确认、全部 source_map、最小 unified diff、编译命令及原始流。
`source_map` 的三段覆盖原始和新文件：前后为逐字节 identity，中间为替换区间；
中间另给出原参数读取在新文件中的准确嵌套区间，其余 bytes 是本机制生成的结构，**不伪造原 AST 身份映射**。
位置均在新 bytes 上重算；UTF-8 夹具验证字节列和偏移，没有 ±1 或模糊规则。

未来接口标记 `POTENTIAL_ENTRY_ONLY / NOT_ASSESSED`。
新增关系适合后续研究地址属性 MR，因为成员当前是合法普通 int、地址立即解引用并把值传回原运算。
但当前原生检查器故意不接受复合字面量对象形式，也尚未给新结构生成非法变换及约束见证。
后续须独立完成复合字面量对象/新字段的全部影响检查并验证目标 error，不能将这五例当作 MR 成功样本。

## 6. 实际命令、编译与执行结果

工作目录 `/home/pyx/edgm-experiment`。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover \
  -s second_point/tests -p test_stage2d_enrichment.py -v \
  > second_point/evidence/stage2d/development_tests.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.enrichment_run \
  --out-dir second_point/evidence/stage2d/experiment --scan-timeout 45 \
  > second_point/evidence/stage2d/experiment_stdout.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 second_point/tools/stage2d_finalize.py \
  > second_point/evidence/stage2d/finalize_stdout.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover \
  -s second_point/tests -v > second_point/evidence/stage2d/final_all_tests.txt 2>&1
```

以上实际退出码均为 0。最初开发测试为 20 项；最终完整套件为 **87 项全部通过，无 skip，3.145 秒**，
其中原有 63 项保持通过，阶段 2D 新增 24 项。
扫描每个种子使用独立工作进程，45 秒硬时限；失败 `candidate_count=null`，不会当作零候选进入有效分母。
实验中没有实际超时/崩溃；测试包括真实极短 deadline、模拟超时和崩溃，明确区分零候选与失败。

实际语法检查命令（每个 `<P0>` 分别为原文件和新文件）：

```text
/opt/gcc-15.1.0/bin/gcc -std=c11 -fsyntax-only -w -I /home/pyx/edgm-experiment/src/csmithedge/csmith/runtime <P0>
/opt/llvm-22.1.0/bin/clang-22 -std=c11 -fsyntax-only -w -I /home/pyx/edgm-experiment/src/csmithedge/csmith/runtime <P0>
```

五个真实样例共 20 次语法检查返回 0，原始 stderr 均为空；最小样例原始与增强也双边返回 0。
每个完整 argv、耗时、stdout、stderr 和返回码见 `experiment/*_enrichment.json`。
没有用 warning 代替 error 判定，本阶段也没有需要目标 error 的 P1。

受控 `compute(input)=(input+3)*2` 夹具原始/增强分别编译为临时实验二进制并运行：

```text
<gcc或clang实际路径> -std=c11 -w <夹具路径> -o <证据目录内二进制>
<证据目录内二进制>
```

两个编译器下四次运行均返回 0，stdout 都为 `6 20 -10\n`，stderr 空。
额外单元测试用安全的 `x ^ 3` 运算覆盖 0、负数、INT_MAX、INT_MIN，双编译器下原始/增强输出一致。
动态一致只作附加证据，不替代上述独立的单次读取、值/类型和副作用论证。

开发期间补上两个保守边界：排除隐藏在 sizeof/_Generic/界限内的读取；排除函数体内 include 隐藏参数写入，
并拒绝记录定义后 include 导致的未知记录使用。新增反例测试均通过。
全库已保存输入不存在后置 include；最终规则重算五个真实计划，编辑和候选数逐项相同，
见 `final_plan_hardening_audit.json` 与 `late_include_corpus_audit.json`，没有伪称重新跑过第二次全库。
另重复扫描一个原生种子和重复计划生成，去除耗时/进程回执后结果一致。
最终实现 SHA 清单为 `final_implementation_manifest.json`；早期检查清单保留为开发证据。

## 7. 测试、文件保护与交付

新增测试覆盖：合法关系和双编译器检查、语义论证/单次求值、完整源映射区间、UTF-8 锚点、确定性/只读、
无目标编译调用的计划生成、宏及宏参数映射、名字冲突、同名不同函数身份、header 过滤、函数内 include 隐藏写入、
volatile/_Atomic/指针/写入/未初始化/副作用拒绝、未求值上下文、禁止覆盖、动态极值、
原生阳性全部地址枚举、布局/逃逸/初始化/类型/声明器负例、同名字段不合并、解析失败和超时/崩溃区分、50 个原 SHA。

原 EDGM 的既有 468 文件哈希全部不变，50 原始种子哈希全不变。
原有 git 删除记录、backups 保持，没有修改 src/edgm、原 scripts/config/tests、runtime 或生成器参数。

新增：

- `src/edgm_second/seed_enrichment.py`、`native_bitfield_candidates.py`、`enrichment_run.py`。
- `tests/test_stage2d_enrichment.py`、`tools/stage2d_finalize.py`。
- `examples/stage2d/original.c` 和 `enriched.c`、本报告及 `evidence/stage2d/`。

唯一已有基础模块改动是 `ast_extract.parse_translation_unit` 的向后兼容可选预处理记录开关。

主要机器证据：

- `experiment/summary.json`、`native_records.json/csv`、`native_001.json` 至 `native_050.json`。
- `experiment/enrichment_records.json`、五份新 P0、五份 diff 和五份详细回执。
- `final_checks/minimal.json`、`minimal.diff`、`reproducibility.json`、环境和只读检查。
- `final_all_tests.txt`、`final_plan_hardening_audit.json`、`final_implementation_manifest.json`。

## 8. 五个验收问题

1. **原始安全候选多少？** 当前可完整证明子集确认 **0 字段/0 种子**，原始已观察关系仍为 69 字段、2,410 地址点。
   更广泛 C11 安全候选数未知，工程限制不能冒充不存在证明。
2. **能否构造真实数据依赖？** 能：5/5 固定真实样例通过新成员传递原 int 读取，且增强后双编译合法。
   四例为 argc 控制路径，一例为生成函数参数计算，不夸大覆盖。
3. **语义证据充分吗？** 对当前受限变换有独立的局部类型/初始化/单次读取/地址寿命/无逃逸论证，
   受控输出和极值测试补充验证；不足以声称整个真实程序形式化无 UB 或所有预处理策略等价。
4. **适合后续 MR 吗？** 提供明确的普通成员与地址关系，有潜力；完整 MR 前置检查、P1、独立见证及 error 验证尚未完成，适用率不能提前认定。
5. **何时原生、何时预处理？** 原生关系能闭合时优先使用并独立保留天然统计；
   原生关系缺失或当前无法闭合、但存在这种可证明安全的真实 int 读取时才考虑预处理。
   不满足安全条件时拒绝，不能追加固定模板。下一步应优先提升原生闭合证明，或专门验证该新对象形式的 MR 接口，均需单独安排。

阶段 2D 到此停止，等待验收。
