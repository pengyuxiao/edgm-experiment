"""Closed AST subset: a private record-copy pair with a scalar const leaf.

Recognition uses declaration identities and complete reference closure, never
generator names or provenance labels. Only P0 is parsed; P1 is a byte edit.
"""
import json
from pathlib import Path
from .seed_enrichment import Context,K,T
from .semantic_profile import usr
from .source_edit import ByteEdit,digest,anchor
from .active_enrichment import verify_enrichment
from .aggregate_enrichment import MR_ID

CATEGORY='aggregate_not_modifiable_due_to_const_member'


def require(value,reason):
    if not value:raise ValueError(reason)


def seal(value):return digest(json.dumps({k:v for k,v in value.items() if k!='seal'},sort_keys=True).encode())


def scan(path,runtime):
    ctx=Context(path,runtime);accepted=[];rejected=[]
    for fi,field in enumerate(ctx.nodes):
        if field.kind!=K.FIELD_DECL or field.semantic_parent.semantic_parent.kind!=K.FUNCTION_DECL:continue
        try:
            typ=field.type.get_canonical();inner=field.semantic_parent;rid=usr(inner)
            require(typ.kind in (T.INT,T.UINT) and not typ.is_const_qualified() and not typ.is_volatile_qualified() and not field.is_bitfield(),'LEAF_NOT_PLAIN_UNQUALIFIED_INT')
            fields=list(inner.get_children());require(len(fields)==1 and fields[0]==field and inner.kind==K.STRUCT_DECL and inner.is_definition(),'REQUIRES_SINGLE_SCALAR_STRUCT')
            tokens=[t.spelling for t in field.get_tokens()]
            require(tokens[-1]==field.spelling and ' '.join(tokens[:-1]) in ('int','signed int','unsigned int','unsigned','signed'),'LEAF_DECLARATOR_UNSUPPORTED')
            ir=ctx.index[inner];function=ctx.index[inner.semantic_parent]
            bodies=[i for i in ctx.children[function] if ctx.nodes[i].kind==K.COMPOUND_STMT];require(len(bodies)==1,'BODY_UNPROVEN');body=bodies[0]
            refs=[i for i in ctx.refs[rid] if ctx.nodes[i].kind==K.TYPE_REF];contain=[]
            for i in refs:
                j=ctx.parents[i]
                if ctx.nodes[j].kind==K.FIELD_DECL:contain.append(j)
            outer=inner;outer_i=ir;path_edges=[];extra=[]
            if contain:
                require(len(contain)==len(refs)==1,'INNER_TYPE_ESCAPES')
                link=ctx.nodes[contain[0]];outer=link.semantic_parent;outer_i=ctx.index[outer]
                require(outer.kind==K.STRUCT_DECL and outer.is_definition() and outer.semantic_parent==inner.semantic_parent,'OUTER_SCOPE_OR_KIND')
                ofs=list(outer.get_children());require(1<=len(ofs)<=2 and ofs[0]==link and all(x.kind==K.FIELD_DECL for x in ofs),'OUTER_SHAPE_UNSUPPORTED')
                require(link.type.get_canonical().kind==T.RECORD and usr(link.type.get_canonical().get_declaration())==rid and not link.type.is_const_qualified(),'CONTAINMENT_IDENTITY_UNPROVEN')
                require([t.spelling for t in link.get_tokens()]==['struct',inner.spelling,link.spelling],'CONTAINMENT_DECLARATOR_UNSUPPORTED')
                for x in ofs[1:]:
                    require([t.spelling for t in x.get_tokens()]==['int',x.spelling] and not x.is_bitfield(),'OTHER_FIELD_NOT_PLAIN_INT')
                    require(not ctx.refs[usr(x)],'OTHER_FIELD_USED');extra.append(ctx.index[x])
                path_edges=[dict(outer_usr=usr(outer),member_usr=usr(link),inner_usr=rid,member=ctx.anchor(contain[0]))]
                refs=[i for i in ctx.refs[usr(outer)] if ctx.nodes[i].kind==K.TYPE_REF]
            require(len(refs)==2,'RECORD_TYPE_USES_NOT_CLOSED')
            objects=[ctx.parents[i] for i in refs]
            require(len(set(objects))==2 and all(ctx.nodes[i].kind==K.VAR_DECL for i in objects),'REQUIRES_TWO_PRIVATE_OBJECTS')
            nodes=[ir,outer_i,fi,*contain,*extra,*objects];initializers={}
            for i in objects:
                obj=ctx.nodes[i];ot=obj.type.get_canonical()
                require(obj.semantic_parent==inner.semantic_parent and ot.kind==T.RECORD and usr(ot.get_declaration())==usr(outer) and not ot.is_const_qualified() and not ot.is_volatile_qualified() and obj.storage_class.name in ('NONE','AUTO'),'OBJECT_SCOPE_TYPE_OR_STORAGE')
                init=[j for j in ctx.children[i] if ctx.nodes[j].kind==K.INIT_LIST_EXPR];require(len(init)==1,'OBJECT_NOT_INITIALIZED')
                require([t.spelling for t in obj.get_tokens()][:4]==['struct',outer.spelling,obj.spelling,'='],'OBJECT_DECLARATOR_UNSUPPORTED')
                initializers[i]=init[0];nodes.append(init[0])
            sources=[]
            for i in objects:
                n=ctx.nodes[initializers[i]]
                reads=[j for j,x in enumerate(ctx.nodes) if x.kind==K.DECL_REF_EXPR and n.extent.start.offset<=x.extent.start.offset<n.extent.end.offset]
                if reads:sources.append((i,reads))
            require(len(sources)==1 and len(sources[0][1])==1,'SOURCE_NOT_SINGLE_PARAMETER')
            src,reads=sources[0];read=reads[0];parameter=ctx.nodes[read].referenced;dst=next(i for i in objects if i!=src)
            require(parameter.kind==K.PARM_DECL and parameter.semantic_parent==inner.semantic_parent and parameter.type.get_canonical()==typ,'INITIALIZER_TYPE_OR_IDENTITY')
            nested=bool(path_edges);tag=bool(extra)
            expected_src=['{','{',parameter.spelling,'}']+([',','0'] if tag else [])+['}'] if nested else ['{',parameter.spelling,'}']
            expected_dst=['{','{','0','}']+([',','0'] if tag else [])+['}'] if nested else ['{','0','}']
            require([t.spelling for t in ctx.nodes[initializers[src]].get_tokens()]==expected_src and [t.spelling for t in ctx.nodes[initializers[dst]].get_tokens()]==expected_dst,'INITIALIZATION_GRAMMAR_UNPROVEN')
            assignments=[];leaf_reads=[];allowed_refs=set()
            for use in ctx.refs[usr(ctx.nodes[src])]:
                _,p=ctx.operation_parent(use)
                require(p>=0 and ctx.nodes[p].kind==K.BINARY_OPERATOR and ctx.op(p)=='=','SOURCE_ESCAPE')
                require([t.spelling for t in ctx.nodes[p].get_tokens()]==[ctx.nodes[dst].spelling,'=',ctx.nodes[src].spelling],'ASSIGNMENT_NOT_PLAIN_RECORD_COPY')
                kids=ctx.children[p];lhs=kids[0]
                require(ctx.nodes[lhs].kind==K.DECL_REF_EXPR and usr(ctx.nodes[lhs].referenced)==usr(ctx.nodes[dst]) and ctx.nodes[lhs].type.get_canonical()==ctx.nodes[dst].type.get_canonical(),'ASSIGNMENT_LHS_TYPE')
                require(ctx.parents[p]==body,'ASSIGNMENT_SCOPE_UNPROVEN')
                assignments.append((p,lhs,use));allowed_refs.update((lhs,use))
            require(1<=len(assignments)<=4,'NO_TARGET_OR_TOO_MANY_TARGETS')
            for use in ctx.refs[usr(ctx.nodes[dst])]:
                if use in allowed_refs:continue
                j=use
                for link_id in ([path_edges[0]['member_usr']] if nested else [])+[usr(field)]:
                    j=ctx.parents[j];require(j>=0 and ctx.nodes[j].kind==K.MEMBER_REF_EXPR and usr(ctx.nodes[j].referenced)==link_id,'DESTINATION_ESCAPE_OR_UNCLOSED_MEMBER')
                require(ctx.is_read(j),'DIRECT_MEMBER_WRITE_OR_UNPROVEN_READ');leaf_reads.append(j);nodes.append(j)
            require(bool(leaf_reads) and set(ctx.refs[usr(field)])==set(leaf_reads),'FIELD_USE_CLOSURE')
            if nested:require(set(ctx.refs[path_edges[0]['member_usr']])=={ctx.parents[u] for u in ctx.refs[usr(ctx.nodes[dst])] if u not in allowed_refs},'CONTAINING_MEMBER_CLOSURE')
            prefix=[ctx.parents[i] for i in ([ir,outer_i] if nested else [ir])]+[ctx.parents[src],ctx.parents[dst]]+[a[0] for a in sorted(assignments,key=lambda a:ctx.nodes[a[0]].location.offset)]
            require(ctx.children[body][:len(prefix)]==prefix,'ENTRY_INITIALIZATION_AND_COPY_DOMINANCE')
            for r in (inner,outer):
                grammar=['struct',r.spelling,'{']
                for f in r.get_children():grammar.extend([t.spelling for t in f.get_tokens()]+[';'])
                grammar.append('}')
                require([t.spelling for t in r.get_tokens()]==grammar,'RECORD_GRAMMAR_OR_ATTRIBUTES')
                require(r.type.get_align()>=typ.get_align(),'PACKED_LAYOUT_UNPROVEN')
                require(sum(x.kind==K.STRUCT_DECL and usr(x)==usr(r) for x in ctx.nodes)==1,'RECORD_REDECLARATION')
            require(all(ctx.clean(i) for i in set(nodes+[read,*[j for a in assignments for j in a]])),'MACRO_OR_PHYSICAL_LOCATION')
            require(not any(x.kind==K.INCLUSION_DIRECTIVE and x.location.offset>inner.extent.start.offset for x in ctx.nodes),'LATE_INCLUDE_UNCLOSED')
            # No record-valued expressions besides declarations, initializers,
            # copy operands/conversions, and intermediate contained-member reads.
            permitted={K.STRUCT_DECL,K.FIELD_DECL,K.VAR_DECL,K.INIT_LIST_EXPR,K.TYPE_REF,K.DECL_REF_EXPR,K.UNEXPOSED_EXPR,K.BINARY_OPERATOR,K.MEMBER_REF_EXPR}
            require(all(x.kind in permitted for x in ctx.nodes if x.type.get_canonical().kind==T.RECORD and usr(x.type.get_canonical().get_declaration()) in {rid,usr(outer)}),'OTHER_RECORD_OPERATION')
            proof=dict(field=ctx.anchor(fi),inner=ctx.anchor(ir),outer=ctx.anchor(outer_i),containment=path_edges,
                source=ctx.anchor(src),destination=ctx.anchor(dst),canonical_kind=typ.kind.name,
                parameter=ctx.anchor(ctx.index[parameter]),parameter_read=ctx.anchor(read),leaf_reads=[ctx.anchor(i) for i in leaf_reads],
                initializers=[ctx.anchor(initializers[i]) for i in objects],
                assignments=[dict(expression=ctx.anchor(a),lhs=ctx.anchor(l),rhs=ctx.anchor(r),record_usr=usr(outer)) for a,l,r in assignments],
                closure=dict(type_uses_closed=True,object_references_closed=True,field_reads_only=True,initialized=True,entry_dominance=True,no_layout_or_address_escape=True),
                p0_modifiable=True,p1_modifiable=False,qualifiers_before=[],qualifiers_after=['const'])
            accepted.append(dict(field_usr=usr(field),proof=proof,edit=dict(start=field.extent.start.offset,end=field.extent.end.offset,before=ctx.text(fi).decode(),after='const '+ctx.text(fi).decode())))
        except ValueError as exc:rejected.append(dict(field_usr=usr(field),reason=str(exc)))
    return dict(status='APPLICABLE' if accepted else 'NOT_APPLICABLE',candidates=accepted,rejected_fields=rejected,p0_sha256=digest(ctx.data))


def build(path,p1_path,runtime,admission,enrichment=None,candidate_index=0):
    p0=Path(path).read_bytes()
    require(admission['status']=='valid' and admission['sha256']==digest(p0) and all(admission['checks'][c]['returncode']==0 for c in ('gcc','clang')),'P0_ADMISSION_REQUIRED')
    result=scan(path,runtime);require(len(result['candidates'])>candidate_index,'NO_SAFE_AGGREGATE_CANDIDATE')
    c=result['candidates'][candidate_index];e=c['edit'];edit=ByteEdit(e['start'],e['end'],e['before'].encode(),e['after'].encode());p1=edit.apply(p0)
    proof=c['proof'];targets=[]
    for a in proof['assignments']:
        versions={}
        for key in ('expression','lhs','rhs'):
            old=a[key];new=anchor(p1_path,p1,*[edit.map_offset(old[k]) for k in ('offset','start_offset','end_offset')],old['node_identity'])
            versions[key]=dict(p0=old,p1=new)
        # The = token is a separate exact alternative caret, never a tolerance.
        lhs=a['lhs'];rhs=a['rhs'];gap=p0[lhs['end_offset']:rhs['start_offset']];equal=lhs['end_offset']+gap.index(b'=')
        versions['operator']=dict(p0=anchor(path,p0,equal,equal,equal+1,'assignment_operator'),p1=anchor(p1_path,p1,edit.map_offset(equal),edit.map_offset(equal),edit.map_offset(equal)+1,'assignment_operator'))
        versions['record_usr']=a['record_usr'];targets.append(versions)
    source=Path(enrichment['s_file']).read_bytes() if enrichment else p0
    value_route=None
    if enrichment:
        from .aggregate_enrichment import route
        value_route=route(enrichment,path,runtime,c)
    w=dict(schema_version='stage2h-const-aggregate-v1',mr_id=MR_ID,source_kind='Enriched' if enrichment else 'Native',
        seed_id=digest(source),s_file=enrichment['s_file'] if enrichment else str(Path(path).resolve()),s_sha256=digest(source),
        p0_file=str(Path(path).resolve()),p0_sha256=digest(p0),p1_file=str(Path(p1_path).resolve()),p1_sha256=digest(p1),
        constraint_id='C11:6.5.16p2',standard=dict(document='N1570',clauses=['6.3.2.1p1','6.5.16p2','6.5.16.1p1','6.7.9','5.1.1.3p1']),
        changed_declaration=c['field_usr'],proof=proof,affected_uses=targets,active_enrichment=enrichment,value_route=value_route,
        source_map=dict(edit=e,p0_file=str(Path(path).resolve()),p1_file=str(Path(p1_path).resolve()),byte_delta=len(edit.after)-len(edit.before)),
        expected_error=dict(category=CATEGORY,required_severities=['error','fatal'],warning_satisfies=False,exact_caret_mandated_by_standard=False,expected_violation_count=len(targets)),
        proof_boundary='closed local AST subset and replayable fact certificate, not a full C11 verifier')
    w['seal']=seal(w);verify(w,source,p0,p1);return w,p1


def verify(w,s,p0,p1):
    require(w['seal']==seal(w) and w['schema_version']=='stage2h-const-aggregate-v1' and w['mr_id']==MR_ID,'WITNESS_SEAL_OR_SCHEMA')
    require([digest(s),digest(p0),digest(p1)]==[w['s_sha256'],w['p0_sha256'],w['p1_sha256']] and w['seed_id']==digest(s),'SOURCE_HASH')
    e=w['source_map']['edit'];edit=ByteEdit(e['start'],e['end'],e['before'].encode(),e['after'].encode())
    require(edit.after==b'const '+edit.before and edit.apply(p0)==p1,'CONST_EDIT')
    proof=w['proof'];require(w['changed_declaration']==proof['field']['usr'],'FIELD_IDENTITY')
    require((e['start'],e['end'],e['before'])==(proof['field']['start_offset'],proof['field']['end_offset'],proof['field']['source_text']),'FIELD_EDIT_LOCATION')
    require(set(proof['closure'])=={'type_uses_closed','object_references_closed','field_reads_only','initialized','entry_dominance','no_layout_or_address_escape'},'CLOSURE_FACTS')
    require(proof['p0_modifiable'] and not proof['p1_modifiable'] and all(proof['closure'].values()),'CLOSURE_OR_MODIFIABILITY')
    require(proof['qualifiers_before']==[] and proof['qualifiers_after']==['const'] and proof['canonical_kind'] in ('INT','UINT'),'QUALIFIER_TRANSITION')
    rid=proof['inner']['usr']
    for edge in proof['containment']:
        require(edge['inner_usr']==rid and edge['member']['usr']==edge['member_usr'],'CONTAINMENT_EDGE');rid=edge['outer_usr']
    require(rid==proof['outer']['usr'],'OUTER_TYPE_IDENTITY')
    require(1<=len(w['affected_uses'])==len(proof['assignments'])==w['expected_error']['expected_violation_count']<=4,'TARGET_COVERAGE')
    require(w['expected_error']['category']==CATEGORY and w['constraint_id']=='C11:6.5.16p2','CONSTRAINT_CATEGORY')
    require(w['expected_error']['required_severities']==['error','fatal'] and not w['expected_error']['warning_satisfies'] and not w['expected_error']['exact_caret_mandated_by_standard'],'DIAGNOSTIC_POLICY')
    require(w['source_map']['p0_file']==w['p0_file'] and w['source_map']['p1_file']==w['p1_file'] and w['source_map']['byte_delta']==6,'SOURCE_MAP_FILES_OR_DELTA')
    anchors=[proof[k] for k in ('field','inner','outer','source','destination','parameter','parameter_read')]+proof['initializers']+proof['leaf_reads']+[e['member'] for e in proof['containment']]
    for index,use in enumerate(w['affected_uses']):
        require(use['record_usr']==rid,'ASSIGNMENT_RECORD_TYPE')
        for key in ('expression','lhs','rhs','operator'):
            a=use[key]['p0'];b=use[key]['p1'];anchors.append(a)
            require(b==anchor(w['p1_file'],p1,*[edit.map_offset(a[k]) for k in ('offset','start_offset','end_offset')],a['node_identity']),'EXACT_SOURCE_TRANSPORT')
            if key!='operator':require(a==proof['assignments'][index][key],'ASSIGNMENT_CERTIFICATE')
    for a in anchors:
        expected=anchor(w['p0_file'],p0,a['offset'],a['start_offset'],a['end_offset'],a['node_identity'])
        require(all(a[k]==v for k,v in expected.items()),'SOURCE_ANCHOR')
    require(proof['parameter_read']['referenced_usr']==proof['parameter']['usr'],'INITIALIZER_IDENTITY')
    require(proof['source']['usr']!=proof['destination']['usr'],'DISTINCT_PRIVATE_OBJECTS')
    require(all(a['lhs']['referenced_usr']==proof['destination']['usr'] and a['rhs']['referenced_usr']==proof['source']['usr'] for a in proof['assignments']),'COPY_OBJECT_DECLARATION_IDENTITIES')
    require(all(a['referenced_usr']==proof['field']['usr'] for a in proof['leaf_reads']),'LEAF_READ_DECLARATION_IDENTITY')
    require(proof['inner']['start_offset']<=proof['field']['start_offset']<proof['field']['end_offset']<=proof['inner']['end_offset'],'FIELD_DECLARATION_CONTAINMENT')
    if w['active_enrichment']:
        from .active_enrichment import mapped_offset
        r=w['active_enrichment'];verify_enrichment(r,s,p0)
        require(proof['parameter']['offset']==mapped_offset(r,r['position']['declaration']['offset']),'PARAMETER_SOURCE_MAP')
        require(w['value_route']['leaf_read']==proof['leaf_reads'][0],'VALUE_ROUTE')
    else:require(s==p0,'NATIVE_SOURCE')
    return True
