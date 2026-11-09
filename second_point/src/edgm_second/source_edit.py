"""A single checked byte edit and exact physical source mapping."""
from dataclasses import dataclass
from pathlib import Path
import hashlib


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ByteEdit:
    start: int
    end: int
    before: bytes
    after: bytes

    def apply(self, data):
        if not 0 <= self.start < self.end <= len(data) or data[self.start:self.end] != self.before:
            raise ValueError('edit does not match the original byte range')
        return data[:self.start] + self.after + data[self.end:]

    def map_offset(self, offset):
        if offset <= self.start:
            return offset
        if offset >= self.end:
            return offset + len(self.after) - len(self.before)
        raise ValueError('point inside replaced bytes has no identity mapping')


def anchor(path, data, offset, start, end, identity):
    if not 0 <= start <= offset <= end <= len(data):
        raise ValueError('invalid anchor range')
    prefix = data[:offset]
    return dict(file=str(Path(path).resolve()), line=prefix.count(b'\n') + 1,
                column=len(prefix.rsplit(b'\n', 1)[-1]) + 1, offset=offset,
                start_offset=start, end_offset=end, node_identity=identity,
                coordinate_system='physical byte columns, 1-based; byte offsets, 0-based',
                source_text=data[start:end].decode('utf-8'))
