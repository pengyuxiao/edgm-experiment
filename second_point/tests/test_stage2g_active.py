import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from edgm_second.environment import PROJECT,inspect_environment,load_config,run_process
from edgm_second.bitfield_validation import collect_integer_profile,observe_errors
from edgm_second.active_validation import observe_typedef
from edgm_second.active_enrichment import enumerate_positions,plan_enrichment,validate_route,verify_enrichment,FAMILIES
from edgm_second.active_oracle import scan_prepared,build_oracle,verify_active_oracle
from edgm_second.controlled_bitfield import scan_controlled_bitfields
from edgm_second.seeds import validate_seed
from edgm_second.mr_typedef_indirect_struct_arg import build_mutation as old_typedef

SOURCE='''int compute(int input) {
    if (input == 3) return input ^ 7;
    return input + 4;
}
int main(void) { return compute(1); }
'''


class ActiveTests(unittest.TestCase):
    def test_pool_deduplicates_same_seed_context_before_feedback(self):
        from edgm_second.active_run import freeze_pool
        with tempfile.TemporaryDirectory(dir=PROJECT) as tmp:
            root=Path(tmp)
            base=dict(seed_id='s',s_sha256='s-hash',mr_id='MR',semantic_context_signature='context',
                enrichment_family='split_pointer',semantic_context=dict(function_role='ordinary',input_type='INT',original_operator='+'))
            actions=[dict(base,candidate_id='first'),dict(base,candidate_id='renamed'),dict(base,candidate_id='other-seed',seed_id='t',s_sha256='t-hash')]
            (root/'candidate_pool.json').write_text(json.dumps(dict(valid_candidates=actions,all_attempts=actions)))
            (root/'enrichment_summary.json').write_text('{}')
            (root/'semantic_contexts.json').write_text(json.dumps(dict(signatures={'context':{}},candidate_to_signature={})))
            freeze_pool(root);freeze_pool(root)
            pool=json.loads((root/'candidate_pool.json').read_text())
            self.assertEqual([a['candidate_id'] for a in pool['valid_candidates']],['first','other-seed'])
            self.assertEqual([a['candidate_id'] for a in pool['excluded_context_duplicates']],['renamed'])
            contexts=json.loads((root/'semantic_contexts.json').read_text())
            self.assertEqual(set(contexts['candidate_to_signature']),{'first','other-seed'})
            self.assertFalse((root/'experiment').exists())

    @classmethod
    def setUpClass(cls):
        cls.env=inspect_environment(load_config());cls.runtime=cls.env['runtime']['path'];cls.profile=collect_integer_profile(cls.env)
        if not cls.env['ready']:raise RuntimeError('target compilers required')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=PROJECT);self.addCleanup(self.tmp.cleanup)
        self.s=Path(self.tmp.name)/'S.c';self.s.write_text(SOURCE)

    def prepare(self,family,index=0):
        ctx,positions,_=enumerate_positions(self.s,self.runtime)
        eligible=[p for p in positions if (family!='nested_two_addresses' or p['nested_supported']) and (family!='typedef_callback' or p['kind']=='INT')]
        record,data=plan_enrichment(ctx,eligible[index],family)
        folder=Path(self.tmp.name)/f'{family}_{index}';folder.mkdir();p0=folder/'P0.c';p0.write_bytes(data)
        return record,p0,folder/'P1.c'

    def test_three_families_real_p0_p1_oracle_and_exact_errors(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                r,p0,p1=self.prepare(family);admission=validate_seed(p0,self.env);self.assertEqual(admission['status'],'valid')
                self.assertEqual(scan_prepared(p0,r,self.runtime,self.profile)['status'],'APPLICABLE')
                oracle,data=build_oracle(p0,p1,r,self.runtime,self.profile,admission)
                self.assertTrue(verify_active_oracle(oracle,self.s.read_bytes(),p0.read_bytes(),data));p1.write_bytes(data)
                checks=validate_seed(p1,self.env)
                for process in checks['checks'].values():
                    observed=observe_typedef(process,oracle['formal_witness']) if family=='typedef_callback' else observe_errors(process,oracle['formal_witness'])
                    self.assertEqual(observed['status'],'TARGET_ERRORS_OBSERVED',process['stderr'])
                    self.assertEqual(len(observed['errors']),2 if family=='nested_two_addresses' else 1)

    def test_unsigned_full_width_and_extreme_value_control(self):
        self.s.write_text('unsigned compute(unsigned input){return input ^ 3u;} int main(void){return compute(0u)!=3u;}')
        for family in FAMILIES[:2]:
            r,p0,p1=self.prepare(family);o,data=build_oracle(p0,p1,r,self.runtime,self.profile,validate_seed(p0,self.env))
            self.assertEqual(o['formal_witness']['width_proof']['width'],32)
            self.assertEqual(o['formal_witness']['width_proof']['base_type'],'unsigned int')

    def test_multiple_original_positions_not_first_only(self):
        ctx,positions,_=enumerate_positions(self.s,self.runtime);self.assertGreaterEqual(len(positions),3)
        a,first,_=self.prepare('split_pointer',0);b,second,_=self.prepare('split_pointer',1)
        self.assertNotEqual(a['position']['position_id'],b['position']['position_id']);self.assertNotEqual(first.read_bytes(),second.read_bytes())

    def test_no_compilation_or_p1_parse_needed_for_oracles(self):
        for family in FAMILIES:
            r,p0,p1=self.prepare(family);admission=validate_seed(p0,self.env)
            from edgm_second.ast_extract import parse_translation_unit
            def guard(path,runtime,*args,**kwargs):
                self.assertEqual(Path(path).resolve(),p0.resolve());return parse_translation_unit(path,runtime,*args,**kwargs)
            with patch('subprocess.run',side_effect=AssertionError('compiler forbidden')), \
                 patch('edgm_second.seed_enrichment.parse_translation_unit',side_effect=guard), \
                 patch('edgm_second.mr_typedef_indirect_struct_arg.parse_translation_unit',side_effect=guard):
                o,data=build_oracle(p0,p1,r,self.runtime,self.profile,admission)
                self.assertTrue(verify_active_oracle(o,self.s.read_bytes(),p0.read_bytes(),data))
            self.assertFalse(p1.exists())

    def test_mutable_volatile_atomic_address_taken_and_asm_refused(self):
        for text in ['int f(int x){x++;return x+1;}', 'int f(volatile int x){return x+1;}',
                     'int f(_Atomic int x){return x+1;}','int f(int x){int *p=&x;return x+1;}',
                     'int f(int x){__asm__("nop");return x+1;}']:
            self.s.write_text(text)
            self.assertFalse(enumerate_positions(self.s,self.runtime)[1],text)

    def test_uninitialized_local_is_not_selected(self):
        self.s.write_text('int f(void){int x;return x+1;}')
        self.assertFalse(enumerate_positions(self.s,self.runtime)[1])

    def test_macro_read_unevaluated_context_and_comma_rejected(self):
        for text in ['#define PLUS(x) ((x)+1)\nint f(int x){return PLUS(x);}',
                     'int f(int x){return sizeof(x+1);}',
                     'int f(int x){return _Generic((x+1),int:2);}',
                     'int f(int x){return (x,3);}']:
            self.s.write_text(text);self.assertFalse(enumerate_positions(self.s,self.runtime)[1],text)

    def test_return_and_verified_function_argument_contexts(self):
        self.s.write_text('int identity(int x){return x;} int f(int y){return identity(y);}')
        _,positions,_=enumerate_positions(self.s,self.runtime)
        self.assertIn('return_value',[p['operator'] for p in positions]);self.assertIn('function_argument',[p['operator'] for p in positions])
        r,p0,_=self.prepare('typedef_callback');self.assertTrue(validate_route(r,self.s.read_bytes(),p0,self.runtime)['cross_function_value_route'])

    def test_names_and_whitespace_not_new_structure_family(self):
        ctx,positions,_=enumerate_positions(self.s,self.runtime);r,_=plan_enrichment(ctx,positions[0],'split_pointer')
        self.s.write_text(SOURCE.replace('input','renamed').replace('compute','other_name'))
        ctx,positions,_=enumerate_positions(self.s,self.runtime);r2,_=plan_enrichment(ctx,positions[0],'split_pointer')
        self.assertEqual(r['semantic_context_signature'],r2['semantic_context_signature'])

    def test_callback_default_old_behavior_preserved_and_includes_supported_locally(self):
        self.s.write_text('#include <stdint.h>\n'+SOURCE)
        r,p0,p1=self.prepare('typedef_callback');admission=validate_seed(p0,self.env)
        self.assertEqual(old_typedef(p0,p1,self.runtime,admission)['status'],'NOT_APPLICABLE')
        self.assertEqual(scan_prepared(p0,r,self.runtime,self.profile)['status'],'APPLICABLE')

    def test_unsigned_callback_and_illegal_nested_wrapper_refused(self):
        self.s.write_text('unsigned f(unsigned x){return x+1u;}')
        ctx,pos,_=enumerate_positions(self.s,self.runtime)
        with self.assertRaises(ValueError):plan_enrichment(ctx,pos[0],'typedef_callback')
        self.s.write_text('int f(int x){ label: if(x==1)return x+2; return 0; }')
        ctx,pos,_=enumerate_positions(self.s,self.runtime)
        p=next(p for p in pos if p['operator']=='==')
        self.assertFalse(p['nested_supported'])

    def test_controlled_field_escaping_or_extra_use_refused(self):
        r,p0,_=self.prepare('split_pointer');text=p0.read_text();name=r['names']['left']
        p0.write_text(text.replace('if (',f'(void)sizeof({name}); if (',1))
        self.assertEqual(scan_controlled_bitfields(p0,self.runtime)['candidate_count'],0)

    def test_tamper_maps_identity_position_and_closure_refused(self):
        r,p0,p1=self.prepare('nested_two_addresses');o,data=build_oracle(p0,p1,r,self.runtime,self.profile,validate_seed(p0,self.env))
        changes=[lambda x:x['active_enrichment']['source_map'][0].update(p0=[1,2]),
                 lambda x:x['value_route']['object'].update(usr='wrong'),
                 lambda x:x['value_route']['original_operation'].update(column=999),
                 lambda x:x['formal_witness']['isolation']['native_proof']['controlled_flow'].update(type_and_value_uses_closed=False)]
        for change in changes:
            v=copy.deepcopy(o);change(v)
            with self.assertRaises(ValueError):verify_active_oracle(v,self.s.read_bytes(),p0.read_bytes(),data)

    def test_controlled_dynamic_equivalence_extremes_both_compilers(self):
        self.s.write_text('''#include <limits.h>
int compute(int input){return input ^ 3;}
int main(void){int a[]={INT_MIN,INT_MAX,-1,0,1};for(int i=0;i<5;i++)if(compute(a[i])!=(a[i]^3))return 1;return 0;}
''')
        for family in FAMILIES:
            r,p0,_=self.prepare(family)
            for name,c in self.env['compilers'].items():
                binary=p0.parent/name
                build=run_process([c['path'],'-std=c11','-w',str(p0),'-o',str(binary)],20)
                self.assertEqual(build['returncode'],0,build['stderr']);self.assertEqual(run_process([str(binary)],5)['returncode'],0)

    def test_local_diagnostic_unknown_position_unlocated_error_and_ice_fail_closed(self):
        from edgm_second.active_validation import observe_active
        r,p0,p1=self.prepare('typedef_callback')
        o,data=build_oracle(p0,p1,r,self.runtime,self.profile,validate_seed(p0,self.env))
        formal=o['formal_witness'];a=formal['source_anchors']['argument']['p1']
        line=f'{a["file"]}:{a["line"]}:{a["column"]}: error: incompatible type for argument 1 of callback\n'
        def process(stderr,status='nonzero'):return dict(returncode=1,status=status,stderr=stderr)
        self.assertEqual(observe_active(process(line),formal)['status'],'TARGET_ERRORS_OBSERVED')
        self.assertEqual(observe_active(process(line+'cc1: error: unrelated driver error\n'),formal)['status'],'UNPARSED_ERROR_REVIEW')
        self.assertEqual(observe_active(process(line+'internal compiler error: failure\n'),formal)['status'],'COMPILER_CRASH')
        shifted=line.replace(f':{a["column"]}:',f':{a["column"]+1}:')
        self.assertEqual(observe_active(process(shifted),formal)['status'],'TARGET_LOCATION_OR_COUNT_REVIEW')
        self.assertEqual(observe_active(process(line.replace('incompatible type for argument 1 of callback','unknown category')),formal)['status'],'UNRELATED_ERROR_REVIEW')
        self.assertEqual(observe_active(process(line,'timeout'),formal)['status'],'COMPILER_TIMEOUT')


if __name__=='__main__':unittest.main()
