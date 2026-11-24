"""Safety and replay checks for the one stage 2F layout extension."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from edgm_second.environment import PROJECT,inspect_environment,load_config
from edgm_second.bitfield_validation import collect_integer_profile,observe_errors
from edgm_second.seeds import validate_seed
from edgm_second.source_edit import digest
from edgm_second.seed_enrichment import Context,K
from edgm_second.semantic_profile import usr
from edgm_second.native_bitfield_candidates import scan_native
from edgm_second.scoped_layout import scan_native_scoped_layout,LayoutAnalysis,seal,verify_layout_proof
from edgm_second.native_contexts import extract_contexts
from edgm_second.mr_member_bitfield_addressability import scan_candidates,build_mutation,verify_bitfield_witness

BASE='''struct Target { int x; };
int compute(int input) {
    struct Target object={0};
    int unrelated[3]={0};
    (void)sizeof(unrelated);
    (void)sizeof(int);
    (void)_Alignof(int);
    int *p=&object.x;
    { int *q=&object.x; return input + *p + *q; }
}
int main(void) { return compute(0); }
'''


class NativeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env=inspect_environment(load_config());cls.runtime=cls.env['runtime']['path']
        cls.profile=collect_integer_profile(cls.env)
        if not cls.env['ready']:raise RuntimeError('required toolchain unavailable')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=PROJECT);self.addCleanup(self.tmp.cleanup)
        self.p0=Path(self.tmp.name)/'P0.c';self.p0.write_text(BASE);self.p1=self.p0.with_name('P1.c')

    def scan(self,text=None):
        if text is not None:self.p0.write_text(text)
        return scan_candidates(self.p0,self.runtime,self.profile,native_scanner=scan_native_scoped_layout)

    def build(self,admission=None):
        return build_mutation(self.p0,self.p1,self.runtime,self.profile,admission or validate_seed(self.p0,self.env),
            dict(source_kind='Native',original_path=str(self.p0),original_sha256=digest(self.p0.read_bytes())),
            native_scanner=scan_native_scoped_layout)

    def proof(self):
        ctx=Context(self.p0,self.runtime);f=next(i for i,n in enumerate(ctx.nodes) if n.kind==K.FIELD_DECL and n.semantic_parent.spelling=='Target')
        return LayoutAnalysis(ctx).prove(f)

    def test_unrelated_scalar_array_size_and_builtin_align_positive(self):
        self.assertEqual(scan_candidates(self.p0,self.runtime,self.profile)['candidate_count'],0)
        r=self.scan();self.assertEqual(r['candidate_count'],1,r)
        self.assertEqual(len(r['candidates'][0]['address_anchors']),2)
        _,p1,w=self.build();self.assertIn(b'signed int x : 32',p1)
        self.assertTrue(verify_bitfield_witness(w,self.p0.read_bytes(),p1,self.p0.read_bytes()))
        self.p1.write_bytes(p1)
        for check in validate_seed(self.p1,self.env)['checks'].values():
            self.assertEqual(observe_errors(check,w)['status'],'TARGET_ERRORS_OBSERVED',check['stderr'])

    def test_oracle_without_compiler_or_p1_ast(self):
        admission=validate_seed(self.p0,self.env)
        def guard(path,runtime):
            self.assertEqual(Path(path),self.p0);return Context(path,runtime)
        with patch('subprocess.run',side_effect=AssertionError('forbidden')), \
             patch('edgm_second.mr_member_bitfield_addressability.Context',side_effect=guard), \
             patch('edgm_second.scoped_layout.Context',side_effect=guard):
            _,p1,w=self.build(admission)
            self.assertTrue(verify_bitfield_witness(w,self.p0.read_bytes(),p1,self.p0.read_bytes()))
        self.assertFalse(self.p1.exists())

    def test_direct_record_and_member_size_rejected(self):
        for expression in ['sizeof(object)','sizeof(struct Target)','sizeof(object.x)','_Alignof(struct Target)']:
            with self.subTest(expression=expression):
                self.p0.write_text(BASE.replace('sizeof(unrelated)',expression))
                self.assertEqual(self.scan()['candidate_count'],0)
                self.assertEqual(self.proof()['status'],'UNPROVEN')

    def test_typedef_pointer_array_and_nested_type_dependency_rejected(self):
        cases=[('typedef struct Target Alias;','sizeof(Alias)'),
               ('struct Target *pointer;','sizeof(pointer)'),
               ('struct Target array[3];','sizeof(array)'),
               ('struct Outer { struct Target inner; };','sizeof(struct Outer)'),
               ('struct Target (*fp)(void);','sizeof(fp)')]
        for declaration,expression in cases:
            with self.subTest(declaration=declaration):
                self.p0.write_text(BASE.replace('int compute',declaration+'\nint compute').replace('sizeof(unrelated)',expression))
                self.assertEqual(self.scan()['candidate_count'],0)
                self.assertIn('LAYOUT_REACHES_TARGET_RECORD',self.proof()['reasons'])

    def test_same_spelling_different_field_identity_is_unrelated(self):
        text=BASE.replace('int compute','struct Other {int x;};\nint compute').replace('int unrelated[3]={0};','struct Other other={0};').replace('sizeof(unrelated)','sizeof(other.x)')
        self.assertEqual(self.scan(text)['candidate_count'],1)

    def test_unrelated_typedef_scalar_expression_supported(self):
        text=BASE.replace('int compute','typedef unsigned Other;\nint compute').replace('int unrelated[3]={0};','Other unrelated=0;')
        self.assertEqual(self.scan(text)['candidate_count'],1)

    def test_macro_unknown_pragma_and_vla_rejected(self):
        cases=['#define SZ(x) sizeof(x)\n'+BASE.replace('sizeof(unrelated)','SZ(unrelated)'),
               '#pragma pack(1)\n'+BASE,
               BASE.replace('int unrelated[3]={0};','int unrelated[input+1];'),
               BASE.replace('sizeof(unrelated)','sizeof(int[3])')]
        for text in cases:
            with self.subTest(text=text):self.assertEqual(self.scan(text)['candidate_count'],0)

    def test_other_closure_rules_not_relaxed_and_no_p1(self):
        for text in [BASE.replace('{0};','{2};',1),BASE.replace('struct Target object','static struct Target object'),
                     BASE.replace('int *p=', 'struct Target *escape=&object; int *p=')]:
            with self.subTest(text=text):
                self.p0.write_text(text);scan,p1,w=self.build()
                self.assertEqual(scan['candidate_count'],0);self.assertIsNone(p1);self.assertIsNone(w)
                self.assertFalse(self.p1.exists())

    def test_exact_utf8_crlf_source_transport(self):
        self.p0.write_bytes(('/* 字节位置 */\r\n'+BASE.replace('\n','\r\n')).encode())
        _,p1,w=self.build();self.assertTrue(verify_bitfield_witness(w,self.p0.read_bytes(),p1,self.p0.read_bytes()))

    def test_tamper_identity_position_or_impact_evidence_rejected(self):
        _,p1,w=self.build();p0=self.p0.read_bytes()
        changes=[lambda x:x['isolation']['native_proof']['layout_independence'].update(record_usr='wrong'),
                 lambda x:x['isolation']['native_proof'].update(impact_closed=False),
                 lambda x:x['isolation']['native_proof']['layout_independence']['operations'][0]['anchor'].update(column=999),
                 lambda x:x['affected_uses'][0]['address']['p1'].update(column=999),
                 lambda x:x['isolation']['native_proof']['layout_independence']['operations'][0]['graphs'][0].update(related=True)]
        for change in changes:
            value=copy.deepcopy(w.to_dict());change(value)
            proof=value['isolation']['native_proof']['layout_independence'];proof['seal']=seal(proof)
            with self.assertRaises(ValueError):verify_bitfield_witness(value,p0,p1,p0)

    def test_context_cross_scope_and_statement_has_identity_path(self):
        formal=self.scan();discovery=scan_native_scoped_layout(self.p0,self.runtime)
        fields=extract_contexts(self.p0,self.runtime,formal,discovery)
        self.assertEqual(len(fields),1);field=fields[0]
        self.assertTrue(field['cross_statement']);self.assertTrue(field['cross_scope'])
        self.assertFalse(field['cross_function']);self.assertTrue(field['impact_closed'])
        self.assertEqual(field['target_address_count'],2)
        for edge in field['relations']:
            self.assertEqual(edge['dependency_path'][1]['usr'],field['target_declaration']['usr'])

    def test_shared_type_across_functions_not_runtime_flow_claim(self):
        text=BASE+'\nint other(void) { struct Target second={0};int *p=&second.x;return *p; }\n'
        formal=self.scan(text);fields=extract_contexts(self.p0,self.runtime,formal,scan_native_scoped_layout(self.p0,self.runtime))
        self.assertTrue(fields[0]['cross_function']);self.assertEqual(fields[0]['runtime_cross_function_value_flow'],'NOT_PROVEN')
        self.assertEqual(len(fields[0]['cross_function_evidence']['functions']),2)

    def test_parse_failure_is_not_zero(self):
        r=self.scan('int main( {');self.assertEqual(r['status'],'PARSE_FAILED');self.assertIsNone(r['candidate_count'])

    def test_default_2e_scanner_and_old_evidence_remain(self):
        r=scan_candidates(self.p0,self.runtime,self.profile)
        self.assertEqual(r['candidate_count'],0)
        self.assertEqual(scan_native(self.p0,self.runtime)['candidate_count'],0)
        proof=self.proof();self.assertEqual(proof['status'],'INDEPENDENT')
        self.assertTrue(verify_layout_proof(proof,self.p0.read_bytes(),proof['field_usr'],proof['record_usr']))


if __name__=='__main__':unittest.main()
