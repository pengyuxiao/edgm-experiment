"""Main-file AST snapshots. Coordinates and half-open extents use bytes."""
from pathlib import Path
import hashlib


def parse_translation_unit(path, runtime, detailed_processing=False):
    """Shared P0 parser; callers must check diagnostics before using the tree."""
    from clang import cindex
    return cindex.Index.create().parse(str(Path(path).resolve()),
        args=['-x', 'c', '-std=c11', '-w', '-I', str(runtime)],
        options=(cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD if detailed_processing else 0))


def main_file_nodes(tu, path):
    """Yield (cursor, parent) including TU; prune all header subtrees."""
    from clang import cindex
    path = Path(path).resolve()
    stack = [(tu.cursor, None)]
    while stack:
        node, parent = stack.pop()
        if node.kind != cindex.CursorKind.TRANSLATION_UNIT:
            if not node.location.file or Path(node.location.file.name).resolve() != path:
                continue
        yield node, parent
        stack.extend((child, node) for child in reversed(list(node.get_children())))


def extract_ast(path, runtime):
    path = Path(path).resolve()
    result = dict(source=str(path), status='parse_failed', nodes=[], diagnostics=[],
                  args=['-x', 'c', '-std=c11', '-w', '-I', str(runtime)],
                  coordinate_system='1-based physical byte line/column; 0-based byte offsets; [start,end)',
                  depth_convention='TranslationUnit=0, direct child=1')
    try:
        from clang import cindex
        data = path.read_bytes()
        result['sha256'] = hashlib.sha256(data).hexdigest()
        tu = parse_translation_unit(path, runtime)
        result['diagnostics'] = [dict(severity=d.severity, spelling=d.spelling,
            text=str(d)) for d in tu.diagnostics]
        if any(d.severity >= cindex.Diagnostic.Error for d in tu.diagnostics):
            result['status'] = 'parse_error'
            return result

        def main_file(location):
            return location.file is not None and Path(location.file.name).resolve() == path

        stack = [(tu.cursor, 0, None)]
        while stack:
            node, depth, parent = stack.pop()
            # TU has no file: always traverse it. Exclude header subtrees.
            if node.kind != cindex.CursorKind.TRANSLATION_UNIT and not main_file(node.location):
                continue
            current = parent
            if node.kind != cindex.CursorKind.TRANSLATION_UNIT:
                start, end = node.extent.start, node.extent.end
                if not (main_file(start) and main_file(end) and 0 <= start.offset <= end.offset <= len(data)):
                    raise ValueError('non-main-file or invalid extent')
                location = node.location
                before = data[:location.offset]
                physical_line = before.count(b'\n') + 1
                physical_col = len(before.rsplit(b'\n', 1)[-1]) + 1
                current = len(result['nodes'])
                result['nodes'].append(dict(id=current, parent_id=parent, kind=node.kind.name,
                    spelling=node.spelling, file=str(path), line=physical_line, column=physical_col,
                    offset=location.offset, start_offset=start.offset, end_offset=end.offset, depth=depth,
                    clang_line=location.line, clang_column=location.column))
            children = list(node.get_children())
            stack.extend((child, depth + 1, current) for child in reversed(children))
        result['status'] = 'ok' if result['nodes'] else 'empty_ast'
    except Exception as exc:
        result['exception'] = f'{type(exc).__name__}: {exc}'
    return result
