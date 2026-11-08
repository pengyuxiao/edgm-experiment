# 阶段 0/1 验收记录

日期：2026-11-08，Asia/Shanghai。所有执行均在现有 WSL 中完成。

## 实际执行的关键命令

工作目录 `/home/pyx/edgm-experiment`。先用 `rg --files`、`cat`、`sed`、`diff -u`
阅读代码，以 `git status --short` 保存/比较初始工作区状态；没有运行旧生成、过滤、变异、奖励或大实验入口。

环境核实：

```bash
command -v gcc clang python3
gcc -dumpfullversion
clang --version
venv/bin/python -c 'from clang import cindex; print(cindex.Index.create())'
python3 -c 'from clang import cindex; print(cindex.__file__); print(cindex.Index.create())'
ls src/csmithedge/csmith/runtime/csmith.h
```

真实结果：GCC 15.1.0，Clang 22.1.0，runtime 存在；venv 的命令报 ModuleNotFoundError，
系统 Python 成功创建 Index。程序探测进一步记录 libclang 实际路径/版本为 18.1.3，见下述报告。
没有安装、升级或替换组件。

真实 P0 检查和 AST 提取：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m edgm_second.cli \
  --output second_point/evidence/stage1_run.json
```

退出码 0；选择已有 `data/seeds/diverse/clean_diversify_syntax/seed_0001_997117443.c`，
GCC/Clang 返回码均 0，记录命令/耗时/原始文本 stderr，提取 21,050 个主文件 AST 节点。
默认第一个 clean 目录为空已记录，随后使用已有 diversified clean 目录，不是生成新种子。
完整结果见 `evidence/stage1_run.json`，包含约 8.9 MiB 的 AST，不是大规模实验。

最终自动化测试：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=second_point/src python3 -m unittest discover \
  -s second_point/tests -v > second_point/evidence/unittest.txt 2>&1
```

退出码 0：`Ran 11 tests in 1.304s`，`OK`，没有 skip。
此前 9 项测试也通过；新增输出保护与失败报告测试后重新执行了以上最终 11 项。

额外用临时无效程序 `int main(void) { return undeclared; }` 调用 CLI 的同一个 main，
要求其返回 1，报告保存为 `evidence/failure_run.json`。
报告 success=false、validation=rejected、AST=not_run_invalid_p0；双编译器原始 stderr 均保留。
该临时程序只是失败路径样例，不是本次实现了 MR 或 CsmithEdge 生成。

最后用 Python hashlib 对原树快照逐项比较，并执行 `git status --short`：
468 个文件内容一致，原树中新增/删除/修改列表均为空，见 `evidence/readonly_check.json`。
原始的 git 删除记录、未跟踪 backups 保持原状，新增内容仅 second_point。

## 每项测试结果

| 测试 | 真实结果 | 检查内容 |
|---|---|---|
| test_environment_record | PASS | 实际路径、版本、runtime、libclang 和 JSON 可记录性 |
| test_dual_compiler_and_source_immutable | PASS | 最小合法 P0 双编译器返回 0、固定 flags、耗时和 stderr、输入 bytes 不变 |
| test_ast_utf8_byte_positions_and_depth | PASS | 中文注释后的字节偏移/列、局部变量精确行列与 extent、父子深度 |
| test_header_same_basename_filtered_and_tu_traversed | PASS | 不同目录同名被包含文件排除、TU 根仍遍历到 main |
| test_rejection_and_ast_errors | PASS | 真实错误程序被拒绝、error AST 不输出有效节点、缺文件显式记录 |
| test_execution_failure_and_timeout_preserve_partial_output | PASS | 真实不存在执行文件；模拟超时保留部分 stdout/stderr 与 null 返回码 |
| test_missing_environment_record | PASS | 不存在编译器/runtime 的失败报告；不会把 P0 标为 valid |
| test_existing_edgm_seed_read_only | PASS | 真实原种子双编译验证且前后哈希一致 |
| test_missing_seed_pool_uses_explicit_fixture | PASS | 空目录情况下子进程实际跑通临时最小 P0；明确不是 CsmithEdge 结果 |
| test_cli_rejected_p0_is_recorded_without_ast | PASS | 子进程退出 1、失败 JSON 和原始 stderr、阻止无效 P0 的 AST 阶段 |
| test_cli_refuses_existing_and_external_output | PASS | 子进程退出 2，保护已存在文件及新工程外输出 |

超时测试使用模拟异常，不声称制造了真实编译器超时。其余合法/非法编译用真实编译器。
没有运行旧 EDGM 测试集，未声称其通过。

## 新增文件

全部位于 second_point：

- `README.md`、`pyproject.toml`、`.gitignore`、`config/environment.json`。
- `src/edgm_second/__init__.py`、`environment.py`、`seeds.py`、`ast_extract.py`、`cli.py`。
- `tests/test_foundation.py`。
- `docs/EDGM_CODE_REVIEW.md`、`RESEARCH_DESIGN.md`、本验收记录。
- `evidence/original_hashes.json`、`readonly_check.json`、`stage1_run.json`、`failure_run.json`、`unittest.txt`。

## 重要发现及未解决项

1. 旧 48/83 比较器忽略列、容许 unknown、双空结果可能被认为一致；49 使用另一套 Counter 签名。
   这是阅读发现，未修改旧框架；第二点的严格 Oracle 比较留待后续阶段。
2. 部分 advanced 算子实际使用 regex，另有真正使用 USR/类型的 AST 算子，不能一概归类。
3. 默认 80/62 是 bootstrap_legacy，92 才有明确持久化反馈的 corrected 策略入口；
   当前 81 文件没有 TS 状态更新调用，不能凭其命名宣称更新生效。
4. 现有 clean 为空，但 diversified clean 有数据，本阶段没有种子阻塞；只重新验证了一个现有样本。
5. 默认 libclang 18.1.3 与被测 Clang 22.1.0 不同，venv 又缺 clang Python 绑定。
   系统 Python 已满足本阶段测试；保留原环境，后续需评估 AST 工具版本对目标语义/扩展的影响。
6. 双编译器接受不构成完整 C11 合法性/无 UB 证明；宏/#line 诊断坐标映射与语义依赖图尚未实现。
   当前验证只证明列出的样例和接口，不代表全部 50 个种子或所有语言结构均通过。

下一阶段建议优先设计并小规模验证“typedef 链—结构体函数指针成员—间接调用的按值结构体实参不兼容”MR，
先给出语言约束出处、前置条件、依赖见证和独立 Oracle，再接入诊断检查。具体提案见设计文档。

阶段 0 与阶段 1 已完成；本次停止，没有开始阶段 2。
