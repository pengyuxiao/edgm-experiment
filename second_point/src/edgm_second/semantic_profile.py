"""Read-only semantic relations. No transformation, Oracle construction or learning.

Edges use cursor/declaration identity, not identifier spelling. Counts are AST
occurrences; samples are bounded and do not pretend to be a complete edge dump.
"""
from bisect import bisect_right
from collections import Counter, defaultdict
import ctypes
from pathlib import Path
import time
from clang import cindex as c
from .ast_extract import parse_translation_unit, main_file_nodes
from .source_edit import digest

K, T = c.CursorKind, c.TypeKind
INTS = {T.BOOL, T.CHAR_U, T.UCHAR, T.CHAR_S, T.SCHAR, T.USHORT, T.SHORT, T.UINT, T.INT,
        T.ULONG, T.LONG, T.ULONGLONG, T.LONGLONG, T.UINT128, T.INT128, T.ENUM}
ARRAYS = {T.CONSTANTARRAY, T.INCOMPLETEARRAY, T.VARIABLEARRAY, T.DEPENDENTSIZEDARRAY}
OPS = {K.BINARY_OPERATOR, K.COMPOUND_ASSIGNMENT_OPERATOR, K.UNARY_OPERATOR, K.CSTYLE_CAST_EXPR,
       K.CALL_EXPR, K.ARRAY_SUBSCRIPT_EXPR}
ASSIGNS = {'=', '+=', '-=', '*=', '/=', '%=', '<<=', '>>=', '&=', '|=', '^='}
METRICS = ['typedef_declarations', 'typedef_type_uses', 'functions', 'function_definitions', 'parameters',
    'main_function_calls', 'external_function_calls', 'structs', 'unions', 'fields', 'member_accesses',
    'pointer_declarations', 'dereferences', 'indirect_writes', 'qualified_declarations', 'qualified_uses',
    'arrays', 'array_dimensions', 'array_subscripts', 'integer_literals', 'ice_subset_expressions',
    'enum_constants', 'enum_references', 'switches', 'case_labels', 'default_labels', 'nested_switches',
    'gotos', 'labels', 'local_scopes', 'bitfields', 'integer_operations', 'explicit_casts',
    'implicit_type_changes', 'writes', 'address_of_members', 'record_assignments', 'pointer_address_bindings']
RELATIONS = ['typedef_to_type_use', 'typedef_to_operation', 'formal_call_argument', 'member_to_operation',
    'pointer_to_indirect_write', 'qualified_decl_to_use', 'constant_to_array_bound', 'constant_to_ice_context',
    'switch_to_label', 'goto_to_label', 'bitfield_width_to_field', 'integer_operand_to_operation',
    'array_decl_to_subscript', 'member_to_address', 'record_type_to_assignment', 'address_binding',
    'nested_pointer_formal_actual', 'record_contains_record', 'member_to_access',
    'pointer_to_dereference', 'enum_to_reference', 'function_redeclaration', 'base_decl_to_subscript',
    'bound_declaration_dependency']


class CursorIndex:
    """cindex Cursor is unhashable; buckets still compare true cursor identity."""
    def __init__(self, nodes):
        self.buckets = defaultdict(list)
        for i, node in enumerate(nodes):
            self.buckets[node.hash].append((node, i))

    def get(self, node, default=None):
        if node is not None:
            for candidate, i in self.buckets.get(node.hash, []):
                if candidate == node:
                    return i
        return default

    def __contains__(self, node):
        return self.get(node) is not None

    def __getitem__(self, node):
        result = self.get(node)
        if result is None:
            raise KeyError('cursor is not in main-file AST')
        return result


def usr(node):
    return node.canonical.get_usr() if node else ''


def canonical(type_):
    return type_.get_canonical()


def type_shape(type_):
    levels, current, arrays = [], canonical(type_), []
    for _ in range(32):
        levels.append(dict(kind=current.kind.name, const=current.is_const_qualified(),
                           volatile=current.is_volatile_qualified(), restrict=current.is_restrict_qualified()))
        if current.kind == T.POINTER:
            current = current.get_pointee()
        elif current.kind in ARRAYS:
            arrays.append(current.get_array_size() if current.kind == T.CONSTANTARRAY else None)
            current = current.get_array_element_type()
        else:
            break
    return dict(spelling=type_.spelling, canonical_kind=canonical(type_).kind.name,
        pointer_depth=sum(l['kind'] == 'POINTER' for l in levels), array_bounds=arrays, levels=levels,
        terminal_kind=current.kind.name, terminal_declaration_usr=usr(current.get_declaration()))


def evaluate_integer(node):
    """P0 AST value observation only. Evaluation success alone is NOT C11 ICE proof."""
    lib = c.conf.lib
    lib.clang_Cursor_Evaluate.argtypes = [c.Cursor]
    lib.clang_Cursor_Evaluate.restype = ctypes.c_void_p
    lib.clang_EvalResult_getKind.argtypes = [ctypes.c_void_p]
    lib.clang_EvalResult_getKind.restype = ctypes.c_int
    lib.clang_EvalResult_isUnsignedInt.argtypes = [ctypes.c_void_p]
    lib.clang_EvalResult_isUnsignedInt.restype = ctypes.c_uint
    lib.clang_EvalResult_getAsLongLong.argtypes = [ctypes.c_void_p]
    lib.clang_EvalResult_getAsLongLong.restype = ctypes.c_longlong
    lib.clang_EvalResult_getAsUnsigned.argtypes = [ctypes.c_void_p]
    lib.clang_EvalResult_getAsUnsigned.restype = ctypes.c_ulonglong
    lib.clang_EvalResult_dispose.argtypes = [ctypes.c_void_p]
    value = lib.clang_Cursor_Evaluate(node)
    if not value:
        return None
    try:
        if lib.clang_EvalResult_getKind(value) != 1:
            return None
        return int(lib.clang_EvalResult_getAsUnsigned(value) if lib.clang_EvalResult_isUnsignedInt(value)
                   else lib.clang_EvalResult_getAsLongLong(value))
    finally:
        lib.clang_EvalResult_dispose(value)


class Profiler:
    def __init__(self, path, runtime):
        self.path = Path(path).resolve()
        self.data = self.path.read_bytes()
        self.sha = digest(self.data)
        self.tu = parse_translation_unit(self.path, runtime)
        self.diagnostics = [dict(severity=d.severity, text=str(d)) for d in self.tu.diagnostics]
        if any(d['severity'] >= c.Diagnostic.Error for d in self.diagnostics):
            raise ProfileParseError(self.diagnostics)
        pairs = list(main_file_nodes(self.tu, self.path))
        self.nodes = [n for n, _ in pairs]
        indices = CursorIndex(self.nodes)
        self.parent = [indices.get(p, -1) for _, p in pairs]
        self.children = [[] for _ in pairs]
        for i, p in enumerate(self.parent):
            if p >= 0:
                self.children[p].append(i)
        self.index = indices
        self.declarations = defaultdict(list)
        for i, n in enumerate(self.nodes):
            if n.kind.is_declaration() and usr(n):
                self.declarations[usr(n)].append(i)
        self.main_usrs = set(self.declarations)
        self.newlines = [-1] + [i for i, b in enumerate(self.data) if b == 10]
        self.metric = {key: dict(instances=0, dependency_relations=0, examples=[]) for key in METRICS}
        self.relation = {key: dict(instances=0, examples=[]) for key in RELATIONS}
        self.hist = defaultdict(Counter)
        self.functions = {}
        self.aliases = {}
        self.switch_entries = {}
        self.operator_cache = {}
        self.shape_cache = {}
        self.ice_cache = {}
        self.details = dict(typedefs=[], arrays=[], functions=[], switches=[], bitfields=[], unresolved_operators=0,
                            incomplete_alias_analysis='direct address bindings only; no points-to or reaching-definitions proof')
        self.function_context, self.scope_context, self.switch_context = [], [], []
        for i, n in enumerate(self.nodes):
            p = self.parent[i]
            self.function_context.append(i if n.kind == K.FUNCTION_DECL else self.function_context[p] if p >= 0 else None)
            self.scope_context.append(i if n.kind == K.COMPOUND_STMT else self.scope_context[p] if p >= 0 else None)
            self.switch_context.append(i if n.kind == K.SWITCH_STMT else self.switch_context[p] if p >= 0 else None)

    def anchor(self, index):
        n = self.nodes[index]
        offset = n.location.offset
        line = bisect_right(self.newlines, offset - 1)
        return dict(file=str(self.path), line=line, column=offset - self.newlines[line - 1], offset=offset,
            start_offset=n.extent.start.offset, end_offset=n.extent.end.offset, kind=n.kind.name,
            usr=usr(n), referenced_usr=usr(n.referenced), node_id=f'{n.kind.name}:{n.extent.start.offset}:{n.extent.end.offset}',
            scope_id=self.scope_context[index], function_id=self.function_context[index])

    def count(self, name, index, extra=None, dependencies=0):
        entry = self.metric[name]
        entry['instances'] += 1
        entry['dependency_relations'] += dependencies
        if len(entry['examples']) < 3:
            entry['examples'].append(dict(anchor=self.anchor(index), **(extra or {})))

    def edge(self, name, source, target, extra=None, via=None):
        entry = self.relation[name]
        entry['instances'] += 1
        if len(entry['examples']) < 3:
            entry['examples'].append(dict(source=self.anchor(source), target=self.anchor(target),
                via=[self.anchor(i) for i in (via or [])], **(extra or {})))

    def unwrap(self, i):
        while self.nodes[i].kind in (K.UNEXPOSED_EXPR, K.PAREN_EXPR) and len(self.children[i]) == 1:
            i = self.children[i][0]
        return i

    def descendants(self, i):
        stack = [i]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(reversed(self.children[item]))

    def nearest_operation(self, index):
        i = self.parent[index]
        while i >= 0 and self.nodes[i].kind.is_expression():
            if self.nodes[i].kind in OPS:
                return i
            i = self.parent[i]
        return None

    def operator(self, i):
        if i in self.operator_cache:
            return self.operator_cache[i]
        n, kids = self.nodes[i], self.children[i]
        value = None
        if n.kind in (K.BINARY_OPERATOR, K.COMPOUND_ASSIGNMENT_OPERATOR) and len(kids) == 2:
            a, b = self.nodes[kids[0]], self.nodes[kids[1]]
            if 0 <= a.extent.end.offset <= b.extent.start.offset <= len(self.data):
                text = self.data[a.extent.end.offset:b.extent.start.offset].strip().decode('utf-8')
                if text in ASSIGNS | {'+', '-', '*', '/', '%', '<<', '>>', '&', '|', '^', '<', '>', '<=', '>=', '==', '!=', '&&', '||', ','}:
                    value = text
        elif n.kind == K.UNARY_OPERATOR and len(kids) == 1:
            child = self.nodes[kids[0]]
            for a, b in ((n.extent.start.offset, child.extent.start.offset), (child.extent.end.offset, n.extent.end.offset)):
                if 0 <= a <= b <= len(self.data):
                    text = self.data[a:b].strip().decode('utf-8')
                    if text in {'*', '&', '+', '-', '~', '!', '++', '--'}:
                        value = text
                        break
        if value is None:
            self.details['unresolved_operators'] += 1
        self.operator_cache[i] = value
        return value

    def alias_chain(self, type_):
        chain, seen, current = [], set(), type_
        for _ in range(64):
            declaration = current.get_declaration()
            identity = usr(declaration)
            if declaration.kind == K.TYPEDEF_DECL:
                if identity in seen:
                    break
                seen.add(identity)
                if identity in self.main_usrs:
                    chain.append(identity)
                current = declaration.underlying_typedef_type
            elif current.kind == T.ELABORATED:
                current = current.get_named_type()
            elif current.kind == T.POINTER:
                current = current.get_pointee()
            elif current.kind in ARRAYS:
                current = current.get_array_element_type()
            else:
                break
        return chain

    def decl_shape(self, identity):
        if identity not in self.shape_cache:
            self.shape_cache[identity] = type_shape(self.nodes[self.declarations[identity][0]].type)
        return self.shape_cache[identity]

    def ice_subset(self, i):
        if i in self.ice_cache:
            return self.ice_cache[i]
        n, kids = self.nodes[i], self.children[i]
        value = False
        if n.kind in (K.INTEGER_LITERAL, K.CHARACTER_LITERAL):
            value = True
        elif n.kind == K.DECL_REF_EXPR:
            value = bool(n.referenced and n.referenced.kind == K.ENUM_CONSTANT_DECL)
        elif n.kind in (K.UNEXPOSED_EXPR, K.PAREN_EXPR):
            value = len(kids) == 1 and self.ice_subset(kids[0])
        elif n.kind == K.UNARY_OPERATOR:
            value = self.operator(i) in {'+', '-', '~', '!'} and all(self.ice_subset(k) for k in kids)
        elif n.kind == K.BINARY_OPERATOR:
            value = self.operator(i) in {'+', '-', '*', '/', '%', '<<', '>>', '&', '|', '^', '<', '>', '<=', '>=', '==', '!=', '&&', '||'} and all(self.ice_subset(k) for k in kids)
        elif n.kind == K.CSTYLE_CAST_EXPR:
            exprs = [k for k in kids if self.nodes[k].kind.is_expression()]
            value = canonical(n.type).kind in INTS and len(exprs) == 1 and self.ice_subset(exprs[0])
        self.ice_cache[i] = value
        return value

    def constant_refs(self, i):
        return [j for j in self.descendants(i) if self.nodes[j].kind == K.DECL_REF_EXPR and self.nodes[j].referenced
                and self.nodes[j].referenced.kind in (K.VAR_DECL, K.ENUM_CONSTANT_DECL) and usr(self.nodes[j].referenced) in self.main_usrs]

    def add_constant_edges(self, expression, target, relation):
        count = 0
        for ref in self.constant_refs(expression):
            declaration = self.nodes[ref].referenced
            identity = usr(declaration)
            source = self.declarations[identity][0]
            is_enum = declaration.kind == K.ENUM_CONSTANT_DECL
            is_const = canonical(declaration.type).is_const_qualified()
            if relation == 'constant_to_array_bound' and not (is_enum or is_const):
                continue
            self.edge(relation, source, target, dict(reference=self.anchor(ref), enum_constant=is_enum,
                const_object=is_const, c11_ice_reference=is_enum, note='C const object is not thereby an ICE'), via=[expression])
            count += 1
        return count

    def bound_expressions(self, i):
        """AST expression children bounded by declarator bracket token intervals."""
        n = self.nodes[i]
        brackets, stack = [], []
        for token in n.get_tokens():
            if token.spelling == '=' and not stack:
                break
            if token.spelling == '[':
                stack.append(token.extent.end.offset)
            elif token.spelling == ']' and stack:
                brackets.append((stack.pop(), token.extent.start.offset))
        result = []
        for a, b in brackets:
            children = [j for j in self.children[i] if self.nodes[j].kind.is_expression()
                        and a <= self.nodes[j].extent.start.offset < self.nodes[j].extent.end.offset <= b]
            if children:
                result.append(children[0])
        return result

    def run(self):
        # Declaration tables are built before references, so forward declarations resolve by identity.
        for i, n in enumerate(self.nodes):
            if n.kind == K.TYPEDEF_DECL:
                chain = self.alias_chain(n.type)
                self.aliases[usr(n)] = chain
                self.count('typedef_declarations', i)
                self.hist['typedef_chain_length'][len(chain)] += 1
                self.hist['typedef_root_kind'][canonical(n.underlying_typedef_type).kind.name] += 1
                self.details['typedefs'].append(dict(anchor=self.anchor(i), chain=chain,
                    root_kind=canonical(n.underlying_typedef_type).kind.name))
            if n.kind == K.FUNCTION_DECL:
                self.count('functions', i)
                if n.is_definition():
                    self.count('function_definitions', i)
                if usr(n) not in self.functions or n.is_definition():
                    self.functions[usr(n)] = i
        for identity, definition in self.functions.items():
            for declaration in self.declarations[identity]:
                if declaration != definition and self.nodes[declaration].kind == K.FUNCTION_DECL:
                    self.edge('function_redeclaration', declaration, definition, dict(function_usr=identity,
                        canonical_types_equal=canonical(self.nodes[declaration].type) == canonical(self.nodes[definition].type)))
        for i, n in enumerate(self.nodes):
            kind, kids = n.kind, self.children[i]
            if kind == K.TYPE_REF and n.referenced and usr(n.referenced) in self.aliases:
                source = self.declarations[usr(n.referenced)][0]
                self.count('typedef_type_uses', i, dependencies=1)
                self.edge('typedef_to_type_use', source, i)
            if kind in (K.VAR_DECL, K.PARM_DECL, K.FIELD_DECL):
                shape = self.decl_shape(usr(n)) if usr(n) in self.main_usrs else type_shape(n.type)
                if kind == K.PARM_DECL:
                    self.count('parameters', i, dict(type=shape))
                if shape['pointer_depth']:
                    self.count('pointer_declarations', i, dict(type=shape))
                    self.hist['pointer_depth'][shape['pointer_depth']] += 1
                quals = [level for level in shape['levels'] if level['const'] or level['volatile'] or level['restrict']]
                if quals:
                    self.count('qualified_declarations', i, dict(type=shape))
                    for level, entry in enumerate(shape['levels']):
                        for qualifier in ('const', 'volatile', 'restrict'):
                            if entry[qualifier]:
                                self.hist['qualifier_level'][f'{qualifier}@{level}'] += 1
                if canonical(n.type).kind in ARRAYS:
                    self.count('arrays', i, dict(type=shape))
                    expressions = self.bound_expressions(i)
                    for size in shape['array_bounds']:
                        self.count('array_dimensions', i, dict(size=size))
                    bounds = []
                    for expr in expressions:
                        self.add_constant_edges(expr, i, 'bound_declaration_dependency')
                        count = self.add_constant_edges(expr, i, 'constant_to_array_bound')
                        bounds.append(dict(anchor=self.anchor(expr), value=evaluate_integer(self.nodes[expr]),
                            ice_syntax_subset=self.ice_subset(expr), declaration_dependencies=count))
                        self.metric['arrays']['dependency_relations'] += count
                    self.details['arrays'].append(dict(anchor=self.anchor(i), type=shape, bound_expressions=bounds))
                if kind == K.FIELD_DECL:
                    self.count('fields', i, dict(type=shape))
                    if canonical(n.type).kind == T.RECORD:
                        target = canonical(n.type).get_declaration()
                        if usr(target) in self.main_usrs:
                            self.edge('record_contains_record', self.declarations[usr(n.semantic_parent)][0],
                                      self.declarations[usr(target)][0], dict(field=self.anchor(i)))
                    if n.is_bitfield():
                        width = n.get_bitfield_width()
                        self.count('bitfields', i, dict(width=width, type=shape), dependencies=1)
                        exprs = [j for j in kids if self.nodes[j].kind.is_expression()]
                        if exprs:
                            self.edge('bitfield_width_to_field', exprs[0], i, dict(width=width))
                            self.add_constant_edges(exprs[0], i, 'constant_to_ice_context')
                        self.details['bitfields'].append(dict(anchor=self.anchor(i), width=width, type=shape))
            if kind in (K.STRUCT_DECL, K.UNION_DECL) and n.is_definition():
                self.count('structs' if kind == K.STRUCT_DECL else 'unions', i)
            if kind == K.DECL_REF_EXPR and n.referenced and usr(n.referenced) in self.main_usrs:
                identity = usr(n.referenced)
                source = self.declarations[identity][0]
                if n.referenced.kind in (K.VAR_DECL, K.PARM_DECL):
                    chain = self.alias_chain(n.referenced.type)
                    op = self.nearest_operation(i)
                    if chain and op is not None:
                        self.edge('typedef_to_operation', self.declarations[chain[0]][0], op,
                            dict(chain=chain, reference=self.anchor(i), variable_usr=identity), via=[source])
                        self.hist['typedef_operation_line_span'][abs(self.anchor(op)['line'] - self.anchor(self.declarations[chain[0]][0])['line'])] += 1
                    shape = self.decl_shape(identity)
                    if any(l['const'] or l['volatile'] or l['restrict'] for l in shape['levels']):
                        self.count('qualified_uses', i, dependencies=1)
                        self.edge('qualified_decl_to_use', source, i, dict(type=shape))
                if n.referenced.kind == K.ENUM_CONSTANT_DECL:
                    self.count('enum_references', i, dict(declaration_usr=identity), dependencies=1)
                    self.edge('enum_to_reference', source, i, dict(value=n.referenced.enum_value))
            if kind == K.CALL_EXPR:
                reference = n.referenced
                if reference and reference.kind == K.FUNCTION_DECL:
                    identity = usr(reference)
                    if identity in self.functions:
                        self.count('main_function_calls', i)
                        fn = self.functions[identity]
                        parameters = list(self.nodes[fn].get_arguments())
                        for position, argument in enumerate(n.get_arguments()):
                            if position >= len(parameters) or argument not in self.index:
                                continue
                            arg = self.index[argument]
                            param = self.index.get(parameters[position])
                            if param is None:
                                continue
                            raw = self.unwrap(arg)
                            info = dict(parameter_index=position, function_usr=identity,
                                parameter_type=type_shape(parameters[position].type), argument_type=type_shape(self.nodes[raw].type),
                                call=self.anchor(i), parameter_decl_usr=usr(parameters[position]))
                            self.edge('formal_call_argument', param, raw, info, via=[i])
                            self.metric['main_function_calls']['dependency_relations'] += 1
                            if info['parameter_type']['pointer_depth'] >= 2 and info['argument_type']['pointer_depth'] >= 2:
                                self.edge('nested_pointer_formal_actual', param, raw, info, via=[i])
                    else:
                        self.count('external_function_calls', i)
            if kind == K.MEMBER_REF_EXPR:
                self.count('member_accesses', i)
                reference = n.referenced
                op = self.nearest_operation(i)
                if reference and usr(reference) in self.main_usrs:
                    self.edge('member_to_access', self.declarations[usr(reference)][0], i,
                              dict(field_type=type_shape(reference.type)))
                if reference and usr(reference) in self.main_usrs and op is not None:
                    self.edge('member_to_operation', self.declarations[usr(reference)][0], op,
                              dict(member_access=self.anchor(i), field_type=type_shape(reference.type)))
                    self.metric['member_accesses']['dependency_relations'] += 1
            if kind == K.ARRAY_SUBSCRIPT_EXPR:
                self.count('array_subscripts', i)
                if kids:
                    base = self.unwrap(kids[0])
                    # Follow only the base spine, never merge same-named indices or unrelated operands.
                    while self.nodes[base].kind == K.ARRAY_SUBSCRIPT_EXPR and self.children[base]:
                        base = self.unwrap(self.children[base][0])
                    ref = self.nodes[base].referenced
                    if self.nodes[base].kind == K.DECL_REF_EXPR and ref and usr(ref) in self.main_usrs:
                        source = self.declarations[usr(ref)][0]
                        self.edge('base_decl_to_subscript', source, i, dict(declaration_type=type_shape(ref.type)))
                        if canonical(ref.type).kind in ARRAYS:
                            self.edge('array_decl_to_subscript', source, i, dict(declaration_type=type_shape(ref.type)))
                        self.metric['array_subscripts']['dependency_relations'] += 1
            if kind == K.INTEGER_LITERAL:
                self.count('integer_literals', i)
            if kind.is_expression() and kind in (K.INTEGER_LITERAL, K.CHARACTER_LITERAL, K.DECL_REF_EXPR,
                    K.UNARY_OPERATOR, K.BINARY_OPERATOR, K.CSTYLE_CAST_EXPR) and self.ice_subset(i):
                self.count('ice_subset_expressions', i)
            if kind == K.ENUM_CONSTANT_DECL:
                self.count('enum_constants', i, dict(value=n.enum_value))
                for child in kids:
                    if self.nodes[child].kind.is_expression():
                        self.add_constant_edges(child, i, 'constant_to_ice_context')
            if kind == K.COMPOUND_STMT:
                self.count('local_scopes', i)
            if kind == K.SWITCH_STMT:
                self.count('switches', i)
                entry = dict(anchor=self.anchor(i), control=self.anchor(kids[0]) if kids else None,
                             promoted_control_type=type_shape(self.nodes[kids[0]].type) if kids else None, labels=[])
                self.switch_entries[i] = entry
                self.details['switches'].append(entry)
                parent = self.parent[i]
                if parent >= 0 and self.switch_context[parent] is not None:
                    self.count('nested_switches', i)
            if kind in (K.CASE_STMT, K.DEFAULT_STMT):
                self.count('case_labels' if kind == K.CASE_STMT else 'default_labels', i)
                owner = self.switch_context[self.parent[i]] if self.parent[i] >= 0 else None
                if owner is not None:
                    value, converted, ctrl_shape = None, None, None
                    if kind == K.CASE_STMT and kids:
                        value = evaluate_integer(self.nodes[kids[0]])
                        self.add_constant_edges(kids[0], i, 'constant_to_ice_context')
                        control = self.children[owner][0]
                        type_ = canonical(self.nodes[control].type)
                        ctrl_shape = type_shape(type_)
                        bits = type_.get_size() * 8
                        if value is not None and bits > 0 and bits <= 128:
                            if type_.kind in {T.UINT,T.ULONG,T.ULONGLONG,T.UINT128}:
                                converted = value % (1 << bits)
                            elif -(1 << (bits-1)) <= value < (1 << (bits-1)):
                                converted = value
                    self.edge('switch_to_label', owner, i, dict(label_value=value, converted_value=converted,
                        promoted_control_type=ctrl_shape, conversion_assumption='target CHAR_BIT=8; out-of-range signed conversion unknown'))
                    self.switch_entries[owner]['labels'].append(dict(anchor=self.anchor(i), value=value,
                        converted_value=converted, kind=kind.name))
                    self.metric['switches']['dependency_relations'] += 1
            if kind == K.LABEL_STMT:
                self.count('labels', i)
            if kind == K.GOTO_STMT:
                self.count('gotos', i)
                ref = n.referenced
                if ref and ref in self.index:
                    self.edge('goto_to_label', i, self.index[ref], dict(same_function=
                        self.function_context[i] == self.function_context[self.index[ref]]))
                    self.metric['gotos']['dependency_relations'] += 1
            if kind == K.CSTYLE_CAST_EXPR:
                self.count('explicit_casts', i, dict(result_type=n.type.spelling))
            if kind == K.UNEXPOSED_EXPR and len(kids) == 1:
                child = self.nodes[kids[0]]
                if canonical(n.type) != canonical(child.type):
                    self.count('implicit_type_changes', i, dict(before=child.type.spelling, after=n.type.spelling), dependencies=1)
            if kind in (K.BINARY_OPERATOR, K.COMPOUND_ASSIGNMENT_OPERATOR, K.UNARY_OPERATOR):
                operator = self.operator(i)
                if canonical(n.type).kind in INTS:
                    self.count('integer_operations', i, dict(operator=operator))
                    for child in kids:
                        self.edge('integer_operand_to_operation', child, i, dict(operator=operator))
                        self.metric['integer_operations']['dependency_relations'] += 1
                if kind == K.UNARY_OPERATOR and operator == '*':
                    self.count('dereferences', i)
                    if kids:
                        for ref_index in self.descendants(kids[0]):
                            node = self.nodes[ref_index]
                            ref = node.referenced if node.kind == K.DECL_REF_EXPR else None
                            if ref and usr(ref) in self.main_usrs and canonical(ref.type).kind == T.POINTER:
                                self.edge('pointer_to_dereference', self.declarations[usr(ref)][0], i,
                                          dict(reference=self.anchor(ref_index), type=type_shape(ref.type)))
                                self.metric['dereferences']['dependency_relations'] += 1
                if kind == K.UNARY_OPERATOR and operator == '&' and kids:
                    operand = self.unwrap(kids[0])
                    ref = self.nodes[operand].referenced
                    if self.nodes[operand].kind == K.MEMBER_REF_EXPR and ref and usr(ref) in self.main_usrs:
                        self.count('address_of_members', i, dependencies=1)
                        self.edge('member_to_address', self.declarations[usr(ref)][0], i,
                                  dict(field_type=type_shape(ref.type), already_bitfield=ref.is_bitfield()))
                if operator in ASSIGNS or operator in {'++', '--'}:
                    self.count('writes', i)
                    if kids:
                        lhs = self.unwrap(kids[0])
                        target = self.nodes[lhs]
                        indirect = ((target.kind == K.UNARY_OPERATOR and self.operator(lhs) == '*') or
                                    target.kind == K.ARRAY_SUBSCRIPT_EXPR or
                                    (target.kind == K.MEMBER_REF_EXPR and self.children[lhs] and
                                     canonical(self.nodes[self.children[lhs][0]].type).kind == T.POINTER))
                        if indirect:
                            self.count('indirect_writes', i)
                            # Declaration-reference provenance, not a claim about alias points-to state.
                            base = self.children[lhs][0] if self.children[lhs] else lhs
                            for ref_index in self.descendants(base):
                                node = self.nodes[ref_index]
                                ref = node.referenced if node.kind == K.DECL_REF_EXPR else None
                                if ref and usr(ref) in self.main_usrs and canonical(ref.type).kind == T.POINTER:
                                    self.edge('pointer_to_indirect_write', self.declarations[usr(ref)][0], i,
                                              dict(reference=self.anchor(ref_index), type=type_shape(ref.type)), via=[lhs])
                                    self.metric['indirect_writes']['dependency_relations'] += 1
                        if operator == '=' and canonical(target.type).kind == T.RECORD:
                            self.count('record_assignments', i)
                            record = canonical(target.type).get_declaration()
                            if usr(record) in self.main_usrs:
                                self.edge('record_type_to_assignment', self.declarations[usr(record)][0], i,
                                          dict(lhs=self.anchor(lhs), record_usr=usr(record)))
            # Direct p=&object initializer/assignment binding, with exact referenced declarations.
            if kind == K.VAR_DECL and canonical(n.type).kind == T.POINTER:
                for child in kids:
                    expr = self.unwrap(child)
                    if self.nodes[expr].kind == K.UNARY_OPERATOR and self.operator(expr) == '&' and self.children[expr]:
                        obj = self.unwrap(self.children[expr][0])
                        ref = self.nodes[obj].referenced
                        if ref and usr(ref) in self.main_usrs:
                            self.count('pointer_address_bindings', i, dependencies=1)
                            self.edge('address_binding', self.declarations[usr(ref)][0], i, dict(kind='initializer_only',
                                binding_is_not_reaching_definition_proof=True), via=[expr])
        for identity, i in self.functions.items():
            self.details['functions'].append(dict(usr=identity, anchor=self.anchor(i),
                declaration_count=len(self.declarations[identity]),
                parameters=[dict(usr=usr(p), type=type_shape(p.type)) for p in self.nodes[i].get_arguments()],
                result_type=self.nodes[i].result_type.spelling))
        return dict(status='PROFILED', ast_status='OK', source=str(self.path), source_sha256=self.sha,
            main_file_nodes=len(self.nodes)-1, structures=self.metric, relations=self.relation,
            histograms={key:dict(value) for key,value in self.hist.items()}, details=self.details,
            diagnostics=self.diagnostics, confirmed_mr_candidates=None, mr_applicability='NOT_ASSESSED',
            sample_limit_per_seed=3)


class ProfileParseError(RuntimeError):
    def __init__(self, diagnostics):
        super().__init__('P0_AST_ERRORS')
        self.diagnostics = diagnostics


def profile_file(path, runtime, progress=None):
    started = time.perf_counter()
    try:
        profile = Profiler(path, runtime)
        if progress:
            progress(dict(event='ast_ready', ast_status='OK', main_file_nodes=len(profile.nodes)-1))
        result = profile.run()
        result['source_unchanged'] = digest(Path(path).read_bytes()) == profile.sha
        if not result['source_unchanged']:
            result['status'] = 'SOURCE_CHANGED'
    except ProfileParseError as exc:
        result = dict(status='PARSE_FAILED', ast_status='ERROR_DIAGNOSTICS', diagnostics=exc.diagnostics)
    except c.TranslationUnitLoadError as exc:
        result = dict(status='PARSE_FAILED', ast_status='LOAD_FAILED', exception=str(exc))
    except Exception as exc:
        result = dict(status='PROFILE_FAILED', ast_status='UNKNOWN', exception=f'{type(exc).__name__}: {exc}')
    result['elapsed_seconds'] = time.perf_counter()-started
    return result
