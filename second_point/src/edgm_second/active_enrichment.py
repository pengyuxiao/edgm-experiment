"""Stage 2G: bounded active structure preparation; not an MR or learner.

Only immutable by-value int/unsigned-int parameter reads are moved to a private
entry object. Reading such an initialized nonvolatile value early has no
observable effects. Existing operations retain their original evaluation paths.
"""
from collections import Counter
import json
from pathlib import Path
from .seed_enrichment import Context, K, T, READ_BINARY
from .semantic_profile import usr
from .source_edit import digest

FAMILIES=('split_pointer','nested_two_addresses','typedef_callback')


def apply_edits(source,edits):
    ordered=sorted(edits,key=lambda e:(e['start'],e['end']))
    cursor=0;chunks=[];segments=[];position=0
    for e in ordered:
        a,b=e['start'],e['end'];before=e['before'].encode();after=e['after'].encode()
        if not cursor<=a<=b<=len(source) or source[a:b]!=before:raise ValueError('EDIT_OR_OVERLAP_MISMATCH')
        text=source[cursor:a];chunks.append(text)
        segments.append(dict(kind='identity',s=[cursor,a],p0=[position,position+len(text)]));position+=len(text)
        chunks.append(after);segments.append(dict(kind='generated' if a==b else 'replacement',s=[a,b],p0=[position,position+len(after)]));position+=len(after);cursor=b
    chunks.append(source[cursor:]);segments.append(dict(kind='identity',s=[cursor,len(source)],p0=[position,position+len(source)-cursor]))
    return b''.join(chunks),segments


def enumerate_positions(path,runtime):
    ctx=Context(path,runtime);choices=[];reasons=Counter()
    for d,n in enumerate(ctx.nodes):
        if n.kind!=K.PARM_DECL or not n.spelling:continue
        typ=n.type.get_canonical();func=n.semantic_parent
        if not func or func.kind!=K.FUNCTION_DECL or not func.is_definition():continue
        if typ.kind not in (T.INT,T.UINT) or typ.is_const_qualified() or typ.is_volatile_qualified():
            reasons['NOT_PLAIN_INT_OR_UINT']+=1;continue
        fi=ctx.index.get(func);bodies=[i for i in ctx.children[fi] if ctx.nodes[i].kind==K.COMPOUND_STMT]
        if len(bodies)!=1:continue
        body=bodies[0]
        if not ctx.clean(d) or any(line.lstrip().startswith(b'#') for line in ctx.text(fi).splitlines()) or any(t.spelling in ('asm','__asm__','__asm') for t in func.get_tokens()):
            reasons['FUNCTION_PREPROCESSING_UNPROVEN']+=1;continue
        uses=ctx.refs[usr(n)]
        if not uses or any(ctx.nodes[i].kind!=K.DECL_REF_EXPR or not ctx.is_read(i) for i in uses):
            reasons['PARAMETER_NOT_READ_ONLY']+=1;continue
        for i in uses:
            _,op=ctx.operation_parent(i)
            if op<0:continue
            if ctx.nodes[op].kind==K.BINARY_OPERATOR:
                if ctx.op(op) not in READ_BINARY- {','}:continue
                operation_kind=ctx.op(op)
            elif ctx.nodes[op].kind==K.RETURN_STMT:operation_kind='return_value'
            elif ctx.nodes[op].kind==K.CALL_EXPR:
                called=ctx.nodes[op].referenced
                if not called or called.kind!=K.FUNCTION_DECL or called.spelling.startswith('__builtin') or not ctx.clean(op):continue
                operation_kind='function_argument'
            else:continue
            if ctx.text(i)!=n.spelling.encode() or ctx.nodes[i].type.get_canonical().kind!=typ.kind:continue
            # Find a declaration-free, label-free statement unit which can gain
            # a fresh block without moving existing declarations into a scope.
            unit=i
            while ctx.parents[unit]>=0 and ctx.parents[unit]!=body and ctx.nodes[ctx.parents[unit]].kind!=K.COMPOUND_STMT:
                unit=ctx.parents[unit]
            parent=ctx.parents[unit];nested_ok=False;end=ctx.nodes[unit].extent.end.offset
            if parent>=0 and ctx.nodes[parent].kind==K.COMPOUND_STMT and ctx.nodes[unit].kind in (K.IF_STMT,K.RETURN_STMT,K.BINARY_OPERATOR,K.CALL_EXPR,K.UNARY_OPERATOR,K.COMPOUND_ASSIGNMENT_OPERATOR):
                subtree=[];stack=[unit]
                while stack:
                    j=stack.pop();subtree.append(j);stack.extend(ctx.children[j])
                forbidden={K.LABEL_STMT,K.GOTO_STMT,K.CASE_STMT,K.DEFAULT_STMT}
                nested_ok=not any(ctx.nodes[j].kind in forbidden or ctx.nodes[j].kind.is_declaration() for j in subtree) and ctx.clean(unit)
                if nested_ok and not ctx.text(unit).rstrip().endswith((b'}',b';')):
                    rest=ctx.data[end:];spaces=len(rest)-len(rest.lstrip())
                    if rest[spaces:spaces+1]==b';':end+=spaces+1
                    else:nested_ok=False
            choices.append(dict(position_id=f'{digest(ctx.data)}:{usr(n)}:{ctx.nodes[i].extent.start.offset}',
                read=ctx.anchor(i),declaration=ctx.anchor(d),function_offset=func.extent.start.offset,
                body_start=ctx.nodes[body].extent.start.offset,kind=typ.kind.name,parameter=n.spelling,
                function_name=func.spelling,function_usr=usr(func),operator=operation_kind,operation=ctx.anchor(op),
                unit=[ctx.nodes[unit].extent.start.offset,end],nested_supported=nested_ok,
                ancestor_context=[ctx.nodes[j].kind.name for j in (op,unit,parent) if j>=0]))
    choices.sort(key=lambda c:(c['function_name']=='main',c['read']['start_offset']))
    return ctx,choices,dict(reasons)


def plan_enrichment(ctx,position,family):
    if family not in FAMILIES:raise ValueError('UNKNOWN_FAMILY')
    if family=='typedef_callback' and position['kind']!='INT':raise ValueError('CALLBACK_REQUIRES_INT')
    if family=='nested_two_addresses' and not position['nested_supported']:raise ValueError('NESTED_STATEMENT_UNPROVEN')
    source=ctx.data;p=position;serial=0
    roles=['record','other','value','object','left','right','root','middle','argument','callback','dispatch_type','dispatch','extract','input','invoke']
    while True:
        prefix=f'ag_{digest(source)[:10]}_{p["read"]["start_offset"]}_{FAMILIES.index(family)}_{serial}'
        names={r:prefix+'_'+r for r in roles}
        if not any(n in ctx.names for n in names.values()):break
        serial+=1
    n=names;typ='int' if p['kind']=='INT' else 'unsigned int';param=p['parameter']
    declaration=f'\nstruct {n["record"]} {{ {typ} {n["value"]}; }};\nstruct {n["record"]} {n["object"]} = {{ {param} }};\n'
    helper='';replacement='';edits=[]
    if family=='split_pointer':
        declaration+=f'{typ} *{n["left"]} = &{n["object"]}.{n["value"]};\n'
        replacement=f'(*{n["left"]})'
    elif family=='nested_two_addresses':
        replacement=f'(*({n["left"]} == {n["right"]} ? {n["left"]} : {n["right"]}))'
    else:
        helper=f'''struct {n['record']} {{ int {n['value']}; }};
struct {n['other']} {{ int {n['value']}; }};
typedef struct {n['record']} {n['root']};
typedef {n['root']} {n['middle']};
typedef {n['middle']} {n['argument']};
typedef int (*{n['callback']})({n['root']});
struct {n['dispatch_type']} {{ {n['callback']} {n['invoke']}; }};
static int {n['extract']}({n['root']} {n['input']}) {{ return {n['input']}.{n['value']}; }}
'''
        member=n['invoke']
        declaration=f'\n{n["argument"]} {n["object"]} = {{ {param} }};\nstruct {n["dispatch_type"]} {n["dispatch"]} = {{ {n["extract"]} }};\n'
        replacement=f'{n["dispatch"]}.{member}({n["object"]})'
        edits.append(dict(start=p['function_offset'],end=p['function_offset'],before='',after=helper))
    entry=p['body_start']+1
    edits.append(dict(start=entry,end=entry,before='',after=declaration))
    a,b=p['read']['start_offset'],p['read']['end_offset']
    if family=='nested_two_addresses':
        u,v=p['unit'];old=source[u:v]
        changed=old[:a-u]+replacement.encode()+old[b-u:]
        wrapper=f'{{\n{typ} *{n["left"]} = &{n["object"]}.{n["value"]};\n{typ} *{n["right"]} = &{n["object"]}.{n["value"]};\n'.encode()+changed+b'\n}'
        edits.append(dict(start=u,end=v,before=old.decode(),after=wrapper.decode()))
    else:edits.append(dict(start=a,end=b,before=source[a:b].decode(),after=replacement))
    output,segments=apply_edits(source,edits)
    descriptor=dict(family=family,mr_id='MR_TYPEDEF_INDIRECT_STRUCT_ARG' if family=='typedef_callback' else 'MR_MEMBER_BITFIELD_ADDRESSABILITY',
        input_type=p['kind'],input_role='immutable_by_value_parameter',function_role='entry_control' if p['function_name']=='main' else 'ordinary_function',
        original_operator=p['operator'],original_context=p['ancestor_context'],address_points=0 if family=='typedef_callback' else 2 if family=='nested_two_addresses' else 1,
        cross_statement=True,cross_scope=family=='nested_two_addresses',cross_function=family=='typedef_callback',
        target_use_points=2 if family=='nested_two_addresses' else 1,record_shape='single_integer_member',
        access_form='dot_member_function_pointer_call' if family=='typedef_callback' else 'private_pointer_dereference',
        value_route='record_argument -> member callback -> identity return' if family=='typedef_callback' else 'private member -> address -> pointer selection -> dereference',
        typedef_depth=3 if family=='typedef_callback' else 0)
    signature=digest(json.dumps(descriptor,sort_keys=True).encode())
    return dict(schema='stage2g-enrichment-v1',source_kind='Enriched',s_file=str(ctx.path),s_sha256=digest(source),p0_sha256=digest(output),
        position=p,enrichment_family=family,mr_id=descriptor['mr_id'],semantic_context=descriptor,semantic_context_signature=signature,
        names=n,replacement=replacement,edits=edits,source_map=segments,local_equivalence=dict(parameter_initialized=True,all_parameter_references_read_only=True,
            early_nonvolatile_read_has_no_observable_effect=True,value_type_preserved=True,new_objects_private=True,new_calls_pure_identity=family=='typedef_callback',
            original_expression_evaluation_path_preserved=True,global_ub_proof=False)),output


def verify_enrichment(record,s,p0):
    if digest(s)!=record['s_sha256'] or digest(p0)!=record['p0_sha256']:raise ValueError('LINEAGE_HASH_MISMATCH')
    rebuilt,segments=apply_edits(s,record['edits'])
    if rebuilt!=p0 or segments!=record['source_map']:raise ValueError('LINEAGE_EDIT_OR_MAP_MISMATCH')
    if digest(json.dumps(record['semantic_context'],sort_keys=True).encode())!=record['semantic_context_signature']:raise ValueError('CONTEXT_SIGNATURE_MISMATCH')
    p=record['position'];a,b=p['read']['start_offset'],p['read']['end_offset']
    if s[a:b].decode()!=p['parameter']:raise ValueError('ORIGINAL_READ_MISMATCH')
    if not all(record['local_equivalence'][k] for k in ('parameter_initialized','all_parameter_references_read_only','early_nonvolatile_read_has_no_observable_effect','value_type_preserved','new_objects_private','original_expression_evaluation_path_preserved')):raise ValueError('LOCAL_PROOF_INCOMPLETE')
    return True


def mapped_offset(record,offset):
    for segment in record['source_map']:
        a,b=segment['s']
        if segment['kind']=='identity' and a<=offset<b:return segment['p0'][0]+offset-a
    raise ValueError('SOURCE_POINT_NOT_UNCHANGED')


def validate_route(record,s,path,runtime):
    """Observe the prepared P0, never P1; bind its value route to the S read."""
    data=Path(path).read_bytes();verify_enrichment(record,s,data);ctx=Context(path,runtime)
    p=record['position'];n=record['names']
    parameters=[i for i,x in enumerate(ctx.nodes) if x.kind==K.PARM_DECL and x.location.offset==mapped_offset(record,p['declaration']['offset'])]
    if len(parameters)!=1:raise ValueError('MAPPED_PARAMETER_NOT_UNIQUE')
    parameter=ctx.nodes[parameters[0]]
    objects=[i for i,x in enumerate(ctx.nodes) if x.kind==K.VAR_DECL and x.spelling==n['object']]
    if len(objects)!=1:raise ValueError('PREPARED_OBJECT_NOT_UNIQUE')
    obj=objects[0];x=ctx.nodes[obj]
    reads=[i for i in ctx.refs[usr(parameter)] if x.extent.start.offset<=ctx.nodes[i].extent.start.offset<x.extent.end.offset]
    if len(reads)!=1:raise ValueError('ORIGINAL_VALUE_NOT_ROUTED_INTO_OBJECT')
    oa,ob=p['operation']['start_offset'],p['operation']['end_offset'];a,b=p['read']['start_offset'],p['read']['end_offset']
    expected=s[oa:a]+record['replacement'].encode()+s[b:ob]
    operations=[i for i,x in enumerate(ctx.nodes) if x.kind.name==p['operation']['kind'] and ctx.text(i)==expected]
    if len(operations)!=1:raise ValueError('ORIGINAL_OPERATION_NOT_CONNECTED')
    replacements=[i for i,x in enumerate(ctx.nodes) if x.kind==(K.CALL_EXPR if record['enrichment_family']=='typedef_callback' else K.PAREN_EXPR) and ctx.text(i)==record['replacement'].encode()]
    if not replacements or any(ctx.nodes[i].type.get_canonical().kind.name!=p['kind'] for i in replacements):raise ValueError('VALUE_ROUTE_TYPE_CHANGED')
    helper=None
    if record['enrichment_family']=='typedef_callback':
        funcs=[i for i,x in enumerate(ctx.nodes) if x.kind==K.FUNCTION_DECL and x.spelling==n['extract'] and x.is_definition()]
        if len(funcs)!=1:raise ValueError('IDENTITY_HELPER_NOT_UNIQUE')
        helper=ctx.nodes[funcs[0]]
        ts=[t.spelling for t in helper.get_tokens()]
        if ts!=['static','int',n['extract'],'(',n['root'],n['input'],')','{','return',n['input'],'.',n['value'],';','}'] or not ctx.clean(funcs[0]):raise ValueError('HELPER_NOT_PURE_IDENTITY')
        dispatches=[i for i,x in enumerate(ctx.nodes) if x.kind==K.VAR_DECL and x.spelling==n['dispatch']]
        if len(dispatches)!=1:raise ValueError('DISPATCH_NOT_UNIQUE')
        dispatch=ctx.nodes[dispatches[0]]
        refs=[i for i,x in enumerate(ctx.nodes) if x.kind==K.DECL_REF_EXPR and x.referenced and usr(x.referenced)==usr(helper)
              and dispatch.extent.start.offset<=x.extent.start.offset<dispatch.extent.end.offset]
        if len(refs)!=1 or len(ctx.refs[usr(dispatch)])!=1:raise ValueError('CALLBACK_TARGET_OR_DISPATCH_ESCAPE')
    return dict(parameter=ctx.anchor(parameters[0]),initializer_read=ctx.anchor(reads[0]),object=ctx.anchor(obj),
        original_operation=ctx.anchor(operations[0]),route_expression=ctx.anchor(replacements[0]),
        record_usr=usr(x.type.get_canonical().get_declaration()),helper_usr=usr(helper) if helper else None,
        helper_anchor=ctx.anchor(funcs[0]) if helper else None,cross_function_value_route=bool(helper),
        proof='immutable initialized parameter -> private member initialized once -> known pointer/identity-callback -> same original binary operation')
