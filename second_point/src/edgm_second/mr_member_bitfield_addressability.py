"""P0-only, identity-based bit-field MR for two explicitly closed subsets.

No subprocess, P1 parsing or diagnostic input. Target ABI evidence is supplied
beforehand. This is a conditional local proof, not a complete C11 verifier.
"""
from pathlib import Path
from .seed_enrichment import Context, ParseFailure, K, T
from .semantic_profile import usr
from .source_edit import ByteEdit, anchor, digest
from .constraint_witness import BitfieldConstraintWitness
from .native_bitfield_candidates import scan_native

MR_ID = 'MR_MEMBER_BITFIELD_ADDRESSABILITY'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def width_proof(typ, profile):
    require(profile.get('status') == 'VERIFIED', 'TARGET_INTEGER_MODEL_UNVERIFIED')
    facts = profile['facts']
    require(facts['int_width'] == facts['sizeof_int'] * facts['char_bit']
            and facts['int_max'] == (1 << (facts['int_width'] - 1)) - 1,
            'PADDING_OR_RANGE_MODEL_UNSUPPORTED')
    require(typ.kind in (T.INT, T.UINT, T.BOOL) and not typ.is_const_qualified()
            and not typ.is_volatile_qualified(), 'FIELD_BASE_TYPE_UNSUPPORTED')
    require(typ.kind == T.BOOL or typ.get_size() == facts['sizeof_int'], 'LIBCLANG_TARGET_SIZE_MISMATCH')
    base = {T.INT: 'signed int', T.UINT: 'unsigned int', T.BOOL: '_Bool'}[typ.kind]
    width = 1 if typ.kind == T.BOOL else facts['int_width']
    return dict(base_type=base, canonical_kind=typ.kind.name, width=width,
        target_profile=profile, ordinary_bitfield_before=False, bitfield_after=True,
        full_value_range_preserved=True, plain_int_signedness_ambiguity_avoided=True,
        reasoning='Explicit signed/unsigned int uses all sign/value bits of the verified no-padding target int; '
                  '_Bool uses its entire 0/1 value range. No narrowing width is used.',
        standard=['C11:6.7.2.1p4', 'C11:6.7.2.1p5', 'C11:6.7.2.1p10'])


def compound_closure(ctx, f, uses):
    """Recognize structure, not generated names or an enrichment receipt."""
    field = ctx.nodes[f]; record = field.semantic_parent
    r = ctx.index[record]; parent = ctx.parents[r]
    require(parent >= 0 and ctx.nodes[parent].kind == K.COMPOUND_LITERAL_EXPR,
            'NOT_INLINE_COMPOUND_RECORD')
    literal = parent
    require(record.kind == K.STRUCT_DECL and record.is_definition(), 'UNION_OR_INCOMPLETE_RECORD')
    fields = [n for n in record.get_children() if n.kind == K.FIELD_DECL]
    require(len(fields) == 1 and usr(fields[0]) == usr(field), 'COMPOUND_RECORD_NOT_SINGLE_FIELD')
    rt = [t.spelling for t in record.get_tokens()]
    ft = [t.spelling for t in field.get_tokens()]
    require(rt == ['struct', record.spelling, '{'] + ft + [';', '}'], 'RECORD_ATTRIBUTES_OR_COMPLEX_DECLARATOR')
    rid = usr(record)
    require(not any(n.kind == K.TYPE_REF and usr(n.referenced) == rid for n in ctx.nodes),
            'COMPOUND_RECORD_TYPE_ESCAPES')
    require(len({(n.extent.start.offset,n.extent.end.offset) for n in ctx.nodes
                 if n.kind == K.STRUCT_DECL and usr(n) == rid}) == 1, 'RECORD_REDECLARED')
    require(not any(n.kind == K.INCLUSION_DIRECTIVE and n.location.offset > record.extent.start.offset
                    for n in ctx.nodes), 'LATE_HEADER_INCLUDE_UNCLOSED')
    require(len(uses) == 1, 'COMPOUND_FIELD_USES_NOT_UNIQUE')
    member = uses[0]
    base = ctx.children[member]
    require(len(base) == 1, 'MEMBER_BASE_UNPROVEN')
    b = base[0]
    while ctx.nodes[b].kind == K.PAREN_EXPR and len(ctx.children[b]) == 1:
        b = ctx.children[b][0]
    require(b == literal, 'MEMBER_BASE_NOT_OWN_COMPOUND_LITERAL')
    _, address = ctx.operation_parent(member)
    require(address >= 0 and ctx.nodes[address].kind == K.UNARY_OPERATOR and ctx.op(address) == '&',
            'NOT_DIRECT_MEMBER_ADDRESS')
    _, deref = ctx.operation_parent(address)
    require(deref >= 0 and ctx.nodes[deref].kind == K.UNARY_OPERATOR and ctx.op(deref) == '*',
            'ADDRESS_ESCAPES_IMMEDIATE_DEREFERENCE')
    require(ctx.is_read(deref), 'REPLACEMENT_VALUE_CONTEXT_UNSUPPORTED')
    _, operation = ctx.operation_parent(deref)
    require(ctx.nodes[operation].kind == K.BINARY_OPERATOR, 'NOT_ORIGINAL_BINARY_VALUE_PATH')
    initializers = [j for j in ctx.children[literal] if ctx.nodes[j].kind == K.INIT_LIST_EXPR]
    require(len(initializers) == 1, 'INITIALIZER_UNPROVEN')
    init = initializers[0]
    reads = [i for i,n in enumerate(ctx.nodes) if n.kind == K.DECL_REF_EXPR
             and ctx.nodes[init].extent.start.offset <= n.extent.start.offset < ctx.nodes[init].extent.end.offset]
    require(len(reads) == 1, 'INITIALIZER_NOT_SINGLE_PARAMETER_READ')
    read = reads[0]; parameter = ctx.nodes[read].referenced
    typ = parameter.type.get_canonical()
    require(parameter.kind == K.PARM_DECL and typ.kind == T.INT and not typ.is_volatile_qualified()
            and not typ.is_const_qualified() and field.type.get_canonical().kind == T.INT,
            'INITIALIZER_NOT_PLAIN_INT_PARAMETER')
    # An exact token grammar rejects casts, side effects, hidden nested records,
    # extra designators, macros and conversions. Identifier spellings come from AST.
    require([t.spelling for t in ctx.nodes[init].get_tokens()] ==
            ['{', '.', field.spelling, '=', '(', parameter.spelling, ')', '}'],
            'INITIALIZER_GRAMMAR_UNSUPPORTED')
    require(all(ctx.clean(i) for i in (r,literal,member,address,deref,init,read,operation)),
            'MACRO_OR_LOCATION_UNPROVEN')
    # All record-valued expressions stay within the literal/member operand.
    allowed = {'COMPOUND_LITERAL_EXPR','PAREN_EXPR','INIT_LIST_EXPR','STRUCT_DECL'}
    for n in ctx.nodes:
        t = n.type.get_canonical()
        if t.kind == T.RECORD and usr(t.get_declaration()) == rid:
            require(n.kind.name in allowed and
                    ctx.nodes[member].extent.start.offset <= n.extent.start.offset and
                    n.extent.end.offset <= ctx.nodes[member].extent.end.offset,
                    'RECORD_VALUE_OR_LAYOUT_ESCAPE')
    return dict(subset='inline_single_int_member_compound_literal',
        initializer=ctx.anchor(init), original_read=ctx.anchor(read),
        parameter_declaration=ctx.anchor(ctx.index[parameter]), original_operation=ctx.anchor(operation),
        dereference=ctx.anchor(deref), record_uses_closed=True,
        no_layout_observation=True, no_address_escape=True,
        complexity='simple_local_enhancement', cross_statement=False, cross_scope=False, cross_function=False)


def scan_candidates(path, runtime, profile, native_scanner=None):
    result = dict(status='SCAN_FAILED', candidate_count=None, candidates=[], rejected_fields=[])
    try:
        ctx = Context(path, runtime)
        result['p0_sha256'] = digest(ctx.data)
        native = None
        for f, field in enumerate(ctx.nodes):
            if field.kind != K.FIELD_DECL:
                continue
            identity = usr(field)
            uses = [i for i in ctx.refs[identity] if ctx.nodes[i].kind == K.MEMBER_REF_EXPR]
            addresses = [ctx.operation_parent(i)[1] for i in uses
                         if ctx.operation_parent(i)[1] >= 0 and
                         ctx.nodes[ctx.operation_parent(i)[1]].kind == K.UNARY_OPERATOR and
                         ctx.op(ctx.operation_parent(i)[1]) == '&']
            if not addresses:
                continue
            entry = dict(field=ctx.anchor(f), record_usr=usr(field.semantic_parent),
                         address_anchors=[ctx.anchor(i) for i in addresses])
            try:
                require(not field.is_bitfield(), 'ALREADY_BITFIELD')
                proof = width_proof(field.type.get_canonical(), profile)
                require(not any(n.kind == K.MACRO_DEFINITION and n.spelling in proof['base_type'].split()
                                for n in ctx.tu.cursor.get_children()), 'REPLACEMENT_TYPE_KEYWORD_IS_MACRO')
                ft = [t.spelling for t in field.get_tokens()]
                require(ft in [['int',field.spelling],['signed','int',field.spelling],
                    ['unsigned','int',field.spelling],['unsigned',field.spelling],['_Bool',field.spelling]],
                    'SINGLE_BASIC_DECLARATOR_REQUIRED')
                require(ctx.data[field.extent.end.offset:].lstrip().startswith(b';') and ctx.clean(f),
                        'DECLARATOR_OR_MACRO_UNPROVEN')
                r = ctx.index[field.semantic_parent]
                if ctx.parents[r] >= 0 and ctx.nodes[ctx.parents[r]].kind == K.COMPOUND_LITERAL_EXPR:
                    isolation = compound_closure(ctx, f, uses)
                else:
                    if native is None:
                        native = (native_scanner or scan_native)(path, runtime)
                    accepted = next((e for e in native['candidates'] if e['field']['usr'] == identity), None)
                    rejected = next((e for e in native['rejected_fields'] if e['field']['usr'] == identity), None)
                    require(accepted is not None, 'NATIVE_CLOSURE_REJECTED:' + ','.join(
                        rejected['reasons'] if rejected else [native['status']]))
                    require(len(addresses) == len(uses), 'NATIVE_NON_ADDRESS_MEMBER_USE_OUTSIDE_2E_SUBSET')
                    isolation = dict(subset='stage2d_closed_automatic_objects_address_only',
                        native_proof=accepted, record_uses_closed=True, no_layout_observation=True,
                        no_address_escape=False, complexity='native_declaration_use_dependency')
                # Designator MEMBER_REF is a use too; never silently discard it.
                related = [i for i,n in enumerate(ctx.nodes) if n.referenced and usr(n.referenced)==identity]
                require(all(ctx.nodes[i].kind in (K.FIELD_DECL,K.MEMBER_REF_EXPR,K.MEMBER_REF) for i in related),
                        'UNCLASSIFIED_FIELD_REFERENCE')
                entry.update(width_proof=proof, isolation=isolation,
                    all_related_uses=[ctx.anchor(i) for i in related],
                    member_anchors=[ctx.anchor(i) for i in uses], record=ctx.anchor(r),
                    edit=dict(start=field.extent.start.offset,end=field.extent.end.offset,
                        before=ctx.text(f).decode(),after=f'{proof["base_type"]} {field.spelling} : {proof["width"]}'))
                result['candidates'].append(entry)
            except ValueError as exc:
                entry['reason'] = str(exc); result['rejected_fields'].append(entry)
        result.update(status='APPLICABLE' if result['candidates'] else 'NOT_APPLICABLE',
            candidate_count=len(result['candidates']), source_unchanged=ctx.path.read_bytes()==ctx.data)
    except ParseFailure as exc:
        result.update(status='PARSE_FAILED', diagnostics=exc.args[0])
    except Exception as exc:
        result.update(status='SCAN_FAILED', exception=f'{type(exc).__name__}: {exc}')
    return result


def mapped_anchor(a, data, output, edit):
    def transport(x):
        return edit.map_offset(x)
    # Field-name location lies inside the replaced declaration; derive it from
    # the known replacement, rather than inventing an AST identity in illegal P1.
    offset = (edit.start + len(edit.after.rsplit(b' : ',1)[0]) - len(edit.before.split()[-1])
              if edit.start < a['offset'] < edit.end else transport(a['offset']))
    return dict(anchor(output,data,offset,transport(a['start_offset']),transport(a['end_offset']),
                       a['node_identity']), p1_ast_observed=False, referenced_usr=a.get('referenced_usr'),
                usr=a.get('usr'), kind=a.get('kind'))


def build_mutation(path, output, runtime, profile, admission, provenance, candidate_index=0, native_scanner=None):
    path, output = Path(path).resolve(), Path(output).resolve()
    p0 = path.read_bytes()
    require(admission.get('status')=='valid' and admission.get('sha256')==digest(p0)
            and admission.get('source_unchanged') is True and admission.get('source')==str(path)
            and set(admission.get('checks',{}))=={'gcc','clang'}
            and all(v.get('returncode')==0 and v.get('status')=='ok' for v in admission['checks'].values()),
            'P0_ADMISSION_MISSING_OR_STALE')
    require(output != path, 'OUTPUT_EQUALS_INPUT')
    from .environment import PROJECT
    require(output.is_relative_to(PROJECT) and not output.exists(), 'UNSAFE_OR_EXISTING_OUTPUT')
    scan = scan_candidates(path,runtime,profile,native_scanner=native_scanner)
    if scan['status'] != 'APPLICABLE':
        return scan, None, None
    candidate = scan['candidates'][candidate_index]
    e = candidate['edit']; edit = ByteEdit(e['start'],e['end'],e['before'].encode(),e['after'].encode())
    p1 = edit.apply(p0)
    pairs = []
    members = candidate['member_anchors']
    for a in candidate['address_anchors']:
        member = next(m for m in members if a['start_offset'] <= m['start_offset'] and m['end_offset'] <= a['end_offset'])
        pairs.append(dict(address=dict(p0=a,p1=mapped_anchor(a,p1,output,edit)),
            member=dict(p0=member,p1=mapped_anchor(member,p1,output,edit)),
            field_usr=candidate['field']['usr'], constraint_id='C11:6.5.3.2p1'))
    declaration = dict(field_usr=candidate['field']['usr'],record_usr=candidate['record_usr'],
        p0=candidate['field'],p1=mapped_anchor(candidate['field'],p1,output,edit),
        record_p0=candidate['record'], record_p1=mapped_anchor(candidate['record'],p1,output,edit),
        before_bitfield=False,after_bitfield=True,edit=e)
    delta = len(edit.after)-len(edit.before)
    mapping = dict(p0_file=str(path),p1_file=str(output),p0_sha256=digest(p0),p1_sha256=digest(p1),edit=e,
        segments=[dict(kind='identity',p0=[0,edit.start],p1=[0,edit.start]),
                  dict(kind='field_declaration_replacement',p0=[edit.start,edit.end],p1=[edit.start,edit.start+len(edit.after)]),
                  dict(kind='identity',p0=[edit.end,len(p0)],p1=[edit.end+delta,len(p1)])])
    require(provenance.get('source_kind') in ('Native','Enriched'), 'SOURCE_KIND_REQUIRED')
    s = Path(provenance['original_path']).read_bytes()
    require(digest(s)==provenance['original_sha256'], 'ORIGINAL_SEED_HASH_MISMATCH')
    if provenance['source_kind']=='Native':
        require(s==p0, 'NATIVE_SOURCE_NOT_IDENTICAL')
        provenance=dict(provenance,original_to_p0=dict(kind='identity',original=[0,len(s)],p0=[0,len(p0)],
            original_sha256=digest(s),p0_sha256=digest(p0)))
    else:
        verify_enrichment_provenance(s,p0,provenance['enrichment'])
        receipt = provenance['enrichment']
        isolation = candidate['isolation']
        require(isolation['subset']=='inline_single_int_member_compound_literal', 'ENRICHED_CANDIDATE_NOT_ON_ORIGINAL_PATH')
        a,b=receipt['source_map']['segments'][1]['nested_original_read']
        require([isolation['original_read']['start_offset'],isolation['original_read']['end_offset']]==[a,b],
                'ENRICHED_READ_NOT_MAPPED_ORIGINAL')
        se=receipt['edit']; delta_s=len(se['after'].encode())-len(se['before'].encode())
        oldop=receipt['original_operation']; newop=isolation['original_operation']
        require(s[oldop['start_offset']:oldop['end_offset']].decode()==oldop['source_text'] and
                newop['start_offset']==oldop['start_offset'] and newop['end_offset']==oldop['end_offset']+delta_s,
                'ORIGINAL_OPERATION_NOT_PRESERVED')
        declaration_offset=receipt['selected_declaration']['offset']
        require(isolation['parameter_declaration']['offset']==declaration_offset,
                'PARAMETER_DECLARATION_PROVENANCE_MISMATCH')
    witness = BitfieldConstraintWitness(MR_ID,digest(s),'C11:6.5.3.2p1',provenance,declaration,
        candidate['width_proof'],pairs,mapping,
        dict(candidate['isolation'],all_related_uses=candidate['all_related_uses'],
             proof_boundary='P0 AST facts plus verified target ABI; no general C11 or whole-program UB proof'),
        dict(category='address_of_bitfield',required_severities=['error','fatal'],warning_satisfies=False,
             exact_caret_mandated_by_standard=False,expected_violation_count=len(pairs)))
    verify_bitfield_witness(witness,p0,p1,s)
    return scan,p1,witness


def verify_enrichment_provenance(original, p0, record):
    """Replay all three source segments, including the nested original read."""
    require(digest(original)==record['original_sha256'] and digest(p0)==record['enhanced_sha256'],
            'ENRICHMENT_HASH_MISMATCH')
    e=record['edit']; edit=ByteEdit(e['start'],e['end'],e['before'].encode(),e['after'].encode())
    require(edit.apply(original)==p0,'ENRICHMENT_EDIT_MISMATCH')
    segments=record['source_map']['segments']; delta=len(edit.after)-len(edit.before)
    expected=[([0,edit.start],[0,edit.start]),([edit.start,edit.end],[edit.start,edit.start+len(edit.after)]),
              ([edit.end,len(original)],[edit.end+delta,len(p0)])]
    require(len(segments)==3 and [(x['original'],x['enhanced']) for x in segments]==expected,
            'ENRICHMENT_SEGMENT_MISMATCH')
    a,b=segments[1]['nested_original_read']
    require(edit.start<=a<b<=edit.start+len(edit.after) and p0[a:b]==edit.before,
            'ENRICHMENT_NESTED_READ_MISMATCH')
    return True


def verify_bitfield_witness(witness,p0,p1,original):
    """Check serialized local proof consistency; does not re-establish AST facts."""
    w=witness.to_dict() if isinstance(witness,BitfieldConstraintWitness) else witness
    require(w['mr_id']==MR_ID and w['constraint_id']=='C11:6.5.3.2p1','WRONG_CONSTRAINT')
    m=w['source_map']; e=m['edit']; edit=ByteEdit(e['start'],e['end'],e['before'].encode(),e['after'].encode())
    require(edit.apply(p0)==p1 and p0!=p1 and digest(p0)==m['p0_sha256'] and digest(p1)==m['p1_sha256'],
            'SOURCE_MAP_MISMATCH')
    d=w['changed_declaration']; proof=w['width_proof']; policy=w['expected_error']
    require(d['edit']==e and d['before_bitfield'] is False and d['after_bitfield'] is True,'FIELD_FACT_MISMATCH')
    require(d['p0']['usr']==d['field_usr'] and d['record_p0']['usr']==d['record_usr'] and
            d['p0']['source_text']==e['before'] and d['p1']['source_text']==e['after'] and
            [d['p0']['start_offset'],d['p0']['end_offset']]==[edit.start,edit.end], 'DECLARATION_IDENTITY_MISMATCH')
    facts=proof['target_profile']['facts']
    require(proof['base_type'] in ('signed int','unsigned int','_Bool') and
        proof['width']==(1 if proof['base_type']=='_Bool' else facts['int_width']) and
        facts['int_width']==facts['sizeof_int']*facts['char_bit'] and
        facts['int_max']==(1<<(facts['int_width']-1))-1 and proof['target_profile']['status']=='VERIFIED' and
        proof['full_value_range_preserved'] is True, 'WIDTH_PROOF_MISMATCH')
    name=e['before'].split()[-1]
    require(e['after']==f'{proof["base_type"]} {name} : {proof["width"]}', 'WIDTH_EDIT_MISMATCH')
    require(policy==dict(category='address_of_bitfield',required_severities=['error','fatal'],warning_satisfies=False,
        exact_caret_mandated_by_standard=False,expected_violation_count=len(w['affected_uses'])) and
        len(w['affected_uses'])>0,'INVALID_ERROR_POLICY')
    require(w['isolation']['record_uses_closed'] is True and w['isolation']['no_layout_observation'] is True,
            'IMPACT_NOT_CLOSED')
    native = w['isolation'].get('native_proof', {})
    if 'layout_independence' in native:
        from .scoped_layout import verify_layout_proof
        require(native['field']['usr'] == d['field_usr'] and native['record_usr'] == d['record_usr'],
                'NATIVE_LAYOUT_IDENTITY_MISMATCH')
        require(native['impact_closed'] is True and not native['reasons'],'NATIVE_LAYOUT_IMPACT_NOT_CLOSED')
        verify_layout_proof(native['layout_independence'],p0,d['field_usr'],d['record_usr'],m['p0_file'])
    delta=len(edit.after)-len(edit.before)
    require(m['segments']==[dict(kind='identity',p0=[0,edit.start],p1=[0,edit.start]),
        dict(kind='field_declaration_replacement',p0=[edit.start,edit.end],p1=[edit.start,edit.start+len(edit.after)]),
        dict(kind='identity',p0=[edit.end,len(p0)],p1=[edit.end+delta,len(p1)])], 'MAP_SEGMENTS_MISMATCH')
    def check_pair(a,b):
        require(b==mapped_anchor(a,p1,m['p1_file'],edit),'ANCHOR_TRANSPORT_MISMATCH')
        for x,data,file in [(a,p0,m['p0_file']),(b,p1,m['p1_file'])]:
            require(x['file']==file and anchor(file,data,x['offset'],x['start_offset'],x['end_offset'],x['node_identity'])==
                {k:x[k] for k in anchor(file,data,x['offset'],x['start_offset'],x['end_offset'],x['node_identity'])},
                'ANCHOR_BYTES_OR_COORDINATES_MISMATCH')
    check_pair(d['p0'],d['p1']);check_pair(d['record_p0'],d['record_p1'])
    seen=set()
    for use in w['affected_uses']:
        require(use['field_usr']==d['field_usr'] and use['constraint_id']==w['constraint_id'], 'FIELD_EDGE_MISMATCH')
        require(use['member']['p0']['referenced_usr']==d['field_usr'], 'MEMBER_IDENTITY_MISMATCH')
        a=use['address']['p0']; member=use['member']['p0']
        require(p0[a['start_offset']:a['start_offset']+1]==b'&' and a['start_offset']<=member['start_offset']
                and member['end_offset']<=a['end_offset'], 'ADDRESS_EDGE_MISMATCH')
        require(a['start_offset'] not in seen,'DUPLICATE_TARGET');seen.add(a['start_offset'])
        check_pair(use['address']['p0'],use['address']['p1']);check_pair(member,use['member']['p1'])
    require({(u['member']['p0']['start_offset'],u['member']['p0']['end_offset']) for u in w['affected_uses']}==
        {(a['start_offset'],a['end_offset']) for a in w['isolation']['all_related_uses'] if a['kind']=='MEMBER_REF_EXPR'},
        'AFFECTED_MEMBER_COVERAGE_MISMATCH')
    provenance=w['provenance']
    require(digest(original)==w['seed_id']==provenance['original_sha256'],'SEED_HASH_MISMATCH')
    if provenance['source_kind']=='Enriched':verify_enrichment_provenance(original,p0,provenance['enrichment'])
    else:
        require(provenance['source_kind']=='Native' and original==p0,'NATIVE_PROVENANCE_MISMATCH')
        require(provenance['original_to_p0']==dict(kind='identity',original=[0,len(original)],p0=[0,len(p0)],
            original_sha256=digest(original),p0_sha256=digest(p0)), 'NATIVE_MAP_MISMATCH')
    return True
