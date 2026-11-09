# 阶段 2E：位域可取地址性 MR 验收

日期：2026-11-09，Asia/Shanghai。仅实现正式 MR `MR_MEMBER_BITFIELD_ADDRESSABILITY`。
没有实现强化学习、其他 MR、一般诊断比较引擎，也没有运行原 EDGM 变异链或生成 Csmith 程序。

## 1. 结论及分母

| 指标 | 实际结果 |
|---|---:|
| 人工原生合法 P0 / 安全字段 / 原有取地址点 | 1 / 1 / 2 |
| 本阶段重新扫描的真实原始 S | 5 |
| 这 5 个 S 的安全原生字段 | **0** |
| 重新解析、检查和双编译准入的既有增强 P0 | **5/5** |
| 增强 P0 中安全字段 / 目标取地址点 | **5 / 5** |
| 有安全增强候选的真实种子 | **5/5** |
| 固定选择并实际生成 P1 的不同真实种子 | **3** |
| 真实 P0 双编译通过 / P1 双边目标 error 验证通过 | **3/3 / 3/3** |
| 每个编译器在真实 P1 上的目标 error 总数 | **3** |
| 真实 P1 额外 error / 超时 / 崩溃 | **0 / 0 / 0** |
| 最终完整测试 | **111 项通过，无 skip，13.237 秒** |

原 87 项保持通过，本阶段新增 24 项。测试时间为保存的这次运行时间，不是性能基准。
阶段 2D 的历史 50 种子扫描仍是原生安全字段 0；**本次正式 2E 检查只重扫其中 5 个 S**，
不把剩余 45 项包装成已按新 MR 复验。2,410 条历史结构关系也没有计为安全候选。
0 是当前保守子集的证明结果，不是 C11 中绝对不存在安全原生位置的证明。

## 2. 先复核预处理的数据依赖

复核了阶段 2C 候选设计、阶段 2D 报告、`seed_enrichment.py`、共享 AST/ByteEdit/ConstraintWitness，
以及 `evidence/stage2d/experiment/enrichment_records.json` 中固定的五份源码和来源映射。

对每份回执独立重放 S→P0 的单次字节编辑、前后 identity 区间和嵌套原参数读取：
所有新成员确实由原始参数读取初始化，成员值经 `&` 和立即 `*` 回到原始二元运算。
原有运算在 P0 中的起点不变、终点精确增加编辑增量；原参数声明位置和嵌套读取也精确对应。
没有独立追加测试函数或无关执行路径。五份逐项复核见 `evidence/stage2e/all_five_lineage_audit.json`。

四份预处理来自 `argc == 2` 控制条件；第二份来自 `func_6` 的 `p_9` 比较/计算。
本次实际选择前 3 份，因此真实 P1 中两份为 argc 控制路径，一份为生成函数计算路径。
**参数来自原程序是可追溯数据依赖，但新增字段与取地址节点同处一个局部表达式，
这些真实样例必须标为 `simple_local_enhancement`。**
没有新字段的跨语句、跨作用域或跨函数传播，不能据其所在行很长就宣称深层语义复杂。
原有函数参数与函数体读取的关联也不等于新增字段已有跨函数依赖。

## 3. MR 实现及安全子集

实现为 `src/edgm_second/mr_member_bitfield_addressability.py`。
`scan_candidates` 只读取 P0，按 canonical USR 和 `referenced` 连接字段、成员访问和一元 `&`；
字段名称只用于与 AST token 校验和生成声明文本，不依赖 `edgm_sp_*` 或任何固定标签。
一个候选是一处字段声明修改，保存该字段的全部目标地址、全部成员访问及初始化设计符。
同名不同记录不合并；有多个安全字段时全部记录，由独立的 candidate_index 选择单次修改。

共有两个明确子集：

1. **原生子集**：复用阶段 2D 的自动局部对象、`{0}` 初始化、记录类型/值无逃逸、宏位置可靠、
   无布局敏感构造的闭合检查。本阶段进一步要求目标字段的全部表达式使用都直接取地址。
   typedef、union、指针/数组传播、复杂声明器、记录重声明、非局部对象等仍拒绝。
2. **复合字面量子集**：完整 struct 直接定义在唯一复合字面量内，只有一个普通 int 成员；
   初始化严格为该成员的一次普通 int 参数读取，完整 token 语法排除副作用、转换和额外初始化项。
   记录无其他 TYPE_REF，无记录值/布局逃逸；成员唯一表达式使用是直接取地址并立即解引用，
   整个值回到受支持的二元运算。检查全部祖先求值上下文，排除 sizeof、_Generic 等未支持上下文。
   记录定义后的 include、宏覆盖、属性、附加字段及其他使用均拒绝。

两个子集都要求字段原先不是位域，类型、声明字节和 token 可精确验证。
编辑新增的 signed/unsigned 等关键字若是活动宏名也拒绝，避免替换文本被宏捕获。
不在子集时给出明确原因；解析失败 `PARSE_FAILED`，内部失败 `SCAN_FAILED`，
隔离进程超时 `SCAN_TIMEOUT`，异常退出/无效进程结果 `WORKER_FAILED`，失败候选数是 null。
失败不会当作零候选加入适用分母。

### 完整宽度，不采用 int : 1

先分别执行两个目标编译器的 `-std=c11 -dM -E -x c /dev/null`，保留全部宏输出，
得到一致的 `__INT_WIDTH__=32`、`__SIZEOF_INT__=4`、`__CHAR_BIT__=8`、`__INT_MAX__=2147483647`。
还要求 P0 libclang 规范 int 类型大小为 4 字节。
这形成默认本地 ABI 下、无 int 填充位的条件证明；不适用于任意交叉编译目标或额外 ABI 参数。
宏探测没有包含 P1，也没有从错误信息推导预期类别。

实际 int 编辑形如：

```diff
-int member
+signed int member : 32
```

32 是十进制整数常量表达式，正且不超过该目标 int 的完整宽度；显式 signed 消除普通 int 位域符号性的实现定义选择。
它保留普通 int 的整个值域，而非仅能表示极小范围的 `:1`。
原生 unsigned int 使用完整 32 位；原生 _Bool 使用完整 0/1 值域的 1 位。
没有声称记录大小、位序或对齐必然不变；安全性来自相关布局不可观察和记录影响范围闭合。
受控合法位域测试分别在两个编译器下覆盖 INT_MIN、-1、0、1、INT_MAX、UINT_MAX 和 _Bool 1，均通过。
这类动态控制补充目标环境证据，不替代独立语言约束推导。

### 标准依据

使用公开 C11 委员会草案 N1570：6.7.2.1 p4/p5/p10 给出宽度、允许基本类型及位域值的解释；
6.5.3.2 p1 要求此类直接 `&` 的对象不是位域，5.1.1.3 p1 给出诊断义务。
本次原 WG14 PDF 访问超时，但成功核对了[同一 N1570 PDF 的镜像](https://nepsweb.co.uk/langstand/isoC/WG14/n1570.pdf)，
尤其 PDF 第 106 页和第 131 页（从 1 计页）。它是委员会草案，不冒充正式付费标准。
标准不规定必须使用 error 标签，也不规定唯一 caret；error/fatal 是本项目的研究合同。

## 4. P0→P1 与独立 Oracle

`build_mutation` 要求与当前 P0 源码哈希一致的双编译合法准入记录，重新解析 P0 并完整检查候选。
它不调用 subprocess、不读取 P1、不解析 P1，也不接收诊断消息。
原生来源优先：先检查 S；若 S 没有当前可证明安全候选，再使用已经存在、哈希一致的阶段 2D P0。
本次复用已有合法预处理结果，没有再次生成五份不同的增强程序。

现有 `ConstraintWitness` 模块新增独立 `BitfieldConstraintWitness` schema，避免虚构 typedef MR 的形参/调用字段。
保存：原始 S 身份和 SHA、Native/Enriched 来源、S→P0 完整来源映射、P0/P1 SHA、
字段与记录声明身份、类型及完整宽度依据、修改前后属性、全部受影响成员与 `&`、
每条 `C11:6.5.3.2p1` 违反、P0/P1 物理字节锚点、全文件三段 source_map 和已验证的其他使用范围。
预处理回执只证明来源，不定义 `address_of_bitfield`；类别来自 MR 固定语言规则和 P0 声明关系。

`verify_bitfield_witness` 不调用 clang 或编译器，检查字节变换、哈希、声明/记录身份一致性、
完整宽度、全部成员覆盖、地址关系、源映射和锚点。篡改宽度、漏掉一个地址、改身份、改列号、
更改 warning 策略或来源映射均被测试拒绝。
**重放器检查保存证明数据的内部一致性，不是第二个 C 解析器；AST 事实本身仍由受限检查器提供。**
整个模型不是完整 C11 形式化验证器，也不证明任意真实 Csmith 程序全执行无 UB。

每次正式验证均先写 `Oracle.json`，保存 SHA 和 `oracle_before_compilation.json`，随后才调用 P1 编译。
编译结束核对 Oracle 哈希不变。四份正式见证（人工 1、真实 3）最终又独立重放通过。
测试取得合法 P0 准入后禁止所有 subprocess 调用，并限制 Context 只能解析 P0，仍能生成及重放见证。

编辑只替换字段声明，`&` 运算符和远端成员引用 token 均不修改。
原生人工例中声明与两次使用在不同语句，第二次位于内层块。
真实增强例的字段声明位于 `&` 操作数内的复合字面量中，所以整个地址表达式源码区间包含声明编辑，
该祖先区间的文本自然变化；不能声称它与编辑区间完全不相交。
P1 锚点从新 bytes 重算，身份明确为从 P0 映射，`p1_ast_observed=false`。

## 5. 真实验证结果与所有拒绝

阶段 2D 原始固定顺序不变，扫描和 P0 双编译准入全部完成后才选择前三个安全增强种子：

| 顺序 / 原始种子 | 原生安全字段 | 增强安全字段 | P0 GCC/Clang rc | P1 GCC/Clang rc | 双边目标 error 位置 |
|---|---:|---:|---|---|---|
| 1 seed_0002_1430124932 | 0 | 1 | 0 / 0 | 1 / 1 | 2257:12 |
| 2 seed_0004_1594631173 | 0 | 1 | 0 / 0 | 1 / 1 | 392:625 |
| 3 seed_0008_2017672589 | 0 | 1 | 0 / 0 | 1 / 1 | 1487:12 |
| 4 seed_0045_873011950 | 0 | 1 | 0 / 0 | 未生成 | — |
| 5 seed_0017_722706979 | 0 | 1 | 0 / 0 | 未生成 | — |

前三个 S/P0/P1 的 SHA 前 12 位如下；完整值保存在 Oracle、result 和逐种子记录中：

| 顺序 | S | P0 | P1 |
|---|---|---|---|
| 1 | 074a51ddf75c | 7ae57f094d9d | eaa25e779d8b |
| 2 | fdc4e62fe869 | 89b26b1fa117 | 01f58f29d54a |
| 3 | 4fec05c15d52 | 336d3261d1ac | fe767b38c8b9 |

每份三者均不同。P0.c 审阅副本与实际解析的阶段 2D P0 字节相同；Oracle 保留实际解析路径，
避免因另存副本的文件名不同而伪称 USR 未变化。
P0 原始编译 stderr 均空。真实 P1 每个编译器恰有一个目标 error，没有 warning 代替 error。
GCC 消息为 `cannot take address of bit-field '…'`，Clang 为 `address of bit-field requested`。
本次均精确落在独立映射的 `&` 锚点；这只是实际观察，不提升成标准唯一定位要求。

五份原生扫描分别保留 5、7、1、0、0 个拒绝字段。
增强扫描在这些既有拒绝字段之外各增加 1 个安全字段，完整拒绝原因及全部地址锚点仍保留，
没有只保存新增成功项、用增强数量替换天然统计，或把第 4/5 项记成失败。
拒绝原因包括基本类型不支持、声明器超出子集，以及原生对象/布局/类型传播未闭合等。
见 `experiment/native_01.json` 至 `native_05.json` 和 `P0_scan_01.json` 至 `P0_scan_05.json`。

本地局部诊断观察器只识别目标消息，所有 error 均保存原文、严重性、文件、行、字节列和类别。
位置只作精确匹配，没有 ±1、相似度或 unknown 放行。
未报目标、未知额外错误、位置不同或预期地址未覆盖分别记为待复核候选，均不自动确认为 bug。
编译崩溃、超时和执行失败独立记录；扫描解析失败不混入诊断差异。
本次真实实验没有发生这些异常，异常分支来自有标记的单元测试而非伪造的真实编译记录。

## 6. 复现、测试和保护

工作目录 `/home/pyx/edgm-experiment`，实际执行命令：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.bitfield_run \
  --out-dir second_point/evidence/stage2e/experiment \
  > second_point/evidence/stage2e/experiment_stdout.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover \
  -s second_point/tests -v > second_point/evidence/stage2e/final_all_tests.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 second_point/tools/stage2e_finalize.py \
  > second_point/evidence/stage2e/finalize_stdout.txt 2>&1
```

实验与测试退出码均为 0；最终校验记录见 `delivery_check.json`。复现实验应换用尚不存在的输出目录。
实际语法编译使用保存的绝对路径，固定为：

```text
/opt/gcc-15.1.0/bin/gcc -std=c11 -fsyntax-only -w -I <原runtime> <P0或P1>
/opt/llvm-22.1.0/bin/clang-22 -std=c11 -fsyntax-only -w -I <原runtime> <P0或P1>
```

完整 argv、版本、耗时、stdout、stderr、返回码均在 JSON；编译 P1 没有附加 warning 升 error 参数。
libclang 保持 18.1.3；没有更换 GCC、Clang、runtime 或 Python 绑定。
只有受控范围夹具被执行，真实程序仅做语法编译。
扫描由单独工作进程执行，60 秒时限，未实际触发超时或崩溃。

新增测试覆盖：人工两处地址、增强来源、禁编译/禁 P1 AST 的 Oracle、无固定名字、同名字段身份、
多个独立安全字段、signed/unsigned/_Bool 完整宽度、真实极短工作进程时限、解析失败、
const/volatile/atomic/long/复杂声明器拒绝、布局与值逃逸、非零初始化、宏及新关键字宏捕获、
复合字面量额外字段/类型逃逸/地址逃逸、头文件过滤与后置 include、UTF-8/CRLF 精确映射、
过期准入及已有输出、证明篡改和全部地址覆盖、unknown 与严格位置策略、受控全值域、真实增强种子冒烟及只读哈希。
首轮开发测试有 5 个夹具错误：预先创建 P0 后调用独占创建的 enrich_seed；修正为新输出路径后通过。
开发失败日志保留，不作为 MR 或编译器缺陷，也没有隐藏最终回归结果。

原 EDGM 清单 468 个文件、原始种子 50 个均按哈希保持；阶段 2D 最终实现清单 6 个文件也保持。
原有 res/test_* 删除记录、backups 和 second_point 未跟踪状态未变。
本阶段仅给现有 constraint_witness.py 增加新 schema、更新 README，并新增本阶段模块、测试、工具和交付文件。

关键交付：

- `experiment/manual/`、`real_01/`、`real_02/`、`real_03/`：P0/P1、Oracle、源码 diff、原始 stderr、编译及局部验证回执。
- `experiment/summary.json`、`records.json`、五组扫描和准入记录。
- `all_five_lineage_audit.json`、`delivery_check.json`、`implementation_manifest.json`、`implementation.diff`。
- `final_all_tests.txt`、`experiment_stdout.txt`、`finalize_stdout.txt`。

## 7. 九项验收回答与停止点

1. **MR 真正实现了吗？** 是，按字段身份修改合法完整宽度位域声明，由已有地址关系推导目标违反；支持上述两个有限子集。
2. **Oracle 在编译前生成吗？** 是，先持久化见证并记录 SHA/先后屏障，再编译；单元测试禁止编译和 P1 解析。
3. **原生和增强候选各多少？** 本次 5 个 S 为 0 个安全原生字段，5 个增强 P0 为 5 个安全字段/5 个地址点；不是结构次数统计。
4. **实际安全候选多少？** 当前检查器证明 5 个增强字段，另有人工原生 1 字段/2 地址，人工不混入真实分母。
5. **多少真实 P0 生成目标 P1？** 按固定顺序仅尝试 3 个，3 个均成功；其余 2 个只扫描和准入。
6. **双编译器正确报告目标 error 吗？** 是，3 份真实 P1 各双边 1 条，人工双边各 2 条，全部保存精确位置。
7. **有额外错误或未覆盖依赖吗？** 本次未观察额外 error 或关系误判；不支持的一般别名/宏/布局传播被拒绝，不宣称穷尽所有 C11 依赖。
8. **与原程序的依赖多强？** 保留原参数读取与原二元运算的真实值依赖，但新增字段关系局部且简单；没有新字段的跨函数/跨语句深层链。
9. **值得扩大吗？** 值得先研究安全原生类型/对象的影响闭合和更自然的声明—使用分离，再评估应用范围。
   当前 3/3 只证明端到端基础正确性，不能支持高缺陷收益、广泛适用率或论文深层创新的结论；扩大仍需另行安排。

阶段 2E 到此停止，不继续强化学习或另一条 MR。
