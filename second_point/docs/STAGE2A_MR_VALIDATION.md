# 阶段 2A：单条 MR 与独立约束见证验收

日期：2026-11-08（Asia/Shanghai）。仅实现 `MR_TYPEDEF_INDIRECT_STRUCT_ARG`。
阶段 1 的 11 项测试先重新运行通过，然后开展本阶段实现；原 EDGM 和环境配置未改变。

## 1. 变换、最小样例与适用范围

可直接查看 `examples/stage2a/P0.c`、`P1.c` 和对应的 `evidence/stage2a/example_witness.json`。
P0 的核心关系如下（完整程序包含 main、初始化和回调函数定义）：

```c
struct Payload { int value; };
struct DifferentPayload { int value; };
typedef struct Payload Stable;
typedef Stable ArgumentBase;
typedef ArgumentBase Argument;
typedef int (*Callback)(Stable);
struct Dispatch { Callback invoke; };
static int consume(Stable input) { return input.value; }
/* main 中 */
struct Dispatch dispatch = { consume };
Argument value = { 0 };
return dispatch.invoke(value);
```

P0→P1 的唯一源码差异，第 5 行：

```diff
-typedef Stable ArgumentBase;
+typedef struct DifferentPayload ArgumentBase;
```

不是替换实参文本，也没有注入函数或非法代码块。被改变的是 P0 AST 中
`ArgumentBase → Stable` 的真实依赖边，改为 `ArgumentBase → struct DifferentPayload`。
实参侧链仍为 `Argument → ArgumentBase → 新根`；形参侧 `Callback → Stable → struct Payload` 不变。

实现位于 `src/edgm_second/mr_typedef_indirect_struct_arg.py`。它解析 P0 的声明/TYPE_REF/USR，
识别成员函数指针、原型、实参 DECL_REF_EXPR 和变量声明，再从实参 typedef 链选隔离的中间边。
类型判据是同一 TU 的结构体声明身份、完整定义、作用域及规范类型的交叉校验，
不是“类型字符串不同”或“字段布局不同”。本样例两个 struct 字段完全相同，仍是不同类型。

为了使额外影响可检查，本次有意采用保守子集：

- 两个候选结构体在文件作用域完整定义，成员均为直接声明的非位域 `int`。
- 简单文件级 typedef 链；修改中间的 typedef-name 依赖，不修改回调形参公共根。
- 单个非变参、有原型、返回 int 的结构体函数指针成员调用，形如 `对象.成员(变量)`。
- 实参变量为本地、无额外限定符/复杂声明器，初始化严格为 `{ 0 }`，仅在目标调用引用一次。
- 受影响别名的全部 TYPE_REF 只能继续构成简单别名或终止于这个变量；共享形参链、额外变量、
  sizeof/类型相关用途等不能闭合证明的情况拒绝；标签遮蔽也拒绝。
- 当前不处理预处理指令、宏、属性、复杂初始化、箭头/显式解引用调用和一般结构体布局。

不在这个子集不代表语言不合法，表示本条已验证实现 `NOT_APPLICABLE`。
`scan_candidates` 是只读接口；`build_mutation` 返回 P1 bytes 和见证，不写文件；
`write_mutation` 才保存新产物，输出必须在 second_point 内且不能覆盖已有文件。
有效 P0 无候选时返回 NOT_APPLICABLE、不创建输出；缺失/过期的 P0 准入记录或非法输出路径作为接口错误拒绝。

## 2. C11 依据和本例推导

参考 [WG14 C11 委员会草案 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)，
公开草案，不冒称下载了 ISO 正式付费出版物。相关条款：

| 条款 | 本 MR 使用的规则 |
|---|---|
| 6.7.2.3 p4–p5 | 结构体身份由声明、作用域和标签确定 |
| 6.2.7 p1 | 类型相同则兼容；不能将跨 TU 规则误套到本例 |
| 6.7.8 p3 | typedef 为已有类型建立别名 |
| 6.5.2.2 p2 | 有原型调用的实参须满足对应形参的赋值约束 |
| 6.5.16.1 p1 | 结构体赋值要求兼容结构体类型 |
| 5.1.1.3 p1 | 语法/约束违反要求产生诊断，不规定统一严重性标签或唯一字符位置 |

P0 的独立推导：Argument 沿声明引用边解析到 `c:@S@Payload`；回调参数同样解析到该声明，
不是仅凭别名拼写相同。`consume` 和 Callback 类型兼容，dispatch 的初始化不触及变化分支，
value 经零初始化后按值传入。示例没有未定义标识符、无效成员或参数数量冲突。
完整小程序在常规 C11 语义下可逐项检查，另以两个指定编译器接受作为实验准入证据；
通用接口的双编译器接受记录不冒充整个任意 C 程序的形式化合法性证明。

P1 的独立推导：对图做一次符号边替换后，实参根为 `c:@S@DifferentPayload`，
形参仍为 `c:@S@Payload`；二者是同一 TU 中两个不同的文件级完整 struct 声明，
不因成员恰好相同而合并。因而目标按值调用的赋值兼容条件被破坏。

额外影响的排除见证：只改变一个类型说明符；两个根均能接受该变量的 `{ 0 }` 初始化；
受影响别名只通向一个局部变量，该变量仅在目标实参使用；回调签名、成员、函数定义和
dispatch 的函数指针初始化全部不变。不是把函数指针赋值冲突当成调用参数错误。
这是一条受限变换的影响闭合检查，不是一般 C 程序的全约束证明器。

## 3. Oracle 的独立生成和数据结构

`constraint_witness.py` 提供固定必填字段的 `ConstraintWitness` dataclass 及 JSON 序列化。
包含任务要求的 mr_id、seed_id（P0 SHA256）、constraint_id、preconditions、changed_declaration、
expected_parameter_type、actual_argument_type、call_expression、source_anchors、expected_error、source_map，
另有 dependency_graph、isolation、provenance 和 schema_version。

字段实例的关键值：

```json
{
  "mr_id": "MR_TYPEDEF_INDIRECT_STRUCT_ARG",
  "constraint_id": "C11:6.5.2.2p2",
  "changed_declaration": {
    "original_dependency_usr": "c:P0.c@T@Stable",
    "new_dependency_usr": "c:@S@DifferentPayload"
  },
  "expected_parameter_type": {"declaration_usr": "c:@S@Payload"},
  "actual_argument_type": {
    "p0": {"declaration_usr": "c:@S@Payload"},
    "p1": {"declaration_usr": "c:@S@DifferentPayload"}
  },
  "expected_error": {
    "category": "incompatible_struct_argument",
    "required_severities": ["error", "fatal"],
    "warning_satisfies": false,
    "exact_caret_mandated_by_standard": false
  }
}
```

expected_error 的类别是由目标违反关系确定的研究类别，没有从编译输出学习消息映射。
error/fatal 是本实验合同；单独 warning 不通过，但不能据此断言只报 warning 就违反 ISO。
本阶段没有诊断/Oracle 完整判定器，更没有把不匹配自动标为 confirmed bug。

独立生成顺序：读取 P0 准入的源哈希 → 只解析 P0 → 构造声明图 → 验证隔离条件 →
符号替换推导 P1 类型 → 应用单个字节编辑 → 重新计算锚点 → 序列化见证 → 才允许冒烟工具编译 P1。
MR 模块不导入 subprocess，不读取 P1 诊断，也不解析 P1。
见证重放函数 `verify_constraint_witness` 只用声明图、源 bytes、哈希和坐标，不调用 libclang 或编译器；
其职责是检查本 MR 的证明数据内部一致性，不是另一个完整 C11 解析/证明器。

在单元测试中，P0 取得准入记录后禁止任何 subprocess.run，并把解析函数限制为只能接收 P0，
仍能生成并重放 Oracle。冒烟记录保存了编译 P1 前见证文件的 SHA256，并确认编译后见证未变化。

## 4. 精确位置和字节编辑

复用阶段 1 的 c11/runtime 解析约定，新增共享 `parse_translation_unit` 和主文件遍历接口；
原 extract_ast 返回结构和头文件过滤逻辑保持。`source_edit.py` 延续原有字节半开区间约定，
只允许校验过的单次编辑，不使用字符串字符索引或宽松位置规则。

本样例编辑 `[177,183)`：6 字节 `Stable` 变为 23 字节 `struct DifferentPayload`，总增量 17 字节。
目标实参位置：P0 offset 473，P1 offset 490；两者均第 13 行第 28 字节列。
行号、字节列由对应 P0/P1 原始 bytes 重新计算，不能简单沿用旧 offset。

source_anchors 保存每个关键节点在 P0/P1 的文件、行列、offset、extent、源码片段及节点身份；
表达式无自有 USR 时保留 kind+P0 hash+extent 的 origin_key，并记录 referenced_usr。
P1 的身份明确标为从 P0 AST 经编辑映射，`p1_ast_observed=false`，不伪造非法程序中重新观察到的 AST。
同时保存回调 typedef、实参完整别名链、两个 struct 定义、变量/成员声明及目标调用。
类型事实中的定义锚点指向 P0 的原始见证，P1 位置在 source_anchors 中单独提供。

## 5. 真实命令、返回码和诊断

最终可复现命令（新输出目录不能已存在）：

```bash
cd /home/pyx/edgm-experiment
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover \
  -s second_point/tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 second_point/tools/stage2a_smoke.py \
  --out-dir second_point/evidence/stage2a/new_smoke
```

本次实际运行的最终输出目录是 `second_point/evidence/stage2a/final_smoke`，
终端输出保存为 `evidence/stage2a/final_smoke_stdout.txt`。
此前 `smoke/` 为首次成功的冒烟记录；最终记录包含补充的声明链锚点。

最终工具对 P0、P1 各执行以下命令，将 `<P>` 分别替换为
`/home/pyx/edgm-experiment/second_point/evidence/stage2a/final_smoke/P0.c` 和 `P1.c`：

```bash
/opt/gcc-15.1.0/bin/gcc -std=c11 -fsyntax-only -w \
  -I /home/pyx/edgm-experiment/src/csmithedge/csmith/runtime <P>
/opt/llvm-22.1.0/bin/clang-22 -std=c11 -fsyntax-only -w \
  -I /home/pyx/edgm-experiment/src/csmithedge/csmith/runtime <P>
```

环境为实际 GCC 15.1.0、Clang 22.1.0。完整 argv、真实返回码、耗时、stdout/stderr 在
`final_smoke/P0_validation.json`、`P1_validation.json` 中，而不是以上命令模板。

| 编译器 | P0 rc | P1 rc | P1 error 数 | 实际 error 位置 |
|---|---:|---:|---:|---|
| GCC 15.1.0 | 0 | 1 | 1 | P1.c:13:28 |
| Clang 22.1.0 | 0 | 1 | 1 | P1.c:13:28 |

GCC error 消息：`incompatible type for argument 1 of 'dispatch.invoke'`；
其 note 指明 expected Stable/struct Payload、actual Argument/struct DifferentPayload。
Clang error 消息：`passing 'Argument' (aka 'struct DifferentPayload') to parameter of incompatible type 'Stable' (aka 'struct Payload')`。
两者本例恰好都选择目标实参锚点，但 Oracle 没有声称标准指定了唯一 caret。
原始完整 stderr 分别保存在 `P1.gcc.stderr.txt`、`P1.clang.stderr.txt`；
观察到的精确字段在 `diagnostic_observations.json`，没有 ±1、unknown 放行或相似度比较。
冒烟工具中的正则仅抽取本例的 error 行以展示/计数，没有生成类别 Oracle 或实现一般差分引擎。

## 6. 自动化测试

最终运行 `unittest discover -s second_point/tests -v`：29 项全部通过，无 skip，见
`evidence/stage2a/all_tests.txt`。其中阶段 1 原有 11 项全部通过，新增 18 项如下：

| 测试 | 结果 |
|---|---|
| 合法 P0 双编译通过，P1 双边恰好一个真实 error，输入不变 | PASS |
| 禁止编译/P1 解析时，Oracle 仍能先生成和重放 | PASS |
| Oracle 完成后才观察 P1 AST，确认实参根改变、形参根不变 | PASS |
| 唯一字节编辑、全部锚点、source_map 和篡改列号拒绝 | PASS |
| 类型/变量/成员/函数改名后仍识别，无固定名称依赖 | PASS |
| 没有成员间接调用时跳过 | PASS |
| 受影响别名用于额外变量时跳过 | PASS |
| 实参变量有 sizeof 等额外使用时跳过 | PASS |
| 非零/指定成员初始化不在支持子集，跳过 | PASS |
| 不完整或只为同一 struct 另起别名，不冒充不同类型 | PASS |
| 宏/变参调用跳过 | PASS |
| 形参与实参共享受影响别名时跳过，避免变换后仍兼容 | PASS |
| typedef 同行中文注释 + CRLF，字节/行列/换行正确，P1 真实拒绝 | PASS |
| 替换 tag 存在遮蔽时跳过 | PASS |
| 无效 P0 AST 明确拒绝 | PASS |
| 合法但不适用的 P0 不创建 P1/见证文件 | PASS |
| 过期 P0 准入记录及覆盖已有输出被拒绝 | PASS |
| 保存 P1/JSON 见证且 P0 bytes 不变 | PASS |

不会把模拟异常当作真实编译结果；阶段 1 的 timeout 测试仍为模拟，
本阶段的 P0/P1 error 检查使用真实进程。P1 AST 的事后观察仅在测试中校验推导，未用于 Oracle 生成。

## 7. 21,050 个节点审计、版本差异和一个真实种子

最小夹具和初始 14 项 MR 测试成功后，工具才重新验证并只读扫描一个既有种子：
`data/seeds/diverse/clean_diversify_syntax/seed_0001_997117443.c`。
最终工具复验同一个种子，没有扩大种子范围。
其 GCC/Clang 准入均通过，扫描为 NOT_APPLICABLE，原因包含 NOT_MEMBER_INDIRECT_CALL、REQUIRES_ONE_ARGUMENT；
受限模型的 supported_records=0，未识别到支持的目标调用。没有强行补插代码，也不推断其他 49 个种子的适用性。

独立遍历未过滤的 libclang 树，再以完整文件路径分类：

| 分类 | 节点数 |
|---|---:|
| 主文件 | 21,050 |
| 头文件（含 runtime/系统头文件） | 6,687 |
| 无 file（含 TU） | 3 |

阶段 1 保存的 21,050 个节点、此次 extract_ast、未过滤树中主文件节点的
kind/location-offset/extent 列表逐项完全一致；foreign_files_in_snapshot 为空。
所以原计数确实没有混入 runtime 头文件，不是单凭旧报告的 file 字段得出结论。
详见 `final_smoke/stage1_ast_audit.json`。

默认 libclang 仍为 `/usr/lib/x86_64-linux-gnu/libclang-18.so.18` / 18.1.3。
本例 P0 的声明关系、符号推导、事后 P1 类型观察与被测 Clang 22.1.0 的结果相符；
本条受限 MR 和该种子 AST 过滤没有观察到版本差异导致的问题。
这不是证明所有 C11/扩展在两个前端版本中均一致。未安装、更换或重新配置任何版本。

## 8. 变更、真实性边界和停止点

新增：`source_edit.py`、`constraint_witness.py`、`mr_typedef_indirect_struct_arg.py`、
`tests/test_stage2a_mr.py`、`tools/stage2a_smoke.py`、P0/P1 样例、本报告及 stage2a 证据。
调整 `ast_extract.py` 仅提取共享 P0 解析/遍历接口；更新 README、研究设计的阶段状态及包说明。
`environment.py`、`seeds.py`、环境 JSON、原 11 项测试和旧验收报告保持。

`final_smoke/readonly_check.json` 对旧代码/配置/测试/种子 468 个文件复核，无改变/删除/新增；
现有 git 中 res/test_* 删除和 backups 未跟踪状态均为此前已有。未运行旧生成器、变异链或在线服务。

已验证最小样例无额外 error，实际诊断与独立推导相符，当前没有发现本例的关系误判或编译器缺陷。
受限模型可能保守跳过很多合法机会，不能宣称任意 CsmithEdge 种子均适用，也不能把当前见证重放当成通用形式化证明。
没有完整诊断比较引擎、第二条 MR、RL、训练、全矩阵或大规模实验。
阶段 2A 到此停止，等待用户验收。
