# 大型历史证据的无损归档

为通过 GitHub 上传接口的体积限制，以下三份历史 JSON 以同目录的 `.json.gz` 保存：

- `stage2b/corpus/records.json`
- `stage2b/corpus/reproducible_results.json`
- `stage2c/final_corpus/records.json`

压缩不改变 JSON 内容。`archive_manifest.json` 保存原文件及压缩文件的完整 SHA256 和字节数。
既有报告、代码和来源映射仍使用原始 `.json` 路径；新克隆仓库后，在复现实验或运行测试之前，
从仓库根目录执行：

```bash
python3 second_point/tools/restore_evidence.py
```

工具校验压缩文件及解压后内容，独占创建缺失的原文件；已有内容一致时跳过，不覆盖内容不同的文件。
恢复后的大型 JSON 被 Git 忽略，归档文件受版本控制。仅检查归档和已恢复文件可使用：

```bash
python3 second_point/tools/restore_evidence.py --check
```

这些是归档与传输调整，不是重新执行实验，也没有改写原始证据或既有实验哈希。
