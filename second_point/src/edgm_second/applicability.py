"""Read-only structural coverage and exhaustive call-site scan for the existing MR."""
from collections import Counter
from pathlib import Path
import time

from clang import cindex as c
from .mr_typedef_indirect_struct_arg import Analysis, ASTParseError, NotApplicable, K, T, uid, unwrap
from .source_edit import digest


def is_function_pointer(type_):
    type_ = type_.get_canonical()
    return type_.kind == T.POINTER and type_.get_pointee().kind in (T.FUNCTIONPROTO, T.FUNCTIONNOPROTO)


def structural_inventory(analysis):
    """Observe structures independently of current MR engineering restrictions."""
    structs, unions, members, indirect_calls, aliases = [], [], [], [], []
    typedef_nodes = analysis.typedefs
    for node in analysis.nodes:
        if node.kind in (K.STRUCT_DECL, K.UNION_DECL) and node.is_definition():
            entry = dict(usr=uid(node), anchor=analysis.node_anchor(node),
                         scope=node.semantic_parent.kind.name,
                         fields=[dict(usr=uid(f), kind=f.type.get_canonical().kind.name,
                                      bitfield=f.is_bitfield()) for f in node.get_children() if f.kind == K.FIELD_DECL])
            (structs if node.kind == K.STRUCT_DECL else unions).append(entry)
        if node.kind == K.FIELD_DECL and is_function_pointer(node.type):
            members.append(dict(usr=uid(node), owner_kind=node.semantic_parent.kind.name,
                owner_usr=uid(node.semantic_parent), type=node.type.get_canonical().spelling,
                anchor=analysis.node_anchor(node)))
        if node.kind == K.CALL_EXPR:
            children = list(node.get_children())
            callee = unwrap(children[0]) if children else None
            if (callee and callee.kind == K.MEMBER_REF_EXPR and callee.referenced
                    and callee.referenced.kind == K.FIELD_DECL and is_function_pointer(callee.referenced.type)):
                indirect_calls.append(dict(anchor=analysis.node_anchor(node),
                    member_usr=uid(callee.referenced), argument_count=len(list(node.get_arguments()))))
        if node.kind == K.TYPEDEF_DECL:
            chain, current = [], node
            while current.kind == K.TYPEDEF_DECL:
                identity = uid(current)
                if identity in chain:
                    break
                chain.append(identity)
                target = current.underlying_typedef_type.get_declaration()
                if uid(target) not in typedef_nodes:
                    break
                current = typedef_nodes[uid(target)]
            canonical = node.underlying_typedef_type.get_canonical()
            root = canonical.get_declaration()
            aliases.append(dict(usr=uid(node), chain=chain, chain_length=len(chain),
                canonical_kind=canonical.kind.name, record_kind=root.kind.name,
                record_usr=uid(root) if canonical.kind == T.RECORD else None,
                scope=node.semantic_parent.kind.name, anchor=analysis.node_anchor(node)))
    return dict(structs=structs, unions=unions, function_pointer_members=members,
                member_indirect_calls=indirect_calls, typedefs=aliases,
                struct_definitions=len(structs), union_definitions=len(unions),
                struct_function_pointer_members=sum(m['owner_kind'] == 'STRUCT_DECL' for m in members),
                member_indirect_call_count=len(indirect_calls), typedef_count=len(aliases),
                record_typedef_chain_count=sum(a['record_kind'] == 'STRUCT_DECL' and a['chain_length'] >= 2 for a in aliases))


def reason_categories(inventory, reasons, supported_records):
    categories = []
    if not inventory['record_typedef_chain_count']:
        categories.append('NO_RELEVANT_STRUCT_TYPEDEF_CHAIN')
    if not inventory['struct_function_pointer_members']:
        categories.append('NO_STRUCT_FUNCTION_POINTER_MEMBER')
    if not inventory['member_indirect_call_count']:
        categories.append('NO_MEMBER_INDIRECT_CALL')
    if len({s['usr'] for s in inventory['structs']}) < 2:
        categories.append('NO_DISTINCT_COMPLETE_STRUCT_PAIR')
    if supported_records < 2:
        categories.append('CURRENT_LAYOUT_RESTRICTION')
    flat = {reason for entry in reasons for reason in entry.split(';')}
    if not inventory['member_indirect_call_count']:
        flat -= {'REQUIRES_ONE_ARGUMENT', 'NOT_MEMBER_INDIRECT_CALL'}
    mapping = {
        'UNSUPPORTED_RECORD_OR_TYPEDEF': 'TYPE_IDENTITY_OR_CHAIN_UNSUPPORTED',
        'P0_TYPES_NOT_IDENTICAL': 'P0_TYPE_COMPATIBILITY_UNPROVEN',
        'NO_DISTINCT_COMPLETE_VISIBLE_STRUCT': 'NO_SAFE_REPLACEMENT_TYPE',
        'ARGUMENT_HAS_OTHER_USES': 'MULTIPLE_AFFECTED_USES',
        'AFFECTED_ALIAS_HAS_OTHER_USES': 'MULTIPLE_AFFECTED_USES',
        'AFFECTED_ALIAS_ESCAPES': 'MULTIPLE_AFFECTED_USES',
        'AFFECTED_TYPEDEF_REDECLARED': 'AFFECTED_TYPEDEF_REDECLARED',
        'REPLACEMENT_TAG_SHADOWED': 'TAG_SHADOWING',
        'PREPROCESSING_OR_ATTRIBUTES_UNSUPPORTED': 'CURRENT_PREPROCESSING_RESTRICTION',
    }
    categories.extend(mapping[r] for r in sorted(flat) if r in mapping)
    if flat - set(mapping) - {'NOT_MEMBER_INDIRECT_CALL', 'NO_CALL_EXPRESSION'}:
        categories.append('OTHER_CURRENT_PRECONDITIONS')
    return sorted(set(categories))


def candidate_summary(analysis, item):
    return dict(candidate_id=f"call:{item['call'].extent.start.offset}",
        call=analysis.node_anchor(item['call']), argument=analysis.node_anchor(item['argument']),
        changed_declaration=analysis.node_anchor(analysis.typedefs[item['changed']]),
        typedef_chain=item['chain'], typedef_chain_length=len(item['chain']),
        expected_parameter_type=analysis.type_info(item['expected_id']),
        p0_argument_type=analysis.type_info(item['actual_id']),
        replacement_argument_type=analysis.type_info(item['replacement_id']),
        function_pointer_member=dict(usr=uid(item['field']), anchor=analysis.node_anchor(item['field']),
                                     canonical_type=item['field'].type.get_canonical().spelling),
        edit=dict(start=item['edit'].start, end=item['edit'].end,
                  before=item['edit'].before.decode(), after=item['edit'].after.decode()))


def analyze_candidates(path, runtime, progress=None):
    started = time.perf_counter()
    report = dict(status='SCAN_FAILED', ast_status='NOT_STARTED', candidate_count=None, candidates=[],
                  main_file_nodes=None, reasons=[], reason_categories=[], diagnostics=[],
                  timing_seconds={}, source_sha256=None)
    try:
        analysis = Analysis(path, runtime)
        report.update(ast_status='OK', main_file_nodes=len(analysis.nodes) - 1,
            source_sha256=digest(analysis.data), diagnostics=analysis.diagnostics,
            supported_records=len(analysis.records), simple_typedef_edges=len(analysis.edges))
        report['timing_seconds'].update(analysis.timings)
        if progress:
            progress(dict(event='ast_ready', ast_status='OK', main_file_nodes=report['main_file_nodes'],
                          timing_seconds=analysis.timings))
        phase = time.perf_counter()
        inventory = structural_inventory(analysis)
        report['inventory'] = inventory
        report['timing_seconds']['inventory_seconds'] = time.perf_counter() - phase
        phase = time.perf_counter()
        failures, candidates = [], []
        for node in analysis.nodes:
            if node.kind != K.CALL_EXPR:
                continue
            try:
                candidates.append(candidate_summary(analysis, analysis.candidate(node)))
            except NotApplicable as exc:
                failures.append(dict(call=analysis.node_anchor(node), reason=str(exc)))
        report['timing_seconds']['candidate_seconds'] = time.perf_counter() - phase
        reasons = sorted({failure['reason'] for failure in failures}) or ([] if candidates else ['NO_CALL_EXPRESSION'])
        report.update(status='APPLICABLE' if candidates else 'NOT_APPLICABLE', candidates=candidates,
                      candidate_count=len(candidates), reasons=reasons, rejected_calls=failures,
                      rejected_reason_call_counts=dict(Counter(f['reason'] for f in failures)),
                      candidate_count_unit='unique call sites; first safe edge/replacement per site',
                      reason_categories=reason_categories(inventory, reasons, len(analysis.records)) if not candidates else [])
    except ASTParseError as exc:
        report.update(status='PARSE_FAILED', ast_status='ERROR_DIAGNOSTICS',
                      reasons=['P0_AST_ERRORS'], diagnostics=exc.diagnostics)
    except c.TranslationUnitLoadError as exc:
        report.update(status='PARSE_FAILED', ast_status='LOAD_FAILED', reasons=[str(exc)])
    except Exception as exc:
        report.update(status='SCAN_FAILED', reasons=[f'{type(exc).__name__}: {exc}'])
    report['elapsed_seconds'] = time.perf_counter() - started
    return report
