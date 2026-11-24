"""Lineage envelope around the two existing formal MR witnesses. No subprocess."""
from pathlib import Path
from .active_enrichment import verify_enrichment,validate_route,mapped_offset
from .controlled_bitfield import scan_controlled_bitfields
from .mr_member_bitfield_addressability import build_mutation as bitfield,verify_bitfield_witness,scan_candidates
from .mr_typedef_indirect_struct_arg import build_mutation as typedef
from .constraint_witness import verify_constraint_witness
from .source_edit import digest,anchor


def scan_prepared(p0,record,runtime,profile):
    route=validate_route(record,Path(record['s_file']).read_bytes(),p0,runtime)
    if record['enrichment_family']=='typedef_callback':
        from .mr_typedef_indirect_struct_arg import Analysis,NotApplicable,K,uid
        analysis=Analysis(p0,runtime,controlled=True);items=[];reasons=[]
        for node in analysis.nodes:
            if node.kind!=K.CALL_EXPR:continue
            try:
                item=analysis.candidate(node)
                if uid(item['variable'])==route['object']['usr']:
                    items.append(dict(field_usr=uid(item['field']),argument_usr=uid(item['variable']),call_offset=node.location.offset,expected_addresses=1))
            except NotApplicable as exc:reasons.append(str(exc))
        return dict(status='APPLICABLE' if len(items)==1 else 'NOT_APPLICABLE',candidates=items,
            route=route,reasons=sorted(set(reasons)) if not items else [])
    controlled=scan_controlled_bitfields(p0,runtime)
    formal=scan_candidates(p0,runtime,profile,native_scanner=lambda p,r:controlled)
    candidates=[dict(field_usr=c['field']['usr'],expected_addresses=len(c['address_anchors'])) for c in formal['candidates']
        if c['isolation']['native_proof']['controlled_flow']['object']['usr']==route['object']['usr']]
    return dict(status='APPLICABLE' if len(candidates)==1 else 'NOT_APPLICABLE',candidates=candidates,route=route,
                reasons=[] if candidates else formal.get('rejected_fields',[]))


def build_oracle(p0,p1_path,record,runtime,profile,admission):
    s=Path(record['s_file']).read_bytes();data=Path(p0).read_bytes()
    route=validate_route(record,s,p0,runtime)
    if record['enrichment_family']=='typedef_callback':
        result=typedef(p0,p1_path,runtime,admission,controlled=True)
        if result['status']!='APPLIED':return dict(status='MR_NOT_APPLICABLE',reasons=result['reasons']),None
        formal=result['witness'].to_dict();p1=result['p1']
        if formal['call_expression']['argument_variable_usr']!=route['object']['usr']:raise ValueError('FORMAL_CALL_NOT_ON_ORIGINAL_VALUE_ROUTE')
        if formal['isolation']['initializer_proof']['parameter']['offset']!=route['parameter']['offset']:raise ValueError('INITIALIZER_PARAMETER_ROUTE_MISMATCH')
    else:
        scan=scan_candidates(p0,runtime,profile,native_scanner=scan_controlled_bitfields)
        indices=[i for i,c in enumerate(scan['candidates']) if c['isolation']['native_proof']['controlled_flow']['object']['usr']==route['object']['usr']]
        if len(indices)!=1:return dict(status='MR_NOT_APPLICABLE',scan=scan),None
        _,p1,w=bitfield(p0,p1_path,runtime,profile,admission,
            dict(source_kind='Enriched',original_path=record['s_file'],original_sha256=record['s_sha256'],active_enrichment=record),
            candidate_index=indices[0],native_scanner=scan_controlled_bitfields)
        formal=w.to_dict()
    envelope=dict(schema='stage2g-active-oracle-v1',source_kind='Enriched',s_file=record['s_file'],s_sha256=digest(s),
        p0_file=str(Path(p0).resolve()),p0_sha256=digest(data),p1_file=str(Path(p1_path).resolve()),p1_sha256=digest(p1),
        mr_id=record['mr_id'],active_enrichment=record,value_route=route,formal_witness=formal,
        proof_boundary='local immutable-parameter routing plus closed controlled MR subset; not a whole-program C11/UB verifier')
    verify_active_oracle(envelope,s,data,p1)
    return envelope,p1


def verify_active_oracle(envelope,s,p0,p1):
    w=envelope
    if w['schema']!='stage2g-active-oracle-v1' or [digest(s),digest(p0),digest(p1)]!=[w['s_sha256'],w['p0_sha256'],w['p1_sha256']]:raise ValueError('ACTIVE_ORACLE_HASH_OR_SCHEMA')
    if w['mr_id']!=w['active_enrichment']['mr_id'] or w['mr_id']!=w['formal_witness']['mr_id']:raise ValueError('ACTIVE_MR_ID_MISMATCH')
    verify_enrichment(w['active_enrichment'],s,p0)
    route=w['value_route']
    keys=['parameter','initializer_read','object','original_operation','route_expression']
    if route.get('helper_anchor'):keys.append('helper_anchor')
    for key in keys:
        a=route[key];expected=anchor(w['p0_file'],p0,a['offset'],a['start_offset'],a['end_offset'],a['node_identity'])
        if any(a[k]!=v for k,v in expected.items()):raise ValueError('ACTIVE_ROUTE_ANCHOR')
    if route['initializer_read']['referenced_usr']!=route['parameter']['usr']:raise ValueError('ACTIVE_READ_IDENTITY')
    if route['parameter']['offset']!=mapped_offset(w['active_enrichment'],w['active_enrichment']['position']['declaration']['offset']):raise ValueError('ACTIVE_DECLARATION_MAP')
    formal=w['formal_witness']
    if formal['source_map']['p0_file']!=w['p0_file'] or formal['source_map']['p1_file']!=w['p1_file']:raise ValueError('ACTIVE_FORMAL_FILE_MISMATCH')
    if 'BITFIELD' in w['mr_id']:
        if formal['isolation']['native_proof']['controlled_flow']['object']['usr']!=route['object']['usr']:raise ValueError('ACTIVE_OBJECT_IDENTITY')
    elif formal['call_expression']['argument_variable_usr']!=route['object']['usr'] or route['helper_anchor']['usr']!=route['helper_usr']:
        raise ValueError('ACTIVE_CALLBACK_ROUTE_IDENTITY')
    if 'BITFIELD' in w['mr_id']:verify_bitfield_witness(w['formal_witness'],p0,p1,s)
    else:verify_constraint_witness(w['formal_witness'],p0,p1)
    return True
