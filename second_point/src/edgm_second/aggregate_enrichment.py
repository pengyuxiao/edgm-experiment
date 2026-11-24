"""Two private aggregate-copy routes, reusing the stage 2G parameter proof."""
import json
from .active_enrichment import plan_enrichment as base_plan,apply_edits,verify_enrichment,mapped_offset
from .seed_enrichment import Context,K
from .source_edit import digest

MR_ID='MR_MEMBER_CONST_AGGREGATE_WRITE'
FAMILIES=('aggregate_direct','aggregate_nested')


def plan(ctx,p,family,assignments=1):
    if family not in FAMILIES or assignments not in (1,2):raise ValueError('UNSUPPORTED_AGGREGATE_ROUTE')
    base,_=base_plan(ctx,p,'split_pointer');n=base['names'];typ='int' if p['kind']=='INT' else 'unsigned int'
    # Names come from the same collision-checked role set, with distinct roles.
    inner,outer,value,src,dst,part,tag=[n[k] for k in ('record','other','value','left','object','right','input')]
    decl=f'\nstruct {inner} {{ {typ} {value}; }};\n'
    if family==FAMILIES[1]:
        decl+=f'struct {outer} {{ struct {inner} {part}; int {tag}; }};\n'
        record=outer;si='{{ '+p['parameter']+' }, 0}';di='{{0}, 0}';member=f'{part}.{value}'
    else:record=inner;si='{ '+p['parameter']+' }';di='{0}';member=value
    decl+=f'struct {record} {src} = {si};\nstruct {record} {dst} = {di};\n'
    decl+=(f'{dst} = {src};\n')*assignments
    replacement=f'({dst}.{member})';a,b=p['read']['start_offset'],p['read']['end_offset']
    edits=[dict(start=p['body_start']+1,end=p['body_start']+1,before='',after=decl),
           dict(start=a,end=b,before=ctx.data[a:b].decode(),after=replacement)]
    data,segments=apply_edits(ctx.data,edits)
    descriptor=dict(family=family,mr_id=MR_ID,input_type=p['kind'],input_role='immutable_by_value_parameter',
        function_role='entry_control' if p['function_name']=='main' else 'ordinary_function',
        original_operator=p['operator'],original_context=p['ancestor_context'],cross_statement=True,
        cross_scope=False,cross_function=False,record_depth=2 if family==FAMILIES[1] else 1,
        target_use_points=assignments,value_route='parameter -> initialized source -> whole-record copy -> destination member -> original operation')
    result=dict(base,schema='stage2h-enrichment-v1',enrichment_family=family,mr_id=MR_ID,p0_sha256=digest(data),
        semantic_context=descriptor,semantic_context_signature=digest(json.dumps(descriptor,sort_keys=True).encode()),
        edits=edits,source_map=segments,replacement=replacement,assignment_count=assignments)
    return result,data


def route(record,path,runtime,candidate):
    """Bind the structurally recognized copy route to the unchanged S declaration."""
    from pathlib import Path
    s=Path(record['s_file']).read_bytes();p0=Path(path).read_bytes();verify_enrichment(record,s,p0)
    ctx=Context(path,runtime);p=record['position'];proof=candidate['proof']
    if proof['parameter']['offset']!=mapped_offset(record,p['declaration']['offset']):raise ValueError('PARAMETER_LINEAGE_MISMATCH')
    if proof['parameter']['usr']!=proof['parameter_read']['referenced_usr']:raise ValueError('PARAMETER_REFERENCE_MISMATCH')
    a,b=p['read']['start_offset'],p['read']['end_offset'];oa,ob=p['operation']['start_offset'],p['operation']['end_offset']
    expected=s[oa:a]+record['replacement'].encode()+s[b:ob]
    operations=[i for i,x in enumerate(ctx.nodes) if x.kind.name==p['operation']['kind'] and ctx.text(i)==expected]
    leaves=proof['leaf_reads']
    if len(operations)!=1 or len(leaves)!=1 or record['replacement']!='('+leaves[0]['source_text']+')':raise ValueError('ORIGINAL_OPERATION_NOT_CONNECTED')
    if proof['canonical_kind']!=p['kind']:raise ValueError('LOCAL_VALUE_TYPE_CHANGED')
    return dict(original_operation=ctx.anchor(operations[0]),parameter=proof['parameter'],
        initializer_read=proof['parameter_read'],leaf_read=leaves[0],destination=proof['destination'],
        proof='initialized immutable parameter, same-type member initialization, private complete-record copies, same member value in original operation',
        boundary='local value equivalence on defined original evaluations; early pure capture; not global C11/UB verification')
