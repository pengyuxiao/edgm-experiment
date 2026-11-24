"""Conservative, AST-driven rewrite of one argument-side typedef dependency.

No compiler invocation and no P1 parsing occur here. All type predictions are
computed from P0 declaration identities and a single symbolic edge override.
"""
from pathlib import Path
import time
from clang import cindex as c
from .ast_extract import parse_translation_unit, main_file_nodes
from .constraint_witness import ConstraintWitness, MR_ID, STANDARD
from .source_edit import ByteEdit, anchor, digest
from .environment import PROJECT

K = c.CursorKind
T = c.TypeKind


class ASTParseError(RuntimeError):
    def __init__(self, diagnostics):
        super().__init__('P0_AST_ERRORS')
        self.diagnostics = diagnostics


class NotApplicable(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise NotApplicable(reason)


def uid(node):
    return node.canonical.get_usr()


def tokens(node):
    return [t for t in node.get_tokens() if t.kind != c.TokenKind.COMMENT]


def spelling(node):
    return [t.spelling for t in tokens(node)]


def unwrap(node):
    while node.kind in (K.UNEXPOSED_EXPR, K.PAREN_EXPR):
        children = list(node.get_children())
        if len(children) != 1:
            break
        node = children[0]
    return node


def record_id(type_):
    canonical = type_.get_canonical()
    return uid(canonical.get_declaration()) if canonical.kind == T.RECORD else None


def simple_record(node):
    fields = list(node.get_children())
    return (node.kind == K.STRUCT_DECL and node.is_definition() and bool(node.spelling)
            and node.semantic_parent.kind == K.TRANSLATION_UNIT and bool(fields)
            and all(f.kind == K.FIELD_DECL and f.type.kind == T.INT
                    and not f.is_bitfield() and spelling(f) == ['int', f.spelling] for f in fields))


class Analysis:
    def __init__(self, path, runtime, controlled=False):
        self.controlled = controlled
        self.path = Path(path).resolve()
        self.data = self.path.read_bytes()
        started = time.perf_counter()
        self.tu = (parse_translation_unit(self.path, runtime, detailed_processing=True) if controlled
                   else parse_translation_unit(self.path, runtime))
        self.timings = dict(parse_seconds=time.perf_counter() - started)
        self.diagnostics = [dict(severity=d.severity, text=str(d)) for d in self.tu.diagnostics]
        if any(d['severity'] >= c.Diagnostic.Error for d in self.diagnostics):
            raise ASTParseError(self.diagnostics)
        started = time.perf_counter()
        self.pairs = list(main_file_nodes(self.tu, self.path))
        self.timings['traversal_seconds'] = time.perf_counter() - started
        started = time.perf_counter()
        self.nodes = [n for n, _ in self.pairs]
        self.parents = {n.hash: parent for n, parent in self.pairs}
        self.typedefs = {uid(n): n for n in self.nodes if n.kind == K.TYPEDEF_DECL}
        self.records = {uid(n): n for n in self.nodes if simple_record(n)}
        self.edges = {}
        for identity, node in self.typedefs.items():
            refs = [x for x in node.get_children() if x.kind == K.TYPE_REF]
            ts = spelling(node)
            if node.semantic_parent.kind != K.TRANSLATION_UNIT or len(refs) != 1:
                continue
            target = refs[0].referenced
            if target.kind == K.TYPEDEF_DECL and ts == ['typedef', target.spelling, node.spelling]:
                self.edges[identity] = uid(target)
            elif target.kind == K.STRUCT_DECL and ts == ['typedef', 'struct', target.spelling, node.spelling]:
                self.edges[identity] = uid(target)

        self.timings['index_seconds'] = time.perf_counter() - started

    def resolve(self, identity, override=None):
        chain = []
        while identity in self.edges:
            require(identity not in chain, 'TYPEDEF_CYCLE')
            chain.append(identity)
            identity = override[1] if override and identity == override[0] else self.edges[identity]
        require(identity in self.records, 'UNSUPPORTED_RECORD_OR_TYPEDEF')
        return identity, chain

    def node_anchor(self, node):
        return anchor(self.path, self.data, node.location.offset,
                      node.extent.start.offset, node.extent.end.offset,
                      dict(kind=node.kind.name, usr=uid(node),
                           referenced_usr=uid(node.referenced) if node.referenced else None, p0_hash=digest(self.data),
                           origin_key=f'{node.kind.name}:{node.extent.start.offset}:{node.extent.end.offset}'))

    def type_info(self, identity):
        node = self.records[identity]
        return dict(kind='struct', declaration_usr=identity, tag=node.spelling,
                    scope='translation_unit', complete=True,
                    fields=[dict(name=f.spelling, kind='int') for f in node.get_children()],
                    declaration_anchor=self.node_anchor(node))

    def candidate(self, call):
        children = list(call.get_children())
        args = list(call.get_arguments())
        require(children and len(args) == 1, 'REQUIRES_ONE_ARGUMENT')
        member = unwrap(children[0])
        require(member.kind == K.MEMBER_REF_EXPR and member.referenced is not None
                and member.referenced.kind == K.FIELD_DECL, 'NOT_MEMBER_INDIRECT_CALL')
        field = member.referenced
        pointer = field.type.get_canonical()
        require(pointer.kind == T.POINTER, 'MEMBER_NOT_FUNCTION_POINTER')
        function = pointer.get_pointee()
        require(function.kind == T.FUNCTIONPROTO and not function.is_function_variadic()
                and len(list(function.argument_types())) == 1 and function.get_result().kind == T.INT,
                'UNSUPPORTED_FUNCTION_PROTOTYPE')
        parameter = list(function.argument_types())[0]
        expected_id = record_id(parameter)
        arg = unwrap(args[0])
        require(arg.kind == K.DECL_REF_EXPR and arg.referenced.kind == K.VAR_DECL,
                'ARGUMENT_NOT_PLAIN_VARIABLE')
        variable = arg.referenced
        refs = [x for x in variable.get_children() if x.kind == K.TYPE_REF]
        require(len(refs) == 1 and refs[0].referenced.kind == K.TYPEDEF_DECL, 'ARGUMENT_HAS_NO_TYPEDEF_CHAIN')
        actual_id, chain = self.resolve(uid(refs[0].referenced))
        require(len(chain) >= 2, 'TYPEDEF_CHAIN_TOO_SHORT')
        require(expected_id == actual_id and record_id(arg.type) == actual_id
                and parameter.get_canonical() == arg.type.get_canonical(), 'P0_TYPES_NOT_IDENTICAL')
        # A single automatic, unqualified scalar-record object with universal zero initialization.
        require(variable.semantic_parent.kind == K.FUNCTION_DECL and variable.type.get_canonical().kind == T.RECORD,
                'ARGUMENT_NOT_AUTOMATIC_LOCAL')
        initializer_proof = None
        if spelling(variable) != [refs[0].referenced.spelling, variable.spelling, '=', '{', '0', '}']:
            require(self.controlled, 'ARGUMENT_INITIALIZER_OR_DECLARATOR_UNSUPPORTED')
            reads=[n for n in self.nodes if n.kind==K.DECL_REF_EXPR and variable.extent.start.offset<=n.extent.start.offset<variable.extent.end.offset]
            require(len(reads)==1 and reads[0].referenced.kind==K.PARM_DECL,'INITIALIZER_NOT_SINGLE_PARAMETER')
            read=reads[0];parameter=read.referenced;typ=parameter.type.get_canonical()
            require(typ.kind==T.INT and not typ.is_const_qualified() and not typ.is_volatile_qualified()
                    and parameter.semantic_parent==variable.semantic_parent,'INITIALIZER_PARAMETER_TYPE_OR_SCOPE')
            require(spelling(variable)==[refs[0].referenced.spelling,variable.spelling,'=','{',parameter.spelling,'}'],
                    'INITIALIZER_GRAMMAR_UNSUPPORTED')
            initializer_proof=dict(parameter=self.node_anchor(parameter),read=self.node_anchor(read),
                canonical_kind='INT',qualifiers=False,initializer_text='{ '+parameter.spelling+' }')
        uses = [n for n in self.nodes if n.kind == K.DECL_REF_EXPR and n.referenced
                and uid(n.referenced) == uid(variable)]
        require(len(uses) == 1 and uses[0].location.offset == arg.location.offset, 'ARGUMENT_HAS_OTHER_USES')
        require(len(spelling(call)) == 6 and spelling(call) ==
                [spelling(member)[0], '.', field.spelling, '(', variable.spelling, ')'],
                'REQUIRES_PLAIN_DOT_MEMBER_CALL')
        # Both payload records and the rewritten typedef must be file-scope and macro-free.
        if not self.controlled:
            require(not any(t.spelling in ('#', '##', '__attribute__', '__declspec', '_Pragma')
                            for t in self.tu.get_tokens(extent=self.tu.cursor.extent)), 'PREPROCESSING_OR_ATTRIBUTES_UNSUPPORTED')
        errors = []
        for changed in chain[1:]:  # Leave the variable's immediate alias, change an actual dependency edge.
            node = self.typedefs[changed]
            ts = tokens(node)
            if len(ts) != 3 or self.edges.get(changed) not in self.typedefs:
                continue
            affected = {i for i in self.edges if changed in self.resolve_if_supported(i)}
            try:
                for identity in affected:
                    declarations = {(n.extent.start.offset, n.extent.end.offset) for n in self.nodes
                                    if n.kind == K.TYPEDEF_DECL and uid(n) == identity}
                    require(len(declarations) == 1, 'AFFECTED_TYPEDEF_REDECLARED')
                # Every use of any changed alias must be another simple alias or this one variable.
                for ref, parent in self.pairs:
                    if ref.kind != K.TYPE_REF or not ref.referenced or uid(ref.referenced) not in affected:
                        continue
                    require(parent is not None and ((parent.kind == K.TYPEDEF_DECL and uid(parent) in affected
                            and uid(parent) in self.edges) or parent == variable), 'AFFECTED_ALIAS_HAS_OTHER_USES')
                # Catch redeclarations/implicit type uses not represented by an allowed TYPE_REF.
                for n in self.nodes:
                    if n.kind == K.TYPEDEF_DECL and uid(n) in affected:
                        continue
                    if n.kind in (K.VAR_DECL, K.PARM_DECL, K.FIELD_DECL, K.FUNCTION_DECL):
                        if n.type.get_declaration().kind == K.TYPEDEF_DECL and uid(n.type.get_declaration()) in affected:
                            require(n == variable, 'AFFECTED_ALIAS_ESCAPES')
                alternatives = sorted((r for identity, r in self.records.items() if identity != actual_id
                    and r.extent.end.offset < node.extent.start.offset), key=lambda r: r.location.offset)
                require(alternatives, 'NO_DISTINCT_COMPLETE_VISIBLE_STRUCT')
                other = alternatives[0]
                # Globally unique tag declaration identity: no tag capture at the rewritten site.
                require(len({uid(n) for n in self.nodes if n.kind in (K.STRUCT_DECL, K.UNION_DECL, K.ENUM_DECL)
                             and n.spelling == other.spelling}) == 1, 'REPLACEMENT_TAG_SHADOWED')
                replacement = ('struct ' + other.spelling).encode('utf-8')
                edit = ByteEdit(ts[1].extent.start.offset, ts[1].extent.end.offset,
                                ts[1].spelling.encode('utf-8'), replacement)
                require(self.resolve(uid(refs[0].referenced), (changed, uid(other)))[0] == uid(other),
                        'SYMBOLIC_OVERRIDE_FAILED')
                if self.controlled:
                    from .controlled_typedef import check_regions
                    check_regions(self,[call,variable,field,self.records[actual_id],other,*[self.typedefs[x] for x in affected]])
                return dict(call=call, member=member, field=field, argument=arg, variable=variable,
                            expected_id=expected_id, actual_id=actual_id, changed=changed,
                            replacement_id=uid(other), affected=sorted(affected), chain=chain, edit=edit,initializer_proof=initializer_proof)
            except NotApplicable as exc:
                errors.append(str(exc))
        raise NotApplicable(';'.join(sorted(set(errors))) or 'NO_ISOLATED_TYPEDEF_EDGE')

    def resolve_if_supported(self, identity):
        try:
            return self.resolve(identity)[1]
        except NotApplicable:
            return []

    def scan(self):
        reasons = []
        calls = [n for n in self.nodes if n.kind == K.CALL_EXPR]
        for call in calls:
            try:
                return self.candidate(call), reasons
            except NotApplicable as exc:
                reasons.append(str(exc))
        return None, sorted(set(reasons)) or ['NO_CALL_EXPRESSION']


def scan_candidates(path, runtime):
    """Enumerate every eligible call site, with AST reasons and no mutations.

    One deterministic admissible typedef edge/replacement is retained per call
    site; candidate_count is a count of call positions, not all edit combinations.
    """
    from .applicability import analyze_candidates
    return analyze_candidates(path, runtime)


def build_mutation(path, output_path, runtime, p0_validation, controlled=False):
    """Return P1 bytes + a witness; no files are written and no P1 is parsed.

    P0 validation is the stage-1 receipt; it is admission evidence only, never
    an input to expected type/category inference.
    """
    path, output_path = Path(path).resolve(), Path(output_path).resolve()
    data = path.read_bytes()
    require(p0_validation.get('status') == 'valid' and p0_validation.get('sha256') == digest(data)
            and p0_validation.get('source') == str(path)
            and p0_validation.get('source_unchanged') is True
            and all(p0_validation.get('checks', {}).get(name, {}).get('returncode') == 0
                    and p0_validation['checks'][name]['command'][1:4] == ['-std=c11', '-fsyntax-only', '-w']
                    for name in ('gcc', 'clang')), 'MISSING_OR_STALE_P0_VALIDATION')
    analysis = Analysis(path, runtime, controlled=True) if controlled else Analysis(path, runtime)
    require(analysis.data == data, 'SOURCE_CHANGED_DURING_ANALYSIS')
    item, reasons = analysis.scan()
    if item is None:
        return dict(status='NOT_APPLICABLE', reasons=reasons, witness=None, p1=None)
    edit = item['edit']
    p1 = edit.apply(data)
    changed = analysis.typedefs[item['changed']]
    relevant = dict(changed_declaration=changed, expected_struct=analysis.records[item['expected_id']],
                    replacement_struct=analysis.records[item['replacement_id']], call=item['call'],
                    argument=item['argument'], argument_declaration=item['variable'],
                    member=item['member'], member_declaration=item['field'])
    callback_declaration = item['field'].type.get_declaration()
    if callback_declaration.kind == K.TYPEDEF_DECL:
        relevant['callback_typedef'] = callback_declaration
    for index, identity in enumerate(item['chain']):
        relevant[f'argument_alias_{index}'] = analysis.typedefs[identity]
    anchors = {}
    for label, node in relevant.items():
        p0_anchor = analysis.node_anchor(node)
        p1_anchor = anchor(output_path, p1, edit.map_offset(node.location.offset),
            edit.map_offset(node.extent.start.offset), edit.map_offset(node.extent.end.offset),
            {**p0_anchor['node_identity'], 'identity_basis': 'P0 AST identity transported through exact byte edit',
             'p1_hash': digest(p1), 'p1_ast_observed': False})
        anchors[label] = dict(p0=p0_anchor, p1=p1_anchor)
    before_root, before_chain = analysis.resolve(item['chain'][0])
    after_root, after_chain = analysis.resolve(item['chain'][0], (item['changed'], item['replacement_id']))
    preconditions = [dict(id=name, satisfied=True) for name in (
        'P0_DUAL_COMPILER_ACCEPTED', 'P0_AST_NO_ERRORS', 'TWO_COMPLETE_DISTINCT_FILE_SCOPE_STRUCTS',
        'TYPEDEF_CHAIN_IDENTITY_RESOLVED', 'PROTOTYPED_MEMBER_FUNCTION_POINTER_CALL',
        'P0_PARAMETER_ARGUMENT_SAME_DECLARATION_IDENTITY', 'ONE_ISOLATED_ARGUMENT_VARIABLE',
        'ZERO_INITIALIZER_VALID_FOR_BOTH_RECORDS', 'AFFECTED_TYPE_REFERENCES_CLOSED',
        'NO_MACROS_ATTRIBUTES_OR_TAG_CAPTURE')]
    if item.get('initializer_proof'):
        for condition in preconditions:
            if condition['id']=='ZERO_INITIALIZER_VALID_FOR_BOTH_RECORDS':condition['id']='INT_PARAMETER_INITIALIZER_VALID_FOR_BOTH_RECORDS'
    witness = ConstraintWitness(
        mr_id=MR_ID, seed_id=digest(data), constraint_id='C11:6.5.2.2p2', preconditions=preconditions,
        changed_declaration=dict(usr=item['changed'], spelling=changed.spelling,
            original_dependency_usr=analysis.edges[item['changed']], new_dependency_usr=item['replacement_id'],
            before=anchors['changed_declaration']['p0']['source_text'],
            after=anchors['changed_declaration']['p1']['source_text']),
        expected_parameter_type=analysis.type_info(item['expected_id']),
        actual_argument_type=dict(p0=analysis.type_info(before_root), p1=analysis.type_info(after_root)),
        call_expression=dict(argument_index=0, parameter_count=1, variadic=False,
            callee_type=dict(member_surface_type=item['field'].type.spelling,
                canonical_function_type=item['field'].type.get_canonical().get_pointee().spelling,
                parameter_record_usr=item['expected_id']),
            member_usr=uid(item['field']), argument_variable_usr=uid(item['variable']),
            p0_compatible=True, p1_compatible=False, p0_argument_chain=before_chain, p1_argument_chain=after_chain),
        source_anchors=anchors,
        expected_error=dict(category='incompatible_struct_argument', required_severities=['error', 'fatal'],
            warning_satisfies=False, standard_obligation='diagnostic required; C11 does not prescribe severity or exact caret',
            severity_basis='research contract for the configured GCC/Clang; not an ISO severity mandate',
            target_argument_anchor='source_anchors.argument.p1', exact_caret_mandated_by_standard=False,
            confirmed_compiler_bug=False),
        source_map=dict(p0_file=str(path), p1_file=str(output_path), p0_sha256=digest(data), p1_sha256=digest(p1),
            edit=dict(start=edit.start, end=edit.end, before=edit.before.decode(), after=edit.after.decode()),
            unchanged_segments=[dict(p0=[0, edit.start], p1=[0, edit.start]),
                dict(p0=[edit.end, len(data)], p1=[edit.start + len(edit.after), len(p1)])],
            byte_delta=len(edit.after) - len(edit.before)),
        dependency_graph=dict(edges_p0=analysis.edges, changed_edge=dict(source=item['changed'],
            old_target=analysis.edges[item['changed']], new_target=item['replacement_id']),
            identity_rule='same-TU file-scope struct declaration USR + definition anchor, not type-name inequality',
            constraints=['6.7.2.3p4-p5', '6.2.7p1', '6.7.8p3', '6.5.2.2p2', '6.5.16.1p1']),
        isolation=dict(affected_aliases=item['affected'], allowed_terminal_variable=uid(item['variable']),
            variable_reference_count=1, initializer='{ 0 }', unchanged_parameter_record=item['expected_id'],
            unchanged_callback_field=uid(item['field']),
            scope='complete direct int-field records; file-scope simple aliases; one local plain argument'),
        provenance=dict(standard_reference=STANDARD, standard_edition='public C11 committee draft N1570',
            inference='P0 AST graph with symbolic edge substitution; no P1 parse, compiler or diagnostics',
            p0_admission_sha256=p0_validation['sha256']))
    if item.get('initializer_proof'):
        witness.isolation.update(initializer_proof=item['initializer_proof'],initializer=item['initializer_proof']['initializer_text'],
            controlled_region_policy='physical tokens, no macro overlap, no post-record includes')
    return dict(status='APPLIED', reasons=[], witness=witness, p1=p1)


def write_mutation(path, output_path, witness_path, runtime, p0_validation):
    """Write new artifacts only under second_point; refuse existing paths."""
    output_path, witness_path = Path(output_path).resolve(), Path(witness_path).resolve()
    for target in (output_path, witness_path):
        require(PROJECT.resolve() in target.parents, 'OUTPUT_OUTSIDE_SECOND_POINT')
        require(not target.exists(), 'OUTPUT_EXISTS')
    require(output_path != witness_path and output_path != Path(path).resolve(), 'OUTPUT_COLLISION')
    result = build_mutation(path, output_path, runtime, p0_validation)
    if result['status'] != 'APPLIED':
        return result
    require(digest(Path(path).read_bytes()) == result['witness'].seed_id, 'SOURCE_CHANGED_BEFORE_WRITE')
    for target in (output_path, witness_path):
        target.parent.mkdir(parents=True, exist_ok=True)
    with witness_path.open('x', encoding='utf-8') as stream:
        stream.write(result['witness'].to_json())
    with output_path.open('xb') as stream:
        stream.write(result['p1'])
    return result

