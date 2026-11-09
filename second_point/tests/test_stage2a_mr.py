import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from clang import cindex as c
from edgm_second.environment import PROJECT, inspect_environment, load_config
from edgm_second.seeds import validate_seed
from edgm_second.ast_extract import parse_translation_unit
from edgm_second.constraint_witness import MR_ID, verify_constraint_witness
from edgm_second.mr_typedef_indirect_struct_arg import (
    Analysis, build_mutation, scan_candidates, write_mutation, NotApplicable, record_id)
from edgm_second.source_edit import digest

FIXTURE = PROJECT / 'examples/stage2a/P0.c'


class Stage2ATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config()
        cls.env = inspect_environment(cls.config)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='stage2a-test-', dir=PROJECT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.p0, self.p1 = self.root / 'input.c', self.root / 'output.c'
        self.p0.write_bytes(FIXTURE.read_bytes())

    def admit(self):
        receipt = validate_seed(self.p0, self.env)
        self.assertEqual(receipt['status'], 'valid', receipt)
        return receipt

    def build(self):
        return build_mutation(self.p0, self.p1, self.config['runtime'], self.admit())

    def test_p0_accepted_and_p1_real_error_both_compilers(self):
        original = self.p0.read_bytes()
        result = self.build()
        self.assertEqual(result['status'], 'APPLIED', result['reasons'])
        self.p1.write_bytes(result['p1'])
        self.assertTrue(verify_constraint_witness(result['witness'], original, result['p1']))
        checks = validate_seed(self.p1, self.env)
        self.assertEqual(checks['status'], 'rejected')
        for check in checks['checks'].values():
            self.assertEqual(check['command'][1:4], ['-std=c11', '-fsyntax-only', '-w'])
            self.assertGreater(check['returncode'], 0)
            errors = re.findall(r'^.*?:\d+:\d+: (?:fatal )?error:.*$', check['stderr'], re.M)
            self.assertEqual(len(errors), 1, check['stderr'])
            self.assertRegex(errors[0], r'incompatible (?:type|argument)')
        self.assertEqual(self.p0.read_bytes(), original)

    def test_oracle_generated_before_any_p1_parse_or_compilation(self):
        receipt = self.admit()
        real_parse = parse_translation_unit
        def only_p0(path, runtime):
            self.assertEqual(Path(path).resolve(), self.p0.resolve())
            return real_parse(path, runtime)
        with (patch.object(subprocess, 'run', side_effect=AssertionError('compiler forbidden')),
              patch('edgm_second.mr_typedef_indirect_struct_arg.parse_translation_unit', side_effect=only_p0)):
            result = build_mutation(self.p0, self.p1, self.config['runtime'], receipt)
            self.assertTrue(verify_constraint_witness(result['witness'], self.p0.read_bytes(), result['p1']))
        self.assertFalse(self.p1.exists())
        self.assertEqual(result['witness'].mr_id, MR_ID)
        self.assertNotIn('diagnostics', result['witness'].to_dict())

    def test_typedef_edge_changes_actual_type_not_parameter(self):
        result = self.build()
        witness = result['witness'].to_dict()
        self.p1.write_bytes(result['p1'])
        # P1 AST is inspected only AFTER the witness exists, never used for inference.
        tu = parse_translation_unit(self.p1, self.config['runtime'])
        nodes = list(tu.cursor.walk_preorder())
        var = next(n for n in nodes if n.kind == c.CursorKind.VAR_DECL and n.spelling == 'value')
        field = next(n for n in nodes if n.kind == c.CursorKind.FIELD_DECL and n.spelling == 'invoke')
        param = list(field.type.get_canonical().get_pointee().argument_types())[0]
        self.assertEqual(record_id(var.type), witness['actual_argument_type']['p1']['declaration_usr'])
        self.assertEqual(record_id(param), witness['expected_parameter_type']['declaration_usr'])
        self.assertNotEqual(record_id(var.type), record_id(param))
        self.assertEqual(witness['expected_parameter_type']['fields'], witness['actual_argument_type']['p1']['fields'])
        self.assertNotEqual(witness['expected_parameter_type']['declaration_usr'],
                            witness['actual_argument_type']['p1']['declaration_usr'])

    def test_byte_map_precise_anchors_and_source_only_one_edit(self):
        result = self.build()
        w = result['witness'].to_dict()
        data = self.p0.read_bytes()
        e = w['source_map']['edit']
        self.assertEqual(result['p1'], data[:e['start']] + e['after'].encode() + data[e['end']:])
        self.assertNotEqual(data, result['p1'])
        self.assertTrue(verify_constraint_witness(w, data, result['p1']))
        for name, versions in w['source_anchors'].items():
            a = versions['p1']
            self.assertEqual(a['file'], str(self.p1.resolve()))
            self.assertIn('origin_key', a['node_identity'])
            self.assertFalse(a['node_identity']['p1_ast_observed'])
        self.assertEqual(w['source_anchors']['argument']['p1']['line'], 13)
        self.assertEqual(w['source_anchors']['argument']['p1']['column'], 28)
        bad = copy.deepcopy(w)
        bad['source_anchors']['argument']['p1']['column'] += 1
        with self.assertRaises(ValueError):
            verify_constraint_witness(bad, data, result['p1'])

    def test_names_not_fixed_and_length_delta_not_required(self):
        text = self.p0.read_text()
        for old, new in [('Payload','Alpha'), ('DifferentPayload','Beta'), ('Stable','Root'),
                         ('ArgumentBase','Bridge'), ('Argument','Final'), ('Dispatch','Box'),
                         ('invoke','apply'), ('value','item'), ('consume','handler')]:
            text = re.sub(r'\b' + old + r'\b', new, text)
        self.p0.write_text(text)
        self.assertEqual(self.build()['status'], 'APPLIED')

    def assert_skipped(self, text):
        self.p0.write_text(text)
        result = scan_candidates(self.p0, self.config['runtime'])
        self.assertEqual(result['status'], 'NOT_APPLICABLE', result)
        self.assertTrue(result['reasons'])
        self.assertFalse(self.p1.exists())

    def test_no_struct_member_call_skips(self):
        self.assert_skipped('int main(void){return 0;}\n')

    def test_extra_alias_usage_skips(self):
        text = FIXTURE.read_text().replace('    Argument value', '    Argument other = {0};\n    Argument value')
        self.assert_skipped(text)

    def test_other_variable_use_skips(self):
        self.assert_skipped(FIXTURE.read_text().replace('return dispatch.invoke(value);',
                           '(void)sizeof value; return dispatch.invoke(value);'))

    def test_nonzero_or_member_designated_initializer_skips(self):
        for initializer in ('{ 1 }', '{ .value = 0 }'):
            with self.subTest(initializer=initializer):
                self.assert_skipped(FIXTURE.read_text().replace('value = { 0 }', 'value = ' + initializer))

    def test_incomplete_or_same_record_alternative_skips(self):
        for declaration in ('struct DifferentPayload;', 'typedef struct Payload DifferentPayload;'):
            with self.subTest(declaration=declaration):
                self.assert_skipped(FIXTURE.read_text().replace('struct DifferentPayload { int value; };', declaration))

    def test_macro_and_variadic_call_skips(self):
        self.assert_skipped('#define UNUSED 0\n' + FIXTURE.read_text())
        self.assert_skipped(FIXTURE.read_text().replace('(*Callback)(Stable)', '(*Callback)(Stable, ...)')
                            .replace('consume(Stable input)', 'consume(Stable input, ...)'))

    def test_shared_parameter_alias_cannot_be_mutated(self):
        self.assert_skipped(FIXTURE.read_text().replace('(*Callback)(Stable)', '(*Callback)(Argument)')
                            .replace('consume(Stable input)', 'consume(Argument input)'))

    def test_unicode_comment_inside_typedef_and_crlf(self):
        text = FIXTURE.read_text().replace('typedef Stable ArgumentBase',
                                           'typedef /* 同行中文 */ Stable ArgumentBase')
        self.p0.write_bytes(text.replace('\n', '\r\n').encode())
        result = self.build()
        self.assertTrue(verify_constraint_witness(result['witness'], self.p0.read_bytes(), result['p1']))
        self.assertIn('/* 同行中文 */'.encode(), result['p1'])
        self.assertEqual(result['p1'].count(b'\r\n'), self.p0.read_bytes().count(b'\r\n'))
        self.p1.write_bytes(result['p1'])
        self.assertEqual(validate_seed(self.p1, self.env)['status'], 'rejected')

    def test_shadowed_replacement_tag_skips(self):
        self.assert_skipped(FIXTURE.read_text().replace('int main(void) {',
            'int main(void) {\n    struct DifferentPayload { int value; };'))

    def test_p0_invalid_ast_is_parse_failed(self):
        self.p0.write_text('int main(void) { return missing; }\n')
        result = scan_candidates(self.p0, self.config['runtime'])
        self.assertEqual(result['status'], 'PARSE_FAILED')
        self.assertIsNone(result['candidate_count'])

    def test_write_not_applicable_creates_no_artifacts(self):
        self.p0.write_text('int main(void){return 0;}\n')
        result = write_mutation(self.p0, self.p1, self.root / 'w.json', self.config['runtime'], self.admit())
        self.assertEqual(result['status'], 'NOT_APPLICABLE')
        self.assertFalse(self.p1.exists())
        self.assertFalse((self.root / 'w.json').exists())

    def test_stale_admission_and_overwrites_refused(self):
        receipt = self.admit()
        self.p0.write_bytes(self.p0.read_bytes() + b'\n')
        with self.assertRaises(NotApplicable):
            build_mutation(self.p0, self.p1, self.config['runtime'], receipt)
        receipt = self.admit()
        self.p1.write_bytes(b'owned')
        with self.assertRaises(NotApplicable):
            write_mutation(self.p0, self.p1, self.root / 'w.json', self.config['runtime'], receipt)
        self.assertEqual(self.p1.read_bytes(), b'owned')

    def test_write_witness_and_p1_without_mutating_input(self):
        receipt = self.admit()
        original = self.p0.read_bytes()
        witness_path = self.root / 'w.json'
        result = write_mutation(self.p0, self.p1, witness_path, self.config['runtime'], receipt)
        self.assertEqual(self.p0.read_bytes(), original)
        self.assertEqual(self.p1.read_bytes(), result['p1'])
        self.assertTrue(verify_constraint_witness(json.loads(witness_path.read_text()), original, self.p1.read_bytes()))


if __name__ == '__main__':
    unittest.main()
