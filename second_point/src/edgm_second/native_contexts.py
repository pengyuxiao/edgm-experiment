"""Objective, source-linked facts for address-bearing fields, including rejects.

This is a census, not an action encoder or a runtime points-to analysis.
Shared canonical record identity establishes static type dependencies only.
"""
from .seed_enrichment import Context, K, T
from .semantic_profile import usr
from .source_edit import digest
from .scoped_layout import type_graph


def target_paths(graph):
    paths=[]
    def walk(i,path,seen):
        n=graph['nodes'][i]
        if n['record_usr']==graph['target_record_usr']:
            paths.append(path+[i]);return
        if i in seen:return
        for child in n['edges']:walk(child,path+[i],seen|{i})
    walk(graph['root'],[],set())
    return [[dict(kind=graph['nodes'][i]['kind'],record_usr=graph['nodes'][i]['record_usr']) for i in p] for p in paths]


def extract_contexts(path,runtime,formal,discovery,origin='Native',context=None):
    ctx=context or Context(path,runtime); source_hash=digest(ctx.data)
    owners={}; scopes={}; units={}
    def owner(i):
        if i in owners:return owners[i]
        p=i
        while p>=0:
            n=ctx.nodes[p]
            if n.kind==K.FUNCTION_DECL:
                result=dict(usr=usr(n),name=n.spelling,offset=n.location.offset)
                owners[i]=result;return result
            p=ctx.parents[p]
        owners[i]=None;return None
    def container(i,kind):
        cache=scopes if kind=='scope' else units
        if i in cache:return cache[i]
        p=i
        while p>=0:
            n=ctx.nodes[p]
            is_scope=n.kind in (K.TRANSLATION_UNIT,K.COMPOUND_STMT)
            is_unit=n.kind.is_statement() or (ctx.parents[p]==0 and n.kind.is_declaration())
            if (is_scope if kind=='scope' else is_unit):
                a=dict(kind=n.kind.name,start=n.extent.start.offset,end=n.extent.end.offset,
                    id=f'{source_hash}:{n.kind.name}:{n.extent.start.offset}:{n.extent.end.offset}')
                cache[i]=a;return a
            p=ctx.parents[p]
        return None
    accepted={e['field']['usr']:e for e in formal['candidates']}
    formal_rejects={e['field']['usr']:e['reason'] for e in formal['rejected_fields']}
    fields={usr(n):i for i,n in enumerate(ctx.nodes) if n.kind==K.FIELD_DECL}
    refs={usr(n):i for i,n in enumerate(ctx.nodes) if n.kind in (K.VAR_DECL,K.PARM_DECL)}
    entries=discovery['candidates']+discovery['rejected_fields'];result=[]
    graph_cache={}
    for entry in entries:
        fid=entry['field']['usr'];f=fields[fid];field=ctx.nodes[f];rid=usr(field.semantic_parent)
        def graph_for(t):
            t=t.get_canonical();key=(rid,t.kind.name,t.spelling)
            if key not in graph_cache:graph_cache[key]=type_graph(t,rid)
            return graph_cache[key]
        declarations=[]
        for identity,i in refs.items():
            g=graph_for(ctx.nodes[i].type)
            if g['related']:
                declarations.append(dict(anchor=ctx.anchor(i),usr=identity,kind=ctx.nodes[i].kind.name,
                    function=owner(i),scope=container(i,'scope'),type_paths=target_paths(g),
                    type_graph_complete=not g['unknown']))
        # Avoid embedding full function/variable initializers repeatedly in the
        # declaration census: retain the exact anchor range and physical position.
        for d in declarations:d['anchor'].pop('source_text',None)
        uses=[i for i in ctx.refs[fid] if ctx.nodes[i].kind==K.MEMBER_REF_EXPR]
        relations=[]
        for m in uses:
            _,a=ctx.operation_parent(m)
            if a<0 or ctx.nodes[a].kind!=K.UNARY_OPERATOR or ctx.op(a)!='&':continue
            base=ctx.children[m][0] if ctx.children[m] else None
            g=graph_for(ctx.nodes[base].type) if base is not None else None
            path_refs=[]
            stack=list(ctx.children[m])
            while stack:
                j=stack.pop();n=ctx.nodes[j]
                if n.kind==K.DECL_REF_EXPR and n.referenced:
                    rg=graph_for(n.referenced.type)
                    path_refs.append(dict(reference=ctx.anchor(j),declaration_usr=usr(n.referenced),
                        declaration=ctx.anchor(ctx.index[n.referenced]) if n.referenced in ctx.index else None,
                        declaration_function=owner(ctx.index[n.referenced]) if n.referenced in ctx.index else None,
                        type_paths=target_paths(rg),type_graph_complete=not rg['unknown']))
                stack.extend(ctx.children[j])
            for d in path_refs:
                if d['declaration']:d['declaration'].pop('source_text',None)
            same_expression=ctx.nodes[a].extent.start.offset<=field.extent.start.offset and field.extent.end.offset<=ctx.nodes[a].extent.end.offset
            relations.append(dict(field_usr=fid,record_usr=rid,member=ctx.anchor(m),address=ctx.anchor(a),
                declaration_function=owner(f),use_function=owner(m),declaration_scope=container(f,'scope'),
                use_scope=container(m,'scope'),declaration_unit=container(f,'unit'),use_unit=container(m,'unit'),
                same_local_expression=same_expression,cross_statement=container(f,'unit')!=container(m,'unit'),
                cross_scope=container(f,'scope')!=container(m,'scope'),
                base_type_paths=target_paths(g) if g else [],base_type_related=bool(g and g['related']),
                base_references=path_refs,dependency_path=[dict(kind='record_definition',usr=rid),
                    dict(kind='field_declaration',usr=fid),dict(kind='member_reference',referenced_usr=usr(ctx.nodes[m].referenced),offset=ctx.nodes[m].location.offset),
                    dict(kind='address_operation',offset=ctx.nodes[a].location.offset)],
                byte_span=abs(ctx.nodes[a].location.offset-field.location.offset),
                line_span=abs(ctx.anchor(a)['line']-ctx.anchor(f)['line'])))
        functions={e['use_function']['usr'] for e in relations if e['use_function'] and e['base_type_related']}
        cross_function=len(functions)>1
        depths=[len(p)-1 for d in declarations for p in d['type_paths']]
        result.append(dict(mr_id='MR_MEMBER_BITFIELD_ADDRESSABILITY',constraint_category='address_of_bitfield',
            seed_sha256=source_hash,source=str(ctx.path),origin=origin,target_declaration=ctx.anchor(f),record_usr=rid,
            canonical_basic_type=field.type.get_canonical().kind.name,record_kind=field.semantic_parent.kind.name,
            originally_bitfield=field.is_bitfield(),qualifiers=dict(const=field.type.get_canonical().is_const_qualified(),volatile=field.type.get_canonical().is_volatile_qualified()),
            related_declaration_count=len(declarations),related_declarations=declarations,
            record_type_references=[dict(anchor=ctx.anchor(i),function=owner(i),scope=container(i,'scope'),
                parent_kind=ctx.nodes[ctx.parents[i]].kind.name if ctx.parents[i]>=0 else None)
                for i in ctx.refs[rid] if ctx.nodes[i].kind==K.TYPE_REF],
            direct_record_object_count=sum(ctx.nodes[refs[d['usr']]].type.get_canonical().kind==T.RECORD for d in declarations),
            all_member_expression_uses=len(uses),target_address_count=len(relations),relations=relations,
            cross_statement=any(e['cross_statement'] for e in relations),cross_scope=any(e['cross_scope'] for e in relations),
            cross_function=cross_function,cross_function_evidence=dict(kind='shared_canonical_record_type',functions=sorted(functions),
                paths=[e['dependency_path'] for e in relations if e['use_function'] and e['base_type_related']]) if cross_function else None,
            runtime_cross_function_value_flow='NOT_PROVEN',maximum_observed_type_propagation_depth=max(depths) if depths else None,
            impact_closed=fid in accepted,safe_candidate=fid in accepted,formal_rejection=formal_rejects.get(fid),
            overlapping_reasons=entry.get('reasons',[]),layout_analysis=entry.get('layout_independence'),
            complexity=('simple_local_expression' if all(e['same_local_expression'] for e in relations) else
                        'shared_type_across_functions' if cross_function else 'cross_scope' if any(e['cross_scope'] for e in relations) else 'cross_statement'),
            limitations=['static declaration/type/reference facts; no runtime alias proof',
                         'rejected-field complexity is not a safe MR action', 'distance and AST depth do not establish semantic complexity']))
    return result
