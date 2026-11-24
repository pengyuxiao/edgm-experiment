"""One opt-in extension: discharge only unrelated sizeof/_Alignof vetoes.

Every other stage 2D/2E restriction remains. AST facts are supplied by P0;
replay checks those serialized facts, not arbitrary C semantics from scratch.
"""
from copy import deepcopy
from collections import Counter
import json
from .seed_enrichment import Context, K, T, ParseFailure
from .semantic_profile import usr, INTS, ARRAYS
from .source_edit import digest, anchor
from .native_bitfield_candidates import scan_native

LAYOUT = {'sizeof','_Alignof','_Alignas','__attribute__','__attribute',
          '__builtin_offsetof','offsetof','_Static_assert','pragma','asm','__asm__'}
SUPPORTED = {'sizeof','_Alignof'}
BUILTIN_WORDS = {'char','short','int','long','signed','unsigned','float','double','_Bool','const','volatile'}
SCALARS = INTS | {T.FLOAT,T.DOUBLE,T.LONGDOUBLE,T.VOID}


def type_graph(type_, target_record):
    """Follow canonical pointer/array/record fields; cycles are explicit edges.

    Pointer dependencies are deliberately retained even where pointer size could
    be proven invariant. Unknown/incomplete records and VLAs fail closed.
    """
    nodes=[]; cache={}
    def visit(typ, depth=0):
        typ=typ.get_canonical(); decl=typ.get_declaration()
        rid=usr(decl) if typ.kind==T.RECORD else None
        key=(typ.kind.name,rid,typ.spelling)
        if key in cache:return cache[key]
        index=len(nodes);cache[key]=index
        node=dict(kind=typ.kind.name,canonical_spelling=typ.spelling,record_usr=rid,
                  edges=[],unknown=False);nodes.append(node)
        if depth>32:node['unknown']=True
        elif typ.kind==T.POINTER:node['edges']=[visit(typ.get_pointee(),depth+1)]
        elif typ.kind in ARRAYS:
            node['array_size']=typ.get_array_size()
            node['unknown']=typ.kind!=T.CONSTANTARRAY
            node['edges']=[visit(typ.get_array_element_type(),depth+1)]
        elif typ.kind==T.FUNCTIONPROTO:
            node['edges']=[visit(typ.get_result(),depth+1)]+[visit(t,depth+1) for t in typ.argument_types()]
        elif typ.kind==T.RECORD:
            definition=decl.get_definition()
            if not definition or not definition.is_definition():node['unknown']=True
            else:
                fields=[f for f in definition.get_children() if f.kind==K.FIELD_DECL]
                node['field_usrs']=[usr(f) for f in fields]
                node['edges']=[visit(f.type,depth+1) for f in fields]
                node['definition_file']=str(definition.location.file) if definition.location.file else None
        elif typ.kind not in SCALARS:node['unknown']=True
        return index
    root=visit(type_)
    return dict(root=root,nodes=nodes,target_record_usr=target_record,
        related=any(n['record_usr']==target_record for n in nodes),unknown=any(n['unknown'] for n in nodes))


def descendants(ctx,index):
    stack=list(ctx.children[index])
    while stack:
        i=stack.pop();yield i;stack.extend(ctx.children[i])


def seal(proof):
    return digest(json.dumps({k:v for k,v in proof.items() if k!='seal'},sort_keys=True,ensure_ascii=False).encode())


class LayoutAnalysis:
    def __init__(self, ctx):
        self.ctx=ctx
        self.tokens=[t for t in ctx.tu.get_tokens(extent=ctx.tu.cursor.extent) if t.spelling in LAYOUT]
        self.operations=[]
        for i,n in enumerate(ctx.nodes):
            if n.kind==K.CXX_UNARY_EXPR:
                tokens=list(n.get_tokens())
                if tokens and tokens[0].spelling in SUPPORTED:self.operations.append((i,tokens))

    def prove(self, field):
        ctx=self.ctx; f=ctx.nodes[field]; rid=usr(f.semantic_parent)
        proof=dict(schema='stage2f-layout-v1',p0_file=str(ctx.path),p0_sha256=digest(ctx.data),field_usr=usr(f),record_usr=rid,
            inventory=[dict(offset=t.extent.start.offset,keyword=t.spelling) for t in self.tokens],
            operations=[],reasons=[],status='UNPROVEN',
            rule='all physical layout tokens mapped; every sizeof/_Alignof operand and referenced declaration type is independent; all other layout markers rejected')
        starts=set()
        for i,tokens in self.operations:
            op=dict(anchor=ctx.anchor(i),keyword=tokens[0].spelling,graphs=[],references=[],reasons=[])
            starts.add(tokens[0].extent.start.offset)
            if not ctx.clean(i):op['reasons'].append('LAYOUT_MACRO_OR_LOCATION_UNPROVEN')
            kids=ctx.children[i]
            if not kids:
                # Exact builtin type-name grammar; pointer/array/typeof type
                # declarators are not inferred from an absent operand cursor.
                ts=[t.spelling for t in tokens]
                if not (len(ts)>=4 and ts[1]=='(' and ts[-1]==')' and set(ts[2:-1])<=BUILTIN_WORDS):
                    op['reasons'].append('LAYOUT_OPERAND_TYPE_UNAVAILABLE')
                op['builtin_type_tokens']=ts[2:-1]
            elif len(kids)==1 and ctx.nodes[kids[0]].kind.is_expression():
                op['operand_type']=ctx.nodes[kids[0]].type.get_canonical().spelling
                prefix=[t.spelling for t in tokens[1:] if t.extent.start.offset < ctx.nodes[kids[0]].extent.start.offset]
                if any(t!='(' for t in prefix):
                    op['reasons'].append('LAYOUT_OPERAND_TYPE_UNAVAILABLE')
            elif len(kids)==1 and ctx.nodes[kids[0]].kind==K.TYPE_REF:
                ts=[t.spelling for t in tokens]
                # Only a simple type name: complex type operands have no full
                # type cursor in libclang's public API and remain unproven.
                if any(t in ts for t in ('*','[',']','__typeof__','typeof')):
                    op['reasons'].append('COMPLEX_LAYOUT_TYPE_OPERAND_UNSUPPORTED')
                op['operand_type']=ctx.nodes[kids[0]].type.get_canonical().spelling
            else:op['reasons'].append('LAYOUT_OPERAND_TYPE_UNAVAILABLE')
            for j in descendants(ctx,i):
                n=ctx.nodes[j]
                if n.type.kind!=T.INVALID:
                    graph=type_graph(n.type,rid);op['graphs'].append(graph)
                    if graph['related']:op['reasons'].append('LAYOUT_REACHES_TARGET_RECORD')
                    if graph['unknown']:op['reasons'].append('LAYOUT_TYPE_GRAPH_UNCLOSED')
                ref=n.referenced
                if ref and n.kind in (K.DECL_REF_EXPR,K.MEMBER_REF_EXPR,K.TYPE_REF):
                    ref_record=usr(ref.semantic_parent) if ref.kind==K.FIELD_DECL else None
                    op['references'].append(dict(anchor=ctx.anchor(j),referenced_usr=usr(ref),
                        referenced_field_record_usr=ref_record))
                    if usr(ref)==usr(f) or ref_record==rid:
                        op['reasons'].append('LAYOUT_REFERENCES_TARGET_RECORD_MEMBER')
                    if ref.type.kind!=T.INVALID:
                        graph=type_graph(ref.type,rid);op['graphs'].append(graph)
                        if graph['related']:op['reasons'].append('LAYOUT_REACHES_TARGET_RECORD')
                        if graph['unknown']:op['reasons'].append('LAYOUT_TYPE_GRAPH_UNCLOSED')
            op['reasons']=sorted(set(op['reasons']));proof['operations'].append(op)
        for token in self.tokens:
            if token.spelling not in SUPPORTED:proof['reasons'].append('OTHER_LAYOUT_MARKER_UNSUPPORTED:'+token.spelling)
            elif token.extent.start.offset not in starts:proof['reasons'].append('LAYOUT_KEYWORD_WITHOUT_PHYSICAL_AST')
        proof['reasons']=sorted(set(proof['reasons']+[r for op in proof['operations'] for r in op['reasons']]))
        proof['status']='INDEPENDENT' if not proof['reasons'] else 'UNPROVEN'
        proof['seal']=seal(proof)
        return proof


def verify_layout_proof(proof,p0,field_usr,record_usr,p0_file=None):
    """Pure replay of conditional AST/type evidence, not a C parser."""
    def need(condition):
        if not condition:raise ValueError('LAYOUT_PROOF_INVALID')
    need(proof['schema']=='stage2f-layout-v1' and proof['status']=='INDEPENDENT' and not proof['reasons'])
    need(proof['p0_sha256']==digest(p0) and proof['field_usr']==field_usr and proof['record_usr']==record_usr)
    if p0_file is not None:need(proof['p0_file']==p0_file)
    need(proof['seal']==seal(proof))
    inventory={(a['offset'],a['keyword']) for a in proof['inventory']}
    operations={(o['anchor']['start_offset'],o['keyword']) for o in proof['operations']}
    need(inventory==operations and all(k in SUPPORTED for _,k in inventory))
    for op in proof['operations']:
        need(not op['reasons'])
        a=op['anchor'];need(a['file']==proof['p0_file'])
        expected=anchor(a['file'],p0,a['offset'],a['start_offset'],a['end_offset'],a['node_identity'])
        need(all(a[k]==v for k,v in expected.items()))
        need(p0[a['start_offset']:a['start_offset']+len(op['keyword'])]==op['keyword'].encode())
        if 'builtin_type_tokens' in op:
            words=op['builtin_type_tokens']
            need(bool(words) and set(words)<=BUILTIN_WORDS)
            need(''.join(a['source_text'].split())==op['keyword']+'('+''.join(words)+')')
        else:need(bool(op['graphs']))
        for ref in op['references']:
            need(ref['referenced_usr']!=field_usr and ref['referenced_field_record_usr']!=record_usr)
            ra=ref['anchor'];need(ra['file']==proof['p0_file'] and a['start_offset']<=ra['start_offset']<=ra['end_offset']<=a['end_offset'])
            expected_ref=anchor(ra['file'],p0,ra['offset'],ra['start_offset'],ra['end_offset'],ra['node_identity'])
            need(all(ra[k]==v for k,v in expected_ref.items()))
        for graph in op['graphs']:
            need(graph['target_record_usr']==record_usr and not graph['related'] and not graph['unknown'])
            nodes=graph['nodes'];need(0<=graph['root']<len(nodes))
            for n in nodes:
                need(not n['unknown'] and n['record_usr']!=record_usr and all(0<=e<len(nodes) for e in n['edges']))
                need(n['kind'] in {t.name for t in SCALARS}|{'POINTER','CONSTANTARRAY','RECORD','FUNCTIONPROTO'})
                if n['kind'] in ('POINTER','CONSTANTARRAY'):need(len(n['edges'])==1)
                if n['kind']=='RECORD':need(len(n['edges'])==len(n['field_usrs']))
    return True


def scan_native_scoped_layout(path,runtime, *, context=None, legacy=None):
    """Remove exactly one legacy reason, and only with an independent proof."""
    result=deepcopy(legacy if legacy is not None else scan_native(path,runtime))
    if result['status'] not in ('APPLICABLE','NOT_APPLICABLE'):return result
    ctx=context or Context(path,runtime)
    if result.get('sha256') != digest(ctx.data):raise ValueError('STALE_NATIVE_SCAN')
    analysis=LayoutAnalysis(ctx)
    fields={usr(n):i for i,n in enumerate(ctx.nodes) if n.kind==K.FIELD_DECL}
    kept=[];audits=[]
    for entry in result['rejected_fields']:
        if 'LAYOUT_SENSITIVE_CONSTRUCT_IN_FILE' not in entry['reasons']:
            kept.append(entry);continue
        proof=analysis.prove(fields[entry['field']['usr']]);entry['layout_independence']=proof
        audits.append(dict(field_usr=entry['field']['usr'],status=proof['status'],reasons=proof['reasons']))
        if proof['status']=='INDEPENDENT':
            verify_layout_proof(proof,ctx.data,entry['field']['usr'],entry['record_usr'])
            entry['reasons'].remove('LAYOUT_SENSITIVE_CONSTRUCT_IN_FILE')
        if entry['reasons']:kept.append(entry)
        else:
            entry['impact_closed']=True
            entry['proof']=['unchanged stage2d closed-object rules','stage2f unrelated layout operand/type graphs']
            result['candidates'].append(entry)
    result.update(rejected_fields=kept,candidate_count=len(result['candidates']),layout_audits=audits,
        rejection_counts=dict(Counter(r for e in kept for r in e['reasons'])),
        status='APPLICABLE' if result['candidates'] else 'NOT_APPLICABLE',extension='unrelated_layout_only')
    return result
