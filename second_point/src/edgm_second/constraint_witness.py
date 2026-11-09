"""Serializable proof data for one MR, independent of observed diagnostics."""
from dataclasses import asdict, dataclass
import json

MR_ID = 'MR_TYPEDEF_INDIRECT_STRUCT_ARG'
STANDARD = 'https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf'


@dataclass(frozen=True)
class BitfieldConstraintWitness:
    """Separate schema: no fictitious call/parameter facts from the typedef MR."""
    mr_id: str
    seed_id: str
    constraint_id: str
    provenance: dict
    changed_declaration: dict
    width_proof: dict
    affected_uses: list
    source_map: dict
    isolation: dict
    expected_error: dict
    schema_version: str = 'stage2e-bitfield-v1'

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + '\n'


@dataclass(frozen=True)
class ConstraintWitness:
    mr_id: str
    seed_id: str
    constraint_id: str
    preconditions: list
    changed_declaration: dict
    expected_parameter_type: dict
    actual_argument_type: dict
    call_expression: dict
    source_anchors: dict
    expected_error: dict
    source_map: dict
    dependency_graph: dict
    isolation: dict
    provenance: dict
    schema_version: str = 'stage2a-witness-v1'

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + '\n'


def verify_constraint_witness(witness, p0, p1):
    """Replay this MR's symbolic proof and byte map, without clang or subprocess.

    This checks internal proof consistency; it is not a general C11 verifier,
    nor a comparator of observed compiler diagnostics.
    """
    from .source_edit import ByteEdit, digest
    w = witness.to_dict() if isinstance(witness, ConstraintWitness) else witness
    if w['mr_id'] != MR_ID or w['constraint_id'] != 'C11:6.5.2.2p2':
        raise ValueError('unsupported witness')
    mapping = w['source_map']
    edit = mapping['edit']
    operation = ByteEdit(edit['start'], edit['end'], edit['before'].encode(), edit['after'].encode())
    if operation.apply(p0) != p1 or p0 == p1:
        raise ValueError('invalid source transformation')
    if digest(p0) != mapping['p0_sha256'] or digest(p1) != mapping['p1_sha256'] or digest(p0) != w['seed_id']:
        raise ValueError('source digest mismatch')
    edges = dict(w['dependency_graph']['edges_p0'])
    changed = w['dependency_graph']['changed_edge']
    declaration = w['changed_declaration']
    if (declaration['usr'], declaration['original_dependency_usr'], declaration['new_dependency_usr']) != (
            changed['source'], changed['old_target'], changed['new_target']):
        raise ValueError('changed declaration disagrees with dependency graph')
    if declaration['before'] != w['source_anchors']['changed_declaration']['p0']['source_text'] or \
            declaration['after'] != w['source_anchors']['changed_declaration']['p1']['source_text']:
        raise ValueError('changed declaration disagrees with source anchors')
    if mapping['byte_delta'] != len(operation.after) - len(operation.before):
        raise ValueError('invalid source map byte delta')
    required_preconditions = {
        'P0_DUAL_COMPILER_ACCEPTED', 'P0_AST_NO_ERRORS', 'TWO_COMPLETE_DISTINCT_FILE_SCOPE_STRUCTS',
        'TYPEDEF_CHAIN_IDENTITY_RESOLVED', 'PROTOTYPED_MEMBER_FUNCTION_POINTER_CALL',
        'P0_PARAMETER_ARGUMENT_SAME_DECLARATION_IDENTITY', 'ONE_ISOLATED_ARGUMENT_VARIABLE',
        'ZERO_INITIALIZER_VALID_FOR_BOTH_RECORDS', 'AFFECTED_TYPE_REFERENCES_CLOSED',
        'NO_MACROS_ATTRIBUTES_OR_TAG_CAPTURE'}
    if {p['id'] for p in w['preconditions']} != required_preconditions:
        raise ValueError('missing or unexpected preconditions')
    error = w['expected_error']
    if error['category'] != 'incompatible_struct_argument' or error['required_severities'] != ['error', 'fatal']:
        raise ValueError('invalid target constraint or severities')
    if edges.get(changed['source']) != changed['old_target']:
        raise ValueError('invalid old edge')

    def root(graph, start):
        seen = set()
        while start in graph:
            if start in seen:
                raise ValueError('cyclic proof graph')
            seen.add(start)
            start = graph[start]
        return start

    call = w['call_expression']
    head = call['p0_argument_chain'][0]
    old = root(edges, head)
    edges[changed['source']] = changed['new_target']
    new = root(edges, head)
    expected = w['expected_parameter_type']['declaration_usr']
    if not (old == expected == w['actual_argument_type']['p0']['declaration_usr']
            and new == w['actual_argument_type']['p1']['declaration_usr'] and old != new):
        raise ValueError('type-identity proof failed')
    for record in (w['expected_parameter_type'], w['actual_argument_type']['p1']):
        if record['kind'] != 'struct' or record['scope'] != 'translation_unit' or not record['complete']:
            raise ValueError('unsupported record identity')
    if not all(p['satisfied'] is True for p in w['preconditions']):
        raise ValueError('unsatisfied precondition')
    if w['expected_error']['warning_satisfies'] or w['expected_error']['exact_caret_mandated_by_standard']:
        raise ValueError('invalid expectation policy')
    for versions in w['source_anchors'].values():
        for version, data in (('p0', p0), ('p1', p1)):
            a = versions[version]
            if not (0 <= a['start_offset'] <= a['offset'] <= a['end_offset'] <= len(data)):
                raise ValueError('anchor out of bounds')
            if a['file'] != mapping[version + '_file']:
                raise ValueError('anchor file disagrees with source map')
            prefix = data[:a['offset']]
            if (a['line'], a['column']) != (prefix.count(b'\n') + 1, len(prefix.rsplit(b'\n', 1)[-1]) + 1):
                raise ValueError('anchor coordinates mismatch')
            if data[a['start_offset']:a['end_offset']].decode() != a['source_text']:
                raise ValueError('anchor text mismatch')
        before, after = versions['p0'], versions['p1']
        for field in ('offset', 'start_offset', 'end_offset'):
            if operation.map_offset(before[field]) != after[field]:
                raise ValueError('anchor disagrees with byte edit transport')
    return True
