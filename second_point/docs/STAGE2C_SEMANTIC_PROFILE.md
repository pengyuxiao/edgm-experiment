# 阶段 2C：真实 CsmithEdge 种子的语义结构画像

日期：2026-11-08，Asia/Shanghai。本阶段只建立只读画像和设计建议，没有实现新 MR、生成新 MR 的 P1 或训练模型。

## 1. 样本、环境与执行记录

严格复用阶段 2B `corpus/records.json` 的 50 个文件及其顺序、SHA256 和合法性记录。
来源是 `data/seeds/diverse/clean_diversify_syntax`，属于 CsmithEdge 基础程序经 EDGM diversify 后的合法库，
不是本次新生成的程序，也不是未处理原始 Csmith 的结构分布。本次逐一核对原文件哈希后重解析，
没有再次重复 100 次无必要的 P0 编译。阶段 2B 的合法性回执、当前环境及输入一致性共同形成准入依据。

现有 GCC 15.1.0、Clang 22.1.0、libclang 18.1.3 和 runtime 配置保持。
复用 `ast_extract.parse_translation_unit` 与 `main_file_nodes`，没有新写 C 解析器。
AST 参数仍为 `-x c -std=c11 -w -I <原runtime>`；已有编译回归测试保持 `-std=c11 -fsyntax-only -w`。
本阶段的 P0 数值观察通过 libclang 求值接口完成，不调用目标 GCC/Clang 进程；
它不是独立 Oracle，也不能把“libclang 能求值”当成 C11 ICE 的证明。

实际执行：

```bash
cd /home/pyx/edgm-experiment
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover \
  -s second_point/tests -p test_stage2c_profile.py -v

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.semantic_profile_run \
  --out-dir second_point/evidence/stage2c/final_corpus --timeout 90 \
  > second_point/evidence/stage2c/final_corpus_stdout.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover \
  -s second_point/tests -v > second_point/evidence/stage2c/all_tests.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 second_point/tools/stage2c_finalize.py \
  --corpus-dir second_point/evidence/stage2c/final_corpus \
  > second_point/evidence/stage2c/finalize_stdout.txt
```

最终画像、全测试和汇总命令均退出 0。画像完整处理 **50/50**，总耗时 **181.78 秒**，
无解析失败、超时或工作进程异常，主文件节点合计 **1,490,981**，与阶段 2B 完全一致。
每个种子由独立工作进程处理，时限 90 秒；失败与零结构严格区分。

开发时的前导运行 `evidence/stage2c/corpus/` 在统计口径校准期间中止，未纳入最终结果。
最终结论只来自 `final_corpus/` 的完整 50 项，未将重复读取加入分母。

## 2. 计数与身份约定

- 实例数是 AST 节点出现次数或关系路径出现次数，不是独立源文件变异位置数，更不是 MR 适用数。
- 每类同时报告种子覆盖和实例。一个 AST 节点可承担多个角色，12 类不是互斥分区，不能相加成“总节点数”。
- 类型使用通过 TYPE_REF.referenced；表达式使用通过 DECL_REF_EXPR/MEMBER_REF_EXPR.referenced；
  普通函数通过函数 USR、定义及形参位置关联，不按标识符名字关联。
- 单个种子内的声明身份用 canonical USR；跨种子必须组合源 SHA256 和 USR。
  无 USR 的表达式以文件、kind、extent 和所属函数/块定位。
- 主文件过滤使用 resolve 后完整路径，TU 根没有 file 仍展开；头文件子树不进入统计。
  主文件中对 runtime 函数的调用作为 external_function_calls 单列，不把头文件函数/形参定义算入主文件图。
- 行列为物理文件的 1 起始字节行列，offset 为 0 起始字节偏移，extent 为半开区间。
  示例保留完整路径、声明/引用身份、作用域和函数上下文，不进行行列容忍或模糊归并。
- 每种子每类保存最多 3 个代表关系，总表最多 5 个；保存全量计数和部分完整声明表，
  **没有宣称持久化了所有 150,047 条运算边**。算法、输入哈希与测试允许复查统计。

## 3. 十二类画像

“关系数”按公开关系种类相加；同一使用点可能同时参与多条不同语义路径。
完整分项见 `final_corpus/summary.json`、`summary.csv`、`families.json`。

| 类别 | 含结构种子数 | AST 实例/角色 | 语义依赖关系数 |
|---|---:|---|---:|
| 1. 整数/枚举 typedef 声明与类型使用 | 50 | typedef 1,301；主文件别名 TYPE_REF 834 | 834 |
| 2. typedef 链及向运算传播 | 50 有链；0 有运算传播 | 链长度1/2/3分别467/417/417；传播0 | 0 |
| 3. 普通函数声明、定义、形参与调用 | 50 | 声明872、定义461、形参2,292、主文件函数调用444 | 1,588 |
| 4. struct/union、成员及后续操作 | 26 | struct30、union28、字段268、成员访问9,804 | 22,035 |
| 5. 指针、解引用、间接写入 | 50 | 含指针声明15,276；解引用14,332；间接写入8,279 | 27,066 |
| 6. 限定符与使用 | 47 | 有限定层声明6,757；变量/形参引用27,103 | 27,103 |
| 7. 数组、界限及下标 | 50 | 数组声明5,780；维度10,053；下标54,128 | 54,128 |
| 8. 整数常量、枚举和常量表达式语法子集 | 50 | 整数字面量283,784；枚举项1,011；枚举引用287；ICE语法子集节点317,947 | 287 |
| 9. switch/case/default | 47 | switch468；case936；default468；嵌套switch0 | 1,404 |
| 10. goto、label、块作用域 | 50 有块；38 有goto/label | 块8,767；goto137；label121 | 137 |
| 11. 位域与宽度 | 18 | 位域133（含 struct 与 union） | 133 |
| 12. 整数运算与类型转换 | 50 | 整数结果运算96,366；显式转换30,061；隐式类型变化312,577 | 462,624 |

补充解释：

- 函数形参实例包含声明和定义的不同 ParmDecl，不能把2,292当独立形参身份数。
  重声明→定义边411，形参→调用实参路径1,177。主文件函数444次调用分布50个种子，
  非空实参路径只分布41个；无参调用不是解析失败。
- struct 在15个种子、union在17个种子，两者并集26。133个位域含 union，
  因而不应与阶段2B“仅struct字段中的123个位域”直接比较。
- 指针层级1/2/3/4/5的声明分别为7,953/3,495/1,991/1,168/669；此处“含指针声明”
  也包括数组元素为指针的声明。限定符层级沿完整类型路径记录，路径可包含数组，不能把const@8解释为八级指针。
- 6,938条地址绑定仅指直接初始化 `p=&object` 的声明引用关系，不包含一般赋值和控制流变化的完整 points-to 分析。
- 限定符使用27,103只统计主文件变量/形参 DECL_REF；成员限定类型另在成员关系中保存，
  不把该数字宣传为所有限定符行为的完备统计。
- 参数声明类型保留 libclang 的书写/声明表示，例如 main 的 `char *argv[]` 表示为不完整数组。
  后续做兼容性 Oracle 必须另用函数原型的参数调整类型，不能直接比较这些书写形状。

## 4. 六类核心跨节点路径

| 关系 | 种子数 | 路径实例 | 可控位置、严格约束与风险 |
|---|---:|---:|---|
| typedef声明→类型使用→运算 | 0 | 0 | 834条边只连接别名声明，未传播到实际变量运算；不能据此给typedef语义MR计算适用率 |
| 形参声明→调用→实际参数 | 41 | 1,177 | 可定位参数/实参和USR，但改形参易重复EDGM且引入声明冲突；其中多级指针对有进一步研究价值 |
| 成员类型→成员访问→后续操作 | 26 | 9,759 | 可定位字段和使用；取地址/聚合可修改性可有独立约束，普通改错成员类型高度重复旧算子 |
| 指针声明→间接目标表达式→写入 | 43 | 5,796 | 可定位地址表达式中引用的指针；尚不能证明其运行时别名目标，简单加const与EDGM重叠 |
| 常量声明→数组界限 | 0 | 0 | 所有已定位界限无声明引用；不能将数组5,780个当作跨声明常量MR候选 |
| switch控制表达式→最近归属标签→转换值 | 47 | 1,404 | 所有控制提升后为unsigned int，case分别0/1，嵌套为0；直接复制标签与EDGM重复，不优先 |

其余可直接追溯的相关结构：

- 成员声明→取地址：19种子，2,410次；这是第一候选设计的结构基础，**不是2,410个已验证变异点**。
- 多级指针形参→调用实参：10种子，70对；未确认其中所有实参均为可安全修改的 `&局部指针`。
- 记录类型→整体赋值：10种子，59次；包含struct与union。
- 记录包含另一个记录：3种子，3条边。仅一个种子同时有整体赋值，
  但该共现不等于赋值目标就在该包含路径上；没有把共现冒充关系连接成功。
- goto→实际label：38种子，137条，可跨块但必须属于同一函数。没有用文本相同的label合并不同函数。

每类关系都能提供精确节点位置，但安全可编辑范围、影响闭合和独立语言证明尚未全部验证。
“可定位”不等于“可以安全变异”，本阶段所有新MR完整前置条件候选数为 null/未知。

## 5. 数组、常量、限定符和控制流的关键发现

1. **别名链不等于语义传播。** 1,251个整数别名和50个枚举别名，链长度统计与阶段2B一致；
   834个主文件TYPE_REF都是链内部类型使用，没有发现这些主文件别名用于实际变量并传播到运算。
   因此传播距离分布为空、最大距离为null，不伪造“距离0”。runtime中的int32_t等别名不属于可修改的主文件声明。
2. **数组界限没有跨声明关系。** 10,003个已知尺寸维度为1到10；另外50个未指定维度来自每个main的argv[]。
   不把未知尺寸当0。既无enum/const引用，也无普通变量界限依赖，不能证明一个基于命名界限的MR可用。
   越界索引通常不是必须报error的约束违反，不应把EDGM的array_oob策略搬过来作为严格Oracle。
3. **常量求值与C11 ICE分开。** 317,947是受支持ICE“语法”子集的节点数，包含其子表达式，
   不是已独立证明无溢出/无除零/符合全部C11规则的常量表达式数量。
   对界限/case等观察 libclang 数值，但不作为新MR的真值来源。测试明确拒绝将const对象引用当C11 ICE。
4. **限定符路径很丰富，但别名状态尚未证明。** const/volatile在类型多层出现，
   改限定符必须沿精确层级判断兼容性/可修改性，不能只看到“const”就预测error。
5. **switch的数值结构很统一。** 468个控制类型均为unsigned int；转换后的case0和case1各468次。
   每个switch自己的default合法，不与另一个switch的default相撞。没有嵌套switch可供本库研究归属转移。
   数值转换按当前目标CHAR_BIT=8及libclang类型大小观察；有符号超范围转换本工具会记未知，不擅自假定结果。

## 6. 可追溯节点示例

以下路径均位于 `data/seeds/diverse/clean_diversify_syntax/`。行列为字节坐标，代表关系不代表安全候选。
全路径、offset、extent、USR、类型路径和函数/块ID见JSON。

| 文件 | 关系 | 源位置 → 使用位置 |
|---|---|---|
| seed_0002_1430124932.c | typedef链类型引用 | 24:18 → 25:9 |
| 同上 | 函数形参→实参 | 1325:42 → 1126:263 |
| 同上 | 数组声明→下标 | 94:21 → 95:31 |
| 同上 | 指针声明→间接写 | 594:14 → 666:108 |
| 同上 | switch→所属case | 627:9 → 628:9 |
| 同上 | goto→label | 1928:17 → 2240:1 |
| 同上 | 位域宽度表达式→字段 | 43:18 → 43:13 |
| seed_0004_1594631173.c | U1::f1→成员取地址 | 37:13 → 133:43 |
| 同上 | 多级指针形参→实参 | 1770:52 → 1568:391 |
| 同上 | U0→整体赋值 | 29:7 → 741:70 |

例如U1::f1的身份是`c:@U@U1@FI@f1`，与其他记录中同名f1不同；其规范基本类型INT，
这只证明该地址关系值得继续检查，不证明修改为位域后其所有使用都安全。

## 7. 测试、失败状态和保护

最终 **63项测试全部通过，无skip，1.839秒**：原阶段1/2A/2B的46项未回归，新增17项画像测试。
新增覆盖12类结构与路径、最近switch归属/转换值、const对象非ICE、多层别名和局部遮蔽、
同名数组/形参/字段/跨函数label身份、header同名文件过滤、UTF-8坐标、只读与复现、
解析失败/零结构区分、超时里程碑/崩溃分母、指针重赋值与间接写区分、
数组下标与指针下标区分、普通变量VLA界限不冒充常量、聚合关系计数及不调用目标编译器进程的P0求值。

开发夹具最初暴露goto连接失败：从LABEL_REF获取目标的路线未成功连接到已遍历cursor，
改为使用GOTO_STMT.referenced和真实cursor身份后，通过不同函数同名label回归；没有按名字兜底。
统计校准还分开了数组对象与指针下标、普通VLA变量依赖与常量声明依赖。
最终真实库操作符提取未识别数量为0；对一般宏/注释导致的不可识别操作，工具会显式记录，不能把它当成安全候选。

另对一个真实种子重复画像，除时间/进程记录外的语义结果逐项一致；见
`evidence/stage2c/reproducibility_check.json`。没有声称重复了全库。

`final_corpus/readonly_check.json` 复核原EDGM代码/配置/测试/种子468个文件无修改、删除或新增，
50个原始种子哈希保持。旧git删除记录及backups未动；没有改变编译器、runtime或生成器参数。

## 8. 产物与边界

新增 `semantic_profile.py`、`semantic_profile_run.py`、`tools/stage2c_finalize.py`、
`tests/test_stage2c_profile.py`、本报告、候选设计及 `evidence/stage2c/`。
既有解析器、MR、见证、扫描与环境模块未重构。
机器产物在 `final_corpus/`：summary.json/csv、per_seed.csv、records.json、50个seed_NNN.json、
families.json、supplementary.json、mr_design_summary.json、选择/环境/只读/实现哈希记录。

最普遍的复杂路径是整数操作/转换、数组声明到下标使用和函数声明到定义/调用；
但最常见的结构不一定能构造独立的严格error Oracle。
更值得下一步验证的是已有成员地址依赖和成员限定符对整体记录赋值的传递影响。
多级指针形实参虽有真实结构，但限定符变换的Clang C模式ExtWarn源码路径不支持当前error-only合同，已在候选比较中暂淘汰。
推荐及条件证明见 `STAGE2C_MR_CANDIDATES.md`。本阶段在设计处停止。
