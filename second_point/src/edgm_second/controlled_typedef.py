"""Opt-in local macro/location policy for the existing typedef MR."""
from pathlib import Path
from .mr_typedef_indirect_struct_arg import K,require


def check_regions(analysis,nodes):
    macros=[(n.extent.start.offset,n.extent.end.offset) for n in analysis.nodes if n.kind==K.MACRO_INSTANTIATION]
    records=[n for n in nodes if n.kind==K.STRUCT_DECL]
    for record in records:
        require(all(record.type.get_align()>=f.type.get_align() for f in record.get_children() if f.kind==K.FIELD_DECL),
                'PACKED_RECORD_OUTSIDE_CONTROLLED_SUBSET')
    for field in (n for n in nodes if n.kind==K.FIELD_DECL):
        require(field.semantic_parent.type.get_align()>=field.type.get_align(),'PACKED_CALLBACK_OUTSIDE_CONTROLLED_SUBSET')
    require(not any(n.kind==K.INCLUSION_DIRECTIVE and n.location.offset>min(r.extent.start.offset for r in records)
                    for n in analysis.nodes),'LATE_INCLUDE_UNCLOSED')
    for n in nodes:
        a,b=n.extent.start.offset,n.extent.end.offset
        require(not any(a<y and x<b for x,y in macros),'CONTROLLED_REGION_MACRO')
        tokens=list(n.get_tokens())
        require(tokens and all(t.location.file and Path(t.location.file.name).resolve()==analysis.path
            and analysis.data[t.extent.start.offset:t.extent.end.offset]==t.spelling.encode() for t in tokens),'CONTROLLED_REGION_LOCATION')
        require(not any(t.spelling in ('#','##','__attribute__','__declspec','_Pragma') for t in tokens),'CONTROLLED_REGION_ATTRIBUTE')
