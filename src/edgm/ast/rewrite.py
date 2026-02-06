from pathlib import Path
from typing import List, Tuple


class SourceRewriter:
    """
    一个简单的源码重写工具，用字节偏移来做区间替换。

    用法示例：
        r = SourceRewriter("a.c")
        r.replace_range(start, end, ";")
        r.apply("a.mut.c")
    """

    def __init__(self, src_path: str):
        self.src_path = str(src_path)
        # 用二进制模式读，偏移跟 clang 的 offset 能对得上最保险
        self._original_bytes: bytes = Path(self.src_path).read_bytes()
        self._edits: List[Tuple[int, int, bytes]] = []

    def replace_range(self, start: int, end: int, replacement: str):
        """
        将 [start, end) 这个区间替换成 replacement。
        start / end 使用 clang Cursor.extent.start.offset / end.offset。
        """
        if start < 0 or end < 0:
            raise ValueError(f"offset 不能是负数: start={start}, end={end}")
        if start > end:
            raise ValueError(f"start 不能大于 end: start={start}, end={end}")
        if end > len(self._original_bytes):
            raise ValueError(
                f"end 超出文件长度: end={end}, len={len(self._original_bytes)}"
            )

        rep_bytes = replacement.encode("utf-8")
        self._edits.append((start, end, rep_bytes))

    def apply(self, dst_path: str):
        """
        应用所有编辑并写入 dst_path。
        要求各编辑区间不重叠，否则会报错。
        """
        if not self._edits:
            # 没有修改，直接拷贝原文件
            Path(dst_path).write_bytes(self._original_bytes)
            return

        # 按起始位置排序，确保从前往后拼接
        edits = sorted(self._edits, key=lambda e: e[0])

        # 检查是否重叠
        last_end = 0
        for start, end, _ in edits:
            if start < last_end:
                raise ValueError(
                    f"检测到重叠编辑区间: current=({start},{end}), last_end={last_end}"
                )
            last_end = end

        out_parts: List[bytes] = []
        cursor = 0
        data = self._original_bytes

        for start, end, rep_bytes in edits:
            # 先把 [cursor, start) 之间的原始内容拷贝过去
            out_parts.append(data[cursor:start])
            # 再放入替换内容
            out_parts.append(rep_bytes)
            # 移动 cursor
            cursor = end

        # 最后一段原始内容
        out_parts.append(data[cursor:])

        new_bytes = b"".join(out_parts)
        Path(dst_path).write_bytes(new_bytes)
