"""AST-only proof subset for private split declaration/address/value routing.

No recognition by generated names. No general native rule is relaxed.
"""
import json
from .seed_enrichment import Context, K, T
from .semantic_profile import usr
from .source_edit import digest,anchor


def need(value,reason):
    if not value:raise ValueError(reason)


def flow_seal(proof):
    return digest(json.dumps({k:v for k,v in proof.items() if k!='seal'},sort_keys=True).encode())


def scan_controlled_bitfields(path,runtime):
    ctx=Context(path,runtime);result=dict(status='NOT_APPLICABLE',candidate_count=0,candidates=[],rejected_fields=[],sha256=digest(ctx.data))
    for f,field in enumerate(ctx.nodes):
        if field.kind!=K.FIELD_DECL:continue
        fid=usr(field);uses=[i for i in ctx.refs[fid] if ctx.nodes[i].kind==K.MEMBER_REF_EXPR]
        if not uses:continue
        record=field.semantic_parent;rid=usr(record)
        try:
            typ=field.type.get_canonical()
            need(not field.is_bitfield() and typ.kind in (T.INT,T.UINT) and not typ.is_const_qualified() and not typ.is_volatile_qualified(),'FIELD_TYPE_OR_ATTRIBUTE_UNSUPPORTED')
            need(record.kind==K.STRUCT_DECL and record.is_definition() and record.semantic_parent.kind==K.FUNCTION_DECL,'REQUIRES_PRIVATE_LOCAL_STRUCT')
            fields=[n for n in record.get_children() if n.kind==K.FIELD_DECL]
            need(len(fields)==1 and usr(fields[0])==fid,'REQUIRES_SINGLE_FIELD')
            need(record.type.get_align()>=typ.get_align() and field.get_field_offsetof()==0,
                 'PACKED_OR_UNPROVEN_MEMBER_ALIGNMENT')
            r=ctx.index[record];func=ctx.index[record.semantic_parent]
            bodies=[i for i in ctx.children[func] if ctx.nodes[i].kind==K.COMPOUND_STMT];need(len(bodies)==1,'FUNCTION_BODY_UNPROVEN');body=bodies[0]
            refs=[i for i in ctx.refs[rid] if ctx.nodes[i].kind==K.TYPE_REF]
            need(len(refs)==1,'RECORD_TYPE_ESCAPES')
            obj=ctx.parents[refs[0]];need(obj>=0 and ctx.nodes[obj].kind==K.VAR_DECL,'RECORD_OBJECT_UNPROVEN')
            object_=ctx.nodes[obj]
            need(object_.type.get_canonical().kind==T.RECORD and usr(object_.type.get_canonical().get_declaration())==rid and object_.storage_class.name in ('NONE','AUTO'),'OBJECT_TYPE_OR_STORAGE_UNSUPPORTED')
            init=[j for j in ctx.children[obj] if ctx.nodes[j].kind==K.INIT_LIST_EXPR];need(len(init)==1,'INITIALIZER_UNPROVEN')
            reads=[];stack=list(ctx.children[init[0]])
            while stack:
                j=stack.pop();stack.extend(ctx.children[j])
                if ctx.nodes[j].kind==K.DECL_REF_EXPR:reads.append(j)
            need(len(reads)==1,'INITIALIZER_NOT_SINGLE_READ');read=reads[0];param=ctx.nodes[read].referenced
            need(param.kind==K.PARM_DECL and usr(param.semantic_parent)==usr(ctx.nodes[func]) and param.type.get_canonical()==typ,'INITIALIZER_PARAMETER_TYPE_UNPROVEN')
            need([t.spelling for t in ctx.nodes[init[0]].get_tokens()]==['{',param.spelling,'}'],'INITIALIZER_GRAMMAR_UNPROVEN')
            record_statement=ctx.parents[r];object_statement=ctx.parents[obj]
            need(ctx.children[body][:2]==[record_statement,object_statement] and ctx.nodes[record_statement].kind==K.DECL_STMT and ctx.nodes[object_statement].kind==K.DECL_STMT,'OBJECT_ENTRY_DOMINANCE_UNPROVEN')
            object_refs=ctx.refs[usr(object_)]
            need(len(object_refs)==len(uses) and all(ctx.operation_parent(i)[1] in uses for i in object_refs),'RECORD_VALUE_ESCAPE')
            addresses=[];pointers=[]
            for use in uses:
                _,a=ctx.operation_parent(use)
                need(a>=0 and ctx.nodes[a].kind==K.UNARY_OPERATOR and ctx.op(a)=='&','NON_ADDRESS_FIELD_USE')
                _,p=ctx.operation_parent(a)
                need(p>=0 and ctx.nodes[p].kind==K.VAR_DECL and ctx.nodes[p].type.get_canonical().kind==T.POINTER and ctx.nodes[p].type.get_canonical().get_pointee()==typ,'ADDRESS_NOT_PRIVATE_POINTER_INITIALIZER')
                need(ctx.nodes[p].storage_class.name in ('NONE','AUTO'),'POINTER_STORAGE_UNSUPPORTED')
                addresses.append(a);pointers.append(p)
            need(len(pointers) in (1,2) and len(set(pointers))==len(pointers),'POINTER_COUNT_UNSUPPORTED')
            pointers.sort(key=lambda p:ctx.nodes[p].location.offset)
            dereferences=set();all_pointer_reads=[]
            for pointer in pointers:
                refs=ctx.refs[usr(ctx.nodes[pointer])];need(bool(refs),'POINTER_NOT_USED')
                all_pointer_reads.extend(refs)
                for use in refs:
                    j=ctx.parents[use]
                    while j>=0 and not(ctx.nodes[j].kind==K.UNARY_OPERATOR and ctx.op(j)=='*'):j=ctx.parents[j]
                    need(j>=0,'POINTER_ESCAPE_OR_OTHER_USE');dereferences.add(j)
            need(len(dereferences)==1,'VALUE_ROUTE_NOT_SINGLE_DEREFERENCE');deref=next(iter(dereferences))
            ps=[ctx.nodes[p].spelling for p in pointers]
            expected=['*',ps[0]] if len(ps)==1 else ['*','(',ps[0],'==',ps[1],'?',ps[0],':',ps[1],')']
            need([t.spelling for t in ctx.nodes[deref].get_tokens()]==expected and ctx.is_read(deref),'DEREFERENCE_ROUTE_UNPROVEN')
            need(len(all_pointer_reads)==(1 if len(ps)==1 else 4),'POINTER_REFERENCE_COUNT_UNPROVEN')
            scopes=[ctx.parents[ctx.parents[p]] for p in pointers]
            need(len(set(scopes))==1,'POINTER_SCOPE_UNPROVEN');scope=scopes[0]
            if len(ps)==1:need(scope==body and ctx.children[body][2]==ctx.parents[pointers[0]],'POINTER_ENTRY_DOMINANCE_UNPROVEN')
            else:
                need(scope!=body and ctx.nodes[scope].kind==K.COMPOUND_STMT and ctx.children[scope][:2]==[ctx.parents[p] for p in pointers],'NESTED_POINTER_DOMINANCE_UNPROVEN')
                stack=list(ctx.children[scope])
                while stack:
                    j=stack.pop();need(ctx.nodes[j].kind not in (K.LABEL_STMT,K.GOTO_STMT,K.CASE_STMT,K.DEFAULT_STMT),'NESTED_CONTROL_ENTRY_UNPROVEN');stack.extend(ctx.children[j])
            need(all(ctx.clean(i) for i in [f,r,obj,init[0],read,deref,*addresses,*pointers]),'MACRO_OR_LOCATION_UNPROVEN')
            need(not any(n.kind==K.INCLUSION_DIRECTIVE and n.location.offset>record.extent.start.offset for n in ctx.nodes),'LATE_INCLUDE_UNCLOSED')
            proof=dict(schema='stage2g-private-routing-v1',p0_sha256=digest(ctx.data),field_usr=fid,record_usr=rid,
                record=ctx.anchor(r),object=ctx.anchor(obj),parameter_read=ctx.anchor(read),parameter_declaration=ctx.anchor(ctx.index[param]),dereference=ctx.anchor(deref),
                pointer_declarations=[ctx.anchor(p) for p in pointers],pointer_uses=[ctx.anchor(p) for p in all_pointer_reads],
                addresses=[ctx.anchor(a) for a in addresses],address_count=len(addresses),pointer_read_count=len(all_pointer_reads),
                entry_initialized=True,type_and_value_uses_closed=True,no_layout_use=True,no_address_escape=True,
                cross_statement=True,cross_scope=scope!=body,cross_function=False)
            proof['seal']=flow_seal(proof)
            result['candidates'].append(dict(field=ctx.anchor(f),record_usr=rid,address_anchors=proof['addresses'],
                impact_closed=True,reasons=[],controlled_flow=proof,proof=['private record/type/object uses closed','full target width selected independently by formal MR']))
        except ValueError as exc:result['rejected_fields'].append(dict(field=ctx.anchor(f),reason=str(exc)))
    result.update(candidate_count=len(result['candidates']),status='APPLICABLE' if result['candidates'] else 'NOT_APPLICABLE')
    return result


def verify_controlled_flow(proof,p0,fid,rid):
    need(proof['schema']=='stage2g-private-routing-v1' and proof['p0_sha256']==digest(p0) and proof['field_usr']==fid and proof['record_usr']==rid,'CONTROLLED_IDENTITY_MISMATCH')
    need(proof['seal']==flow_seal(proof),'CONTROLLED_EVIDENCE_CHANGED')
    need(all(proof[k] is True for k in ('entry_initialized','type_and_value_uses_closed','no_layout_use','no_address_escape','cross_statement')),'CONTROLLED_CLOSURE_UNPROVEN')
    need(proof['address_count']==len(proof['addresses'])==len(proof['pointer_declarations']) in (1,2),'CONTROLLED_ADDRESS_COUNT')
    need(proof['pointer_read_count']==len(proof['pointer_uses'])==(1 if proof['address_count']==1 else 4),'CONTROLLED_POINTER_USE_COUNT')
    need(proof['parameter_read']['referenced_usr']==proof['parameter_declaration']['usr'],'CONTROLLED_PARAMETER_IDENTITY')
    for a in [proof['record'],proof['object'],proof['parameter_read'],proof['parameter_declaration'],proof['dereference'],*proof['addresses'],*proof['pointer_declarations'],*proof['pointer_uses']]:
        expected=anchor(a['file'],p0,a['offset'],a['start_offset'],a['end_offset'],a['node_identity'])
        need(all(a[k]==v for k,v in expected.items()),'CONTROLLED_ANCHOR_MISMATCH')
    for a in proof['addresses']:need(a['source_text'].startswith('&'),'CONTROLLED_ADDRESS_OPERATOR')
    return True
