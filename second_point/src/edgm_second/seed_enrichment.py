"""One legal seed preprocessing: route an immutable int parameter read via a member.

No MR/P1/Oracle implementation. All edits are exact bytes. Fail closed outside
an explicitly limited, locally provable C11 subset; compiler acceptance is a
separate experiment, never the semantic-preservation proof.
"""
from collections import Counter, defaultdict
from pathlib import Path
from clang import cindex as c
from .ast_extract import parse_translation_unit, main_file_nodes
from .semantic_profile import CursorIndex, usr
from .source_edit import ByteEdit, digest, anchor

K, T = c.CursorKind, c.TypeKind
READ_BINARY = {'+', '-', '*', '/', '%', '<<', '>>', '&', '|', '^', '<', '>', '<=', '>=', '==', '!=', '&&', '||', ','}


class ParseFailure(ValueError):
    pass


class Context:
    def __init__(self, path, runtime):
        self.path = Path(path).resolve()
        self.data = self.path.read_bytes()
        self.tu = parse_translation_unit(self.path, runtime, detailed_processing=True)
        self.diagnostics = [dict(severity=d.severity, text=str(d)) for d in self.tu.diagnostics]
        if any(d['severity'] >= 3 for d in self.diagnostics):
            raise ParseFailure(self.diagnostics)
        pairs = list(main_file_nodes(self.tu, self.path))
        self.nodes = [n for n, _ in pairs]
        self.index = CursorIndex(self.nodes)
        self.parents = [self.index.get(p, -1) for _, p in pairs]
        self.children = [[] for _ in pairs]
        self.refs = defaultdict(list)
        for i, n in enumerate(self.nodes):
            if self.parents[i] >= 0:
                self.children[self.parents[i]].append(i)
            if n.kind in (K.DECL_REF_EXPR, K.MEMBER_REF_EXPR, K.TYPE_REF):
                self.refs[usr(n.referenced)].append(i)
        self.macros = [(n.extent.start.offset, n.extent.end.offset) for n in self.nodes
                       if n.kind == K.MACRO_INSTANTIATION]
        # Includes header macro names: a fresh tag/member must not be macro-expanded.
        self.names = {n.spelling for n in self.tu.cursor.get_children() if n.spelling}
        self.names.update(n.spelling for n in self.nodes if n.spelling)
        self.names.update(t.spelling for t in self.tu.get_tokens(extent=self.tu.cursor.extent))

    def text(self, i):
        n = self.nodes[i]
        return self.data[n.extent.start.offset:n.extent.end.offset]

    def anchor(self, i, path=None, data=None):
        n = self.nodes[i]
        return dict(anchor(path or self.path, self.data if data is None else data,
            n.location.offset, n.extent.start.offset, n.extent.end.offset,
            f'{usr(n) or usr(n.referenced)}:{n.kind.name}:{n.extent.start.offset}'),
            usr=usr(n), referenced_usr=usr(n.referenced), kind=n.kind.name)

    def clean(self, i):
        n = self.nodes[i]
        a, b = n.extent.start.offset, n.extent.end.offset
        if not (0 <= a < b <= len(self.data)):
            return False
        if any(a < y and x < b for x, y in self.macros):
            return False
        # Tokens must actually originate at the claimed physical byte location.
        tokens = list(n.get_tokens())
        return bool(tokens) and all(t.location.file and Path(t.location.file.name).resolve() == self.path
            and self.data[t.extent.start.offset:t.extent.end.offset] == t.spelling.encode()
            for t in tokens)

    def operation_parent(self, i):
        p = self.parents[i]
        while p >= 0 and self.nodes[p].kind in (K.UNEXPOSED_EXPR, K.PAREN_EXPR):
            i, p = p, self.parents[p]
        return i, p

    def op(self, i):
        n = self.nodes[i]
        kids = self.children[i]
        if n.kind == K.BINARY_OPERATOR and len(kids) == 2:
            return self.data[self.nodes[kids[0]].extent.end.offset:
                             self.nodes[kids[1]].extent.start.offset].strip().decode()
        if n.kind == K.UNARY_OPERATOR and len(kids) == 1:
            child = self.nodes[kids[0]]
            return (self.data[n.extent.start.offset:child.extent.start.offset] +
                    self.data[child.extent.end.offset:n.extent.end.offset]).strip().decode()
        return None

    def is_read(self, i):
        # A binary expression can itself sit inside sizeof/_Generic/VLA bounds.
        # Restrict the entire ancestor path, not merely the immediate operator.
        cursor = self.parents[i]
        allowed = {'UNEXPOSED_EXPR', 'PAREN_EXPR', 'BINARY_OPERATOR', 'UNARY_OPERATOR',
            'COMPOUND_ASSIGNMENT_OPERATOR', 'CSTYLE_CAST_EXPR', 'CALL_EXPR',
            'CONDITIONAL_OPERATOR', 'RETURN_STMT', 'COMPOUND_STMT', 'IF_STMT',
            'FOR_STMT', 'WHILE_STMT', 'DO_STMT', 'SWITCH_STMT', 'CASE_STMT',
            'DEFAULT_STMT', 'LABEL_STMT'}
        while cursor >= 0 and self.nodes[cursor].kind != K.FUNCTION_DECL:
            if self.nodes[cursor].kind.name not in allowed:
                return False
            cursor = self.parents[cursor]
        if cursor < 0:
            return False
        _, p = self.operation_parent(i)
        if p < 0 or not self.clean(i) or not self.clean(p):
            return False
        node = self.nodes[p]
        return ((node.kind == K.BINARY_OPERATOR and self.op(p) in READ_BINARY)
                or (node.kind == K.UNARY_OPERATOR and self.op(p) in {'+', '-', '~', '!'})
                or node.kind in (K.RETURN_STMT, K.CALL_EXPR))


def plan_enrichment(path, runtime):
    """Return a proof-carrying exact edit; does not create a file or invoke compilers."""
    base = dict(original_path=str(Path(path).resolve()), status='ENRICHMENT_REJECTED',
                preprocessing_type='INT_PARAMETER_MEMBER_ADDRESS_READ', origin='seed_preprocessing',
                is_research_contribution=False, reasons={}, candidate_count=0)
    try:
        ctx = Context(path, runtime)
    except ParseFailure as exc:
        return dict(base, status='PARSE_FAILED', diagnostics=exc.args[0])
    except Exception as exc:
        return dict(base, status='SCAN_FAILED', exception=f'{type(exc).__name__}: {exc}')
    base['original_sha256'] = digest(ctx.data)
    reasons, choices = Counter(), []
    for d, node in enumerate(ctx.nodes):
        if node.kind != K.PARM_DECL or not node.spelling:
            continue
        function = node.semantic_parent
        typ = node.type.get_canonical()
        if not function or function.kind != K.FUNCTION_DECL or not function.is_definition():
            reasons['NOT_DEFINITION_PARAMETER'] += 1; continue
        if typ.kind != T.INT or typ.is_volatile_qualified() or typ.is_const_qualified():
            reasons['NOT_PLAIN_UNQUALIFIED_INT'] += 1; continue
        function_bytes = ctx.data[function.extent.start.offset:function.extent.end.offset]
        if not ctx.clean(d) or any(line.lstrip().startswith(b'#') for line in function_bytes.splitlines()):
            reasons['PARAMETER_OR_FUNCTION_PREPROCESSING_UNPROVEN'] += 1; continue
        uses = ctx.refs[usr(node)]
        if not uses or any(ctx.nodes[i].kind != K.DECL_REF_EXPR or not ctx.is_read(i) for i in uses):
            reasons['PARAMETER_NOT_PROVEN_READ_ONLY_OR_MACRO_USE'] += 1; continue
        for i in uses:
            _, p = ctx.operation_parent(i)
            # Actual arithmetic/comparison value use, not an unevaluated operand,
            # a call designator, an initializer, a label or an assignment lvalue.
            if ctx.nodes[p].kind != K.BINARY_OPERATOR or ctx.op(p) not in READ_BINARY:
                continue
            if ctx.text(i) != node.spelling.encode() or ctx.nodes[i].type.get_canonical().kind != T.INT:
                reasons['REFERENCE_BYTES_OR_TYPE_UNPROVEN'] += 1; continue
            choices.append((d, i, p))
    base.update(reasons=dict(sorted(reasons.items())), candidate_count=len(choices),
                main_file_node_count=sum(n.kind not in (K.TRANSLATION_UNIT, K.MACRO_INSTANTIATION,
                    K.MACRO_DEFINITION, K.INCLUSION_DIRECTIVE) for n in ctx.nodes))
    if not choices:
        return base
    d, i, p = min(choices, key=lambda choice: ctx.nodes[choice[1]].extent.start.offset)
    serial = 0
    while True:
        tag = f'edgm_sp_{base["original_sha256"][:12]}_{serial}'
        member = tag + '_value'
        if tag not in ctx.names and member not in ctx.names:
            break
        serial += 1
    before = ctx.text(i)
    replacement = f'(*(&((struct {tag} {{ int {member}; }}){{ .{member} = ('.encode() + before + f') }}).{member}))'.encode()
    n = ctx.nodes[i]
    edit = ByteEdit(n.extent.start.offset, n.extent.end.offset, before, replacement)
    output = edit.apply(ctx.data)
    checks = dict(original_type='INT', replacement_value_type='INT', original_category='int value read',
        replacement_category='int lvalue followed by original value conversion', value_preserved=True,
        original_expression_evaluations=1, replacement_expression_evaluations=1,
        observable_side_effects_added=False, variable_scope_changed=False,
        declaration_visibility_changed=False, fresh_tag_scope_added=True,
        new_undefined_behavior=False, parameter_definitely_initialized=True,
        parameter_all_references_read_only=True, macro_overlap=False,
        proof_scope='local contextual equivalence on every defined evaluation of the original read; not a whole-program UB proof',
        reasoning=['By-value int parameter is initialized on function entry; all references are proven reads.',
            'C11 6.5.2.5 block-scope compound literal creates a fresh automatic struct object.',
            'C11 6.7.9 initializes its sole int member from the one original int read without conversion loss.',
            'C11 6.5.3.2 & and * cancel on the ordinary non-bit-field member; the original value conversion yields the same int.',
            'No volatile, escaping pointer, double evaluation, allocation call or externally observable store is added.',
            'Existing source bytes and evaluation context outside this read are unchanged; the fresh tag shadows no existing name.'])
    base.update(status='ENRICHMENT_PLANNED', selected_declaration=ctx.anchor(d),
        selected_use=ctx.anchor(i), original_operation=ctx.anchor(p),
        edit=dict(start=edit.start, end=edit.end, before=before.decode(), after=replacement.decode()),
        enhanced_sha256=digest(output), semantic_preservation=checks,
        new_names=dict(tag=tag, member=member),
        dependency=dict(kind='original_parameter_read -> int member initialization -> &member -> dereference -> original_operation',
                        original_declaration_usr=usr(ctx.nodes[d])),
        future_mr=dict(name='MR_MEMBER_BITFIELD_ADDRESSABILITY', status='POTENTIAL_ENTRY_ONLY',
                       complete_mr_applicability='NOT_ASSESSED'))
    return base


def enrich_seed(path, output, runtime):
    """Create a distinct legal P0 candidate with exclusive create; compiler checks external."""
    path, output = Path(path).resolve(), Path(output).resolve()
    if path == output:
        raise ValueError('output must differ from original seed')
    result = plan_enrichment(path, runtime)
    if result['status'] != 'ENRICHMENT_PLANNED':
        return result
    data = path.read_bytes()
    if digest(data) != result['original_sha256']:
        return dict(result, status='SOURCE_CHANGED')
    e = result['edit']
    edit = ByteEdit(e['start'], e['end'], e['before'].encode(), e['after'].encode())
    enhanced = edit.apply(data)
    with output.open('xb') as stream:
        stream.write(enhanced)
    delta = len(edit.after) - len(edit.before)
    result.update(status='ENRICHED_UNVALIDATED', enhanced_path=str(output),
        source_unchanged=path.read_bytes() == data,
        source_map=dict(coordinates='byte offsets, half-open; physical byte line/column',
            original_path=str(path), enhanced_path=str(output),
            segments=[dict(kind='identity', original=[0, edit.start], enhanced=[0, edit.start]),
                      dict(kind='replaced_expression', original=[edit.start, edit.end],
                           enhanced=[edit.start, edit.start+len(edit.after)],
                           nested_original_read=[edit.start+edit.after.index(edit.before, edit.after.index(b'= (')+3),
                               edit.start+edit.after.index(edit.before, edit.after.index(b'= (')+3)+len(edit.before)]),
                      dict(kind='identity', original=[edit.end, len(data)], enhanced=[edit.end+delta, len(enhanced)])]))
    # Post-edit AST is validation of the legal P0 structure, not a P1 Oracle.
    try:
        ctx = Context(output, runtime)
        tag, member = result['new_names'].values()
        fields = [i for i,n in enumerate(ctx.nodes) if n.kind == K.FIELD_DECL and n.spelling == member
                  and n.semantic_parent.spelling == tag]
        if len(fields) != 1:
            raise ValueError('new declaration identity not unique')
        field = fields[0]
        uses = [i for i in ctx.refs[usr(ctx.nodes[field])] if ctx.nodes[i].kind == K.MEMBER_REF_EXPR]
        addresses = [ctx.operation_parent(i)[1] for i in uses
                     if ctx.op(ctx.operation_parent(i)[1]) == '&']
        if len(addresses) != 1:
            raise ValueError('new member address dependency not unique')
        result['new_structure'] = dict(field=ctx.anchor(field), member_uses=[ctx.anchor(i) for i in uses],
            address=ctx.anchor(addresses[0]), field_canonical_type=ctx.nodes[field].type.get_canonical().kind.name,
            address_relation_count=1, ast_status='OK')
        start, end = result['source_map']['segments'][1]['nested_original_read']
        reads = [i for i,n in enumerate(ctx.nodes) if n.kind == K.DECL_REF_EXPR
                 and n.extent.start.offset == start and n.extent.end.offset == end]
        if len(reads) != 1 or ctx.nodes[reads[0]].referenced.kind != K.PARM_DECL:
            raise ValueError('original parameter read provenance not unique after edit')
        original_declaration = ctx.nodes[reads[0]].referenced
        if original_declaration.location.offset != edit.map_offset(result['selected_declaration']['offset']):
            raise ValueError('original declaration identity/source mapping mismatch')
        replacement_nodes = [n for n in ctx.nodes if n.kind.is_expression()
            and n.extent.start.offset == edit.start
            and n.extent.end.offset == edit.start + len(edit.after)]
        if not replacement_nodes or any(n.type.get_canonical().kind != T.INT for n in replacement_nodes):
            raise ValueError('replacement expression type is not proven int')
        result['new_structure']['original_read'] = ctx.anchor(reads[0])
        result['new_structure']['original_declaration'] = ctx.anchor(ctx.index[ctx.nodes[reads[0]].referenced])
        result['new_structure']['mapped_declaration_verified'] = True
        result['new_structure']['replacement_canonical_type'] = 'INT'
    except Exception as exc:
        result.update(status='ENRICHMENT_VALIDATION_FAILED', exception=f'{type(exc).__name__}: {exc}')
    return result
