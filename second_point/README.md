# EDGM 第二研究点：阶段 0–2H

独立于 EDGM 的只读基础框架。已实现环境记录、合法候选 P0 加载与双编译器验证、主文件 AST 提取。
阶段 2H 新增第三条 `MR_MEMBER_CONST_AGGREGATE_WRITE`，冻结三条 MR 的初始库；
报告与真实全池执行结果见[阶段 2H 验收](docs/STAGE2H_CONST_AGGREGATE_MR.md)。尚未实现学习模块。
阶段 2G 已增加主动合法增强和两条既有 MR 的真实动作池，见[阶段 2G 验收](docs/STAGE2G_ACTIVE_SEMANTIC_ENRICHMENT.md)。
阶段 2A 已新增单条 typedef 依赖变换及独立约束见证；完整诊断比较与强化学习尚未实现。
阶段 2E 已实现受限的 `MR_MEMBER_BITFIELD_ADDRESSABILITY`，见
[位域 MR 验收](docs/STAGE2E_BITFIELD_MR_VALIDATION.md)。5 个既有增强 P0 均有安全候选，
固定前三个完成 P0→P1+Oracle 和双编译器目标 error 验证；真实关系属于简单局部增强。
本阶段真实结果见 [阶段 2A 验收](docs/STAGE2A_MR_VALIDATION.md)。方法和边界见 [设计文档](docs/RESEARCH_DESIGN.md)，
代码发现见 [阅读报告](docs/EDGM_CODE_REVIEW.md)，实测结果见 [验收记录](docs/STAGE1_VALIDATION.md)。

从 EDGM 仓库根目录运行，使用现有系统 Python，不需要安装此工程：

```bash
cd /home/pyx/edgm-experiment
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover -s second_point/tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.cli --output second_point/evidence/my_run.json
```

指定一个现有 P0：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.cli \
  --seed data/seeds/diverse/clean_diversify_syntax/seed_0001_997117443.c \
  --output second_point/evidence/my_seed_run.json
```

输出文件必须尚不存在，且 resolve 后位于 second_point 内。CLI 退出 0 表示环境、P0 和 AST 均通过，
1 表示有明确失败记录，2 表示参数/输出边界错误。每次只检查一个种子。
配置为 `config/environment.json`，可用 `--config` 指定另一个 JSON；不改变系统环境。
默认先找 `data/seeds/clean`，再找已有 `clean_diversify_syntax`，所有输入都只读并重新验证。
两者均无数据时，在临时目录创建最小合法 C 样例并在报告中明确标识，绝不将其称为 CsmithEdge 结果。

依赖现有 GCC 15.1.0、Clang 22.1.0、Csmith runtime、Python clang 绑定及可加载 libclang。
当前 `/usr/bin/python3` 可用，仓库 venv 缺少绑定；未安装/升级任何组件。
当前默认 libclang 是 18.1.3，独立记录其版本和实际加载路径；它不同于被测 Clang 22.1.0。
框架对编译器版本不符合、缺 runtime、进程异常和 AST 解析错误显式失败。

模块接口：

- `inspect_environment(config)` 返回可 JSON 序列化的版本、路径和探测报告。
- `discover_seeds(config)` 返回只读路径与检查过的目录计数。
- `validate_seed(path, environment, timeout)` 保留命令、返回码、耗时、stdout/stderr 和源哈希。
- `extract_ast(path, runtime)` 返回节点列表、诊断、父 ID、深度、物理行/字节列及字节范围。

只使用 `-std=c11 -fsyntax-only -w` 编译约定并加现有 runtime include。
本阶段没有诊断类别匹配，P0 验证依据进程成功退出；错误 P0 仅用来测试失败状态。
超时保留已有部分输出，返回码为 null；不伪造编译器返回码。
AST 以 bytes 定位，头文件按完整路径排除，TU 无 file 仍遍历子节点；有 error/fatal 就拒绝上下文。
尚未处理完整宏展开位置映射、依赖图或标准合法性证明。

`evidence/stage1_run.json` 包含现有种子的真实验证和完整 AST；
`evidence/failure_run.json` 保存临时无效样例的真实双编译器 stderr；
`evidence/unittest.txt` 为实际测试输出，`readonly_check.json` 为旧文件哈希复核。
测试只在临时目录创建/删除样例，读取原种子，不调用旧生成器、变异链或在线服务。


阶段 2A 的唯一 MR：`MR_TYPEDEF_INDIRECT_STRUCT_ARG`。按 P0 AST 的 typedef 声明引用图
改变实参侧一条依赖边，保持成员回调参数类型不变；不适用就返回 NOT_APPLICABLE。
原型、引用闭合和支持子集详见验收报告。见证类型为 `ConstraintWitness`，其重放不调用编译器。

端到端冒烟（先最小夹具，再只读扫描一个已有种子；输出目录必须新建）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 second_point/tools/stage2a_smoke.py \
  --out-dir second_point/evidence/stage2a/my_smoke
```

检查 `P0_validation.json`、`witness.json`、`P1_validation.json`、原始 stderr、
`one_seed_scan.json`、`stage1_ast_audit.json` 和 `readonly_check.json`。
标准诊断义务与项目 error/fatal 验收条件分别记录，不把 warning 当通过，不声称标准规定唯一 caret。
当前没有一般 Oracle 比较、诊断差分或学习器。

阶段 2B 已完成真实库适用性评估：[适用性报告](docs/STAGE2B_APPLICABILITY_REPORT.md)。
现有合法目录提供 50 个不同种子；全部双编译通过且 AST 可解析，当前 MR 的候选为 0，适用率 0%。
未生成真实种子 P1。审计修复了受影响 typedef 多处声明的漏检，并将解析失败与不适用分开。

只读、可复现的有界扫描（每个 libclang 任务独立进程，输出目录必须不存在）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.applicability_run \
  --out-dir second_point/evidence/stage2b/my_corpus --limit 100 --random-seed 20261108 \
  --scan-timeout 30 --compiler-timeout 20
```

默认搜集既有配置中的全部合法目录并按内容哈希去重，不生成新种子。
若找到候选，此专用验证工具最多验证 3 个不同种子并完整保留结果；无候选正常退出。
`scan_candidates` 本身永不变异，返回全部候选调用位置及 AST 结构/原因清单；
候选数量是调用位置数量，每位置保留原 MR 的首个安全依赖边/替换目标，不是所有编辑组合数。
PARSE_FAILED、SCAN_FAILED、SCAN_TIMEOUT、WORKER_FAILED 等失败不计入适用率有效分母。
真实报告及逐种子 JSON/CSV 位于 `evidence/stage2b/corpus/`。


阶段 2C：已完成同一批 50 个种子的只读语义画像和最多三条 MR 的设计筛选。
见 [语义画像](docs/STAGE2C_SEMANTIC_PROFILE.md) 与 [候选比较](docs/STAGE2C_MR_CANDIDATES.md)。
优先建议研究已有成员的可取地址属性变化；它尚未正式实现，完整安全候选数和适用率仍未知。
没有将成员取地址等结构频率当成 MR 适用率。

复现画像（复用阶段 2B 原始清单与合法性回执，先校验源哈希；输出目录必须新建）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.semantic_profile_run \
  --out-dir second_point/evidence/stage2c/my_profile --timeout 90
```

机器结果为 JSON/CSV，当前正式结果在 `evidence/stage2c/final_corpus/`。
解析失败、工作进程异常和超时单列；PROFILED 只表示画像完成，不表示任何新 MR 可用。

## 阶段 2D：合法种子预处理

[验证报告](docs/STAGE2D_ENRICHMENT_VALIDATION.md)区分原生安全候选与增强后的潜在入口。
只实现普通 int 只读参数经成员地址/解引用返回原运算的合法预处理，不生成 P1。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.enrichment_run \
  --out-dir second_point/evidence/stage2d/reproduction --scan-timeout 45
```

输出目录必须不存在；只读扫描原阶段 2B 清单，固定前五个真实种子作合法增强。
报告中的 0 原生候选是当前保守证明子集的结果，不是语言层面的穷尽结论。

## 阶段 2F：原生依赖范围与布局独立性

本阶段固定复用阶段 2B 的 50 个原始种子，先运行默认阶段 2E 检查器，
再仅启用“无关 sizeof/_Alignof 的可重放独立性证明”。其他原生闭合限制不变。
实际统计、零结果及下一步建议见[阶段 2F 验收](docs/STAGE2F_NATIVE_DEPENDENCY_VALIDATION.md)。
语义上下文记录包括被拒绝字段，不将原始结构复杂性当作安全 MR 动作空间。

新克隆仓库先运行 `python3 second_point/tools/restore_evidence.py`，恢复无损归档的历史 JSON。
以下输出目录都必须尚不存在；不要直接覆盖已保存的阶段结果：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.native_dependency_run \
  --phase baseline --out-dir second_point/evidence/stage2f/reproduce_baseline
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.native_dependency_run \
  --phase manual --out-dir second_point/evidence/stage2f/reproduce_manual
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.native_dependency_run \
  --phase extended --out-dir second_point/evidence/stage2f/reproduce_extended
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover -s second_point/tests -v
```

阶段 2F 不新增增强模板、不实现其他 MR、Bandit、LLM 或奖励函数；默认阶段 2E 接口保持原有行为。

## 阶段 2G：主动合法增强和有效动作池

[验收报告](docs/STAGE2G_ACTIVE_SEMANTIC_ENRICHMENT.md)记录固定 50 个 S 的真实实验。
S 为原始程序，P0 为合法增强程序，P1 为已有正式 MR 的非法结果；全部候选标为 Enriched。
三个受控结构族连接原始只读 int/unsigned int 参数与原计算：同作用域指针、嵌套块双地址、typedef 回调值传递。
默认原生检查器保持原有保守行为；新支持范围必须显式启用。

从仓库根目录运行以下命令；复现目录必须不存在，旧结果禁止覆盖。先恢复前述历史 JSON，
并保证固定的原始种子路径、runtime 和指定工具链可用。`source_snapshots/` 保存本次 50 个 S 的精确副本，
`source_snapshot_index.json` 给出其原路径与 SHA256；不会自动写回原种子目录。

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=second_point/src
python3 -m edgm_second.active_run --phase init --out-dir second_point/evidence/stage2g_reproduction
python3 second_point/tools/stage2g_manual.py --out-dir second_point/evidence/stage2g_reproduction
python3 -m edgm_second.active_run --phase prepare --out-dir second_point/evidence/stage2g_reproduction
python3 -m edgm_second.active_run --phase verify --out-dir second_point/evidence/stage2g_reproduction
python3 second_point/tools/stage2g_audit.py --out-dir second_point/evidence/stage2g_reproduction
python3 -m unittest discover -s second_point/tests -v
```

准备阶段每个 S/结构族最多保留两个位置，优先不同函数；验证前先按 S+MR+规范化上下文去重。
验证阶段按预定轮转规则，每族最多选择 10 个不同种子；Oracle 保存并重放后才编译 P1。
原始位置枚举、排除原因、所有 P0、去重排除项和全部实验结果均保留，不依据诊断补选。
这是受控局部语义保持与闭合约束证明，不是任意 C11 程序的全局等价或无 UB 证明。
阶段 2G 不实现学习模块或新 MR；const 方向留待另行授权的阶段 2H。

## 阶段 2H：const 聚合赋值 MR 与全池验证

本阶段显式支持局部普通整型字段，以及经一层真实结构体包含边递归传播 const 的受控子集。
正式 MR 通过 AST 身份扫描 P0，仅给叶字段声明增加 const；初始化和远端整体赋值保持原字节。
全部 2G 动作和去重后的新增动作均执行，不根据诊断抽样或补选。Oracle 先保存、重放，再编译 P1。
历史证据保持只读，旧 P0 引用路径和 SHA，不复制。每个动作有本批次的 P0 准入和双编译诊断原文。

以下目录须不存在；从仓库根运行，先恢复历史归档 JSON，并准备固定原种子和指定工具链路径。
阶段 2G 的 source_snapshots 可核对 S；脚本不自动覆盖原种子目录。

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=second_point/src
mkdir second_point/evidence/stage2h_reproduction
python3 -m edgm_second.aggregate_run --phase setup --out-dir second_point/evidence/stage2h_reproduction
python3 second_point/tools/stage2h_manual.py --out-dir second_point/evidence/stage2h_reproduction
python3 -m edgm_second.aggregate_run --phase prepare --out-dir second_point/evidence/stage2h_reproduction
python3 -m edgm_second.aggregate_run --phase execute --out-dir second_point/evidence/stage2h_reproduction
python3 second_point/tools/stage2h_audit.py --out-dir second_point/evidence/stage2h_reproduction
python3 -m unittest discover -s second_point/tests -v
```

`combined_candidate_pool.json` 保留稳定身份和规范化上下文；`selection_before_compilation.json` 冻结执行顺序。
异常候选按同环境重复编译并保留两次证据；Oracle 无效、worker 超时、编译器超时/崩溃与普通诊断差异分开记录。
这不是完整 C11 验证器或通用诊断比较器。阶段 2H 后停止，不自动增加第四条 MR、奖励、Bandit 或 LLM。
