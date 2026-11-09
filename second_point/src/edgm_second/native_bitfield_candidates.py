"""Read-only complete acceptance checks for a deliberately small safe subset.

No transformation and no claim to enumerate all theoretically safe C11 edits.
A candidate is one field edit, with every affected address expression retained.
"""
from collections import Counter
from pathlib import Path
from .seed_enrichment import Context, ParseFailure, K, T, READ_BINARY
from .semantic_profile import usr
from .source_edit import digest


def scan_native(path, runtime):
    result = dict(source=str(Path(path).resolve()), status='SCAN_FAILED', candidate_count=None,
                  candidates=[], rejected_fields=[], subset='plain-int struct; direct local zero-initialized objects; closed member uses')
    try:
        ctx = Context(path, runtime)
        result.update(sha256=digest(ctx.data), ast_status='OK',
            main_file_node_count=sum(n.kind not in (K.TRANSLATION_UNIT, K.MACRO_INSTANTIATION,
                K.MACRO_DEFINITION, K.INCLUSION_DIRECTIVE) for n in ctx.nodes))
        members = {usr(n): i for i,n in enumerate(ctx.nodes) if n.kind == K.FIELD_DECL}
        addresses = {}
        for i,n in enumerate(ctx.nodes):
            if n.kind != K.MEMBER_REF_EXPR:
                continue
            _, p = ctx.operation_parent(i)
            if p >= 0 and ctx.nodes[p].kind == K.UNARY_OPERATOR and ctx.op(p) == '&':
                addresses.setdefault(usr(n.referenced), []).append((i,p))
        result['raw_address_relation_count'] = sum(len(v) for v in addresses.values())
        # Layout-sensitive constructs are a whole-file veto in this first subset.
        # This is token screening AFTER AST identity analysis, not a semantic census.
        tokens = {t.spelling for t in ctx.tu.get_tokens(extent=ctx.tu.cursor.extent)}
        layout = bool(tokens & {'sizeof', '_Alignof', '_Alignas', '__attribute__', '__attribute',
                                '__builtin_offsetof', 'offsetof', '_Static_assert', 'pragma', 'asm', '__asm__'})
        for identity, edges in sorted(addresses.items()):
            if identity not in members:
                # Runtime field declarations are never promoted to main-file candidates.
                continue
            f = members[identity]; field = ctx.nodes[f]; record = field.semantic_parent
            rid = usr(record); reasons = set()
            typ = field.type.get_canonical()
            if field.is_bitfield() or typ.kind not in (T.INT, T.UINT, T.BOOL) or typ.is_volatile_qualified() or typ.is_const_qualified():
                reasons.add('FIELD_NOT_SUPPORTED_ORDINARY_INT_UINT_BOOL')
            if record.kind != K.STRUCT_DECL or not record.is_definition():
                reasons.add('UNION_OR_INCOMPLETE_RECORD_OUTSIDE_SUBSET')
            rindex = ctx.index.get(record)
            if any(n.kind == K.INCLUSION_DIRECTIVE and n.location.offset > record.extent.start.offset for n in ctx.nodes):
                reasons.add('LATE_HEADER_INCLUDE_OUTSIDE_CLOSED_WORLD')
            if rindex is None or not ctx.clean(f) or not ctx.clean(rindex):
                reasons.add('DECLARATION_MACRO_OR_LOCATION_UNPROVEN')
            # Exact ordinary declarator grammar; excludes attributes, commas, arrays,
            # typedef spelling ambiguities and macro-derived declaration tokens.
            spelling = [t.spelling for t in field.get_tokens()]
            if ctx.data[field.extent.end.offset:].lstrip()[:1] != b';':
                reasons.add('SINGLE_PLAIN_DECLARATOR_UNPROVEN')
            if spelling not in (["int",field.spelling], ["unsigned","int",field.spelling],
                                 ["unsigned",field.spelling], ["signed","int",field.spelling],
                                 ["signed",field.spelling], ["_Bool",field.spelling]):
                reasons.add('SINGLE_PLAIN_DECLARATOR_UNPROVEN')
            if layout:
                reasons.add('LAYOUT_SENSITIVE_CONSTRUCT_IN_FILE')
            definitions = [n for n in ctx.nodes if n.kind in (K.STRUCT_DECL,K.UNION_DECL) and usr(n)==rid]
            if len(definitions) != 1:
                reasons.add('RECORD_REDECLARATION')
            record_fields = [n for n in record.get_children() if n.kind == K.FIELD_DECL]
            if any(n.type.get_canonical().kind not in (T.INT,T.UINT,T.BOOL) or n.is_bitfield()
                   or n.type.get_canonical().is_volatile_qualified() for n in record_fields):
                reasons.add('OTHER_RECORD_FIELDS_OUTSIDE_CLOSED_SUBSET')
            objects = []
            for i,n in enumerate(ctx.nodes):
                if n.kind != K.TYPE_REF or usr(n.referenced) != rid:
                    continue
                p = ctx.parents[i]
                if p < 0 or ctx.nodes[p].kind != K.VAR_DECL:
                    reasons.add('RECORD_TYPE_ESCAPES_LOCAL_OBJECT_DECLARATION'); continue
                obj = ctx.nodes[p]
                if obj.type.get_canonical().kind != T.RECORD or usr(obj.type.get_canonical().get_declaration()) != rid:
                    reasons.add('POINTER_ARRAY_OR_DERIVED_RECORD_USE'); continue
                if obj.semantic_parent.kind != K.FUNCTION_DECL or obj.storage_class.name not in ('NONE','AUTO'):
                    reasons.add('NON_AUTOMATIC_RECORD_OBJECT'); continue
                initializers = [j for j in ctx.children[p] if ctx.nodes[j].kind == K.INIT_LIST_EXPR]
                if len(initializers)!=1 or ctx.text(initializers[0]).replace(b' ',b'').replace(b'\n',b'') != b'{0}':
                    reasons.add('INITIALIZATION_OUTSIDE_ZERO_SUBSET')
                objects.append(p)
                for use in ctx.refs[usr(obj)]:
                    _, parent = ctx.operation_parent(use)
                    if parent<0 or ctx.nodes[parent].kind != K.MEMBER_REF_EXPR:
                        reasons.add('RECORD_VALUE_ESCAPE_OR_NON_MEMBER_USE')
            if not objects:
                reasons.add('NO_CLOSED_LOCAL_RECORD_OBJECT')
            # Every field reference must be an ordinary member expression under
            # an enumerated operation. sizeof/casts/unknown builtins never pass.
            uses = ctx.refs[identity]
            for i in uses:
                _, p = ctx.operation_parent(i)
                if ctx.nodes[i].kind != K.MEMBER_REF_EXPR or not ctx.clean(i) or p<0 or not ctx.clean(p):
                    reasons.add('FIELD_USE_MACRO_OR_LOCATION_UNPROVEN'); continue
                kind, op = ctx.nodes[p].kind, ctx.op(p)
                if not ((kind == K.UNARY_OPERATOR and op in {'&','+','-','!','~','++','--'})
                    or (kind == K.BINARY_OPERATOR and op in READ_BINARY | {'='})
                    or kind == K.COMPOUND_ASSIGNMENT_OPERATOR
                    or kind in (K.RETURN_STMT,K.CALL_EXPR)):
                    reasons.add('UNCLASSIFIED_FIELD_USE')
                # Member base must be an actual direct closed local object.
                kids = ctx.children[i]
                if len(kids)!=1:
                    reasons.add('UNPROVEN_MEMBER_BASE'); continue
                base = kids[0]
                while ctx.nodes[base].kind in (K.UNEXPOSED_EXPR,K.PAREN_EXPR) and len(ctx.children[base])==1:
                    base = ctx.children[base][0]
                if ctx.nodes[base].kind != K.DECL_REF_EXPR or usr(ctx.nodes[base].referenced) not in {usr(ctx.nodes[o]) for o in objects}:
                    reasons.add('MEMBER_BASE_OUTSIDE_CLOSED_OBJECTS')
            entry = dict(field=ctx.anchor(f), record_usr=rid, canonical_type=typ.kind.name,
                address_anchors=[ctx.anchor(p) for _,p in edges], member_use_count=len(uses),
                object_anchors=[ctx.anchor(o) for o in objects], legal_width=1,
                impact_closed=not reasons, reasons=sorted(reasons))
            if reasons:
                result['rejected_fields'].append(entry)
            else:
                entry['proof'] = ['ordinary supported basic type, width 1 representable',
                    'single exact nonmacro declarator', 'only automatic direct zero-initialized objects',
                    'no record type/value escape or layout-sensitive constructs',
                    'all field uses classified; all address expressions enumerated by identity']
                result['candidates'].append(entry)
        counts = Counter(reason for e in result['rejected_fields'] for reason in e['reasons'])
        result.update(status='APPLICABLE' if result['candidates'] else 'NOT_APPLICABLE',
                      candidate_count=len(result['candidates']), rejection_counts=dict(sorted(counts.items())),
                      source_unchanged=ctx.path.read_bytes()==ctx.data)
    except ParseFailure as exc:
        result.update(status='PARSE_FAILED', ast_status='ERROR', diagnostics=exc.args[0])
    except Exception as exc:
        result.update(status='SCAN_FAILED', exception=f'{type(exc).__name__}: {exc}')
    return result
