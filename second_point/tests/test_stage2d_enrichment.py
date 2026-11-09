import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from edgm_second.seed_enrichment import plan_enrichment, enrich_seed, Context
from edgm_second.native_bitfield_candidates import scan_native
from edgm_second.environment import load_config, inspect_environment, run_process
from edgm_second.seeds import validate_seed

GOOD='/* 中文 */\nint f(int x) { return (x+3)*2; }\nint main(void){return f(2)!=10;}\n'
NATIVE='struct A { int x; }; int f(void) { struct A a={0}; int *p=&a.x; return a.x+*p; }'

class EnrichmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env=inspect_environment(load_config()); cls.runtime=load_config()['runtime']

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.p=self.root/'original.c'; self.out=self.root/'enhanced.c'
        self.p.write_text(GOOD)

    def plan(self, text=None):
        if text is not None:self.p.write_text(text)
        return plan_enrichment(self.p,self.runtime)

    def test_legal_replacement_actual_member_address_data_flow(self):
        r=enrich_seed(self.p,self.out,self.runtime)
        self.assertEqual(r['status'],'ENRICHED_UNVALIDATED',r)
        self.assertEqual(r['new_structure']['address_relation_count'],1)
        self.assertEqual(r['new_structure']['field_canonical_type'],'INT')
        self.assertNotEqual(r['original_sha256'],r['enhanced_sha256'])
        for p in (self.p,self.out): self.assertEqual(validate_seed(p,self.env)['status'],'valid')

    def test_proof_and_single_evaluation(self):
        r=self.plan();s=r['semantic_preservation']
        self.assertTrue(s['value_preserved']);self.assertTrue(s['parameter_definitely_initialized'])
        self.assertEqual(s['replacement_expression_evaluations'],1)
        self.assertFalse(s['observable_side_effects_added']);self.assertFalse(s['new_undefined_behavior'])
        self.assertEqual(r['edit']['after'].count('= (x)'),1)

    def test_source_map_complete_partition_and_original_nested_read(self):
        before=self.p.read_bytes();r=enrich_seed(self.p,self.out,self.runtime);after=self.out.read_bytes()
        seg=r['source_map']['segments'];self.assertEqual(seg[0]['original'][0],0)
        self.assertEqual(seg[-1]['original'][1],len(before));self.assertEqual(seg[-1]['enhanced'][1],len(after))
        for a,b in zip(seg,seg[1:]):
            self.assertEqual(a['original'][1],b['original'][0]);self.assertEqual(a['enhanced'][1],b['enhanced'][0])
        a,b=seg[1]['nested_original_read'];self.assertEqual(after[a:b],b'x')
        for s in (seg[0],seg[2]):
            self.assertEqual(before[s['original'][0]:s['original'][1]],after[s['enhanced'][0]:s['enhanced'][1]])

    def test_utf8_physical_anchors(self):
        r=enrich_seed(self.p,self.out,self.runtime)
        for a,p in ((r['selected_use'],self.p),(r['new_structure']['field'],self.out),(r['new_structure']['address'],self.out)):
            data=p.read_bytes();prefix=data[:a['offset']]
            self.assertEqual(a['line'],prefix.count(b'\n')+1)
            self.assertEqual(a['column'],len(prefix.rsplit(b'\n',1)[-1])+1)
            self.assertEqual(a['source_text'],data[a['start_offset']:a['end_offset']].decode())

    def test_read_only_and_deterministic_plans(self):
        before=self.p.read_bytes();a=self.plan();b=self.plan();self.assertEqual(a,b)
        enrich_seed(self.p,self.out,self.runtime);self.assertEqual(before,self.p.read_bytes())

    def test_no_compiler_required_for_semantic_plan(self):
        with patch('subprocess.run',side_effect=AssertionError('compiler forbidden')):
            self.assertEqual(self.plan()['status'],'ENRICHMENT_PLANNED')

    def test_macros_rejected_including_argument_mapping(self):
        for text in ('#define READ(v) ((v)+1)\nint f(int x){return READ(x);}',
                     '#define X x\nint f(int x){return X+1;}'):
            self.assertEqual(self.plan(text)['status'],'ENRICHMENT_REJECTED')

    def test_volatile_atomic_pointer_and_writes_rejected(self):
        for text in ('int f(volatile int x){return x+1;}',
                     'int f(_Atomic int x){return x+1;}',
                     'int f(int *x){return *x+1;}',
                     'int f(int x){x++; return x+1;}',
                     'int f(int x){int *p=&x;return x+1;}'):
            self.assertEqual(self.plan(text)['status'],'ENRICHMENT_REJECTED',text)

    def test_uninitialized_local_and_side_effect_expressions_rejected(self):
        for text in ('int f(void){int x;return x+1;}',
                     'int f(void){int x=0;return x++ + 1;}'):
            self.assertEqual(self.plan(text)['status'],'ENRICHMENT_REJECTED')

    def test_unevaluated_and_bound_contexts_rejected(self):
        for text in ('int f(int x){return sizeof(x+1);}',
                     'int f(int x){return _Generic((x+1), int: 3);}',
                     'int f(int x){int a[x+1];return 0;}'):
            self.assertEqual(self.plan(text)['status'],'ENRICHMENT_REJECTED',text)

    def test_worker_timeout_and_crash_not_zero_candidates(self):
        import subprocess
        from edgm_second.enrichment_run import isolated
        with patch('subprocess.run',side_effect=subprocess.TimeoutExpired(['worker'],0.01)):
            r=isolated('native',self.p,self.runtime)
        self.assertEqual(r['status'],'SCAN_TIMEOUT');self.assertIsNone(r['candidate_count'])
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([], -11, b'', b'crash')):
            r=isolated('native',self.p,self.runtime)
        self.assertEqual(r['status'],'WORKER_FAILED');self.assertIsNone(r['candidate_count'])
        r=isolated('native',self.p,self.runtime,timeout=0.00001)
        self.assertEqual(r['status'],'SCAN_TIMEOUT');self.assertIsNone(r['candidate_count'])

    def test_all_50_stage2b_sources_retain_manifest_hashes(self):
        from edgm_second.environment import PROJECT
        from edgm_second.source_edit import digest
        records=json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text())
        self.assertEqual(len(records),50)
        self.assertTrue(all(digest(Path(r['source']).read_bytes())==r['sha256'] for r in records))

    def test_same_name_parameters_not_merged(self):
        r=self.plan('int a(int x){x++;return x+1;} int b(int x){return x+2;}')
        self.assertEqual(r['status'],'ENRICHMENT_PLANNED')
        self.assertGreater(r['selected_declaration']['offset'],30)
        self.assertIn('@F@b@',r['selected_declaration']['usr'])

    def test_header_nodes_not_enriched(self):
        (self.root/'h.h').write_text('static int hidden(int x){return x+1;}')
        self.assertEqual(self.plan('#include "h.h"\nint main(void){return 0;}')['status'],'ENRICHMENT_REJECTED')

    def test_header_body_cannot_hide_parameter_writes_or_record_uses(self):
        (self.root/'body.h').write_text('x++;')
        self.assertEqual(self.plan('int f(int x){\n#include "body.h"\nreturn x+1;}')['status'],'ENRICHMENT_REJECTED')
        (self.root/'extra.h').write_text('int extra(struct A *p){return p->x;}')
        r=self.native(NATIVE+'\n#include "extra.h"\n')
        self.assertEqual(r['candidate_count'],0,r)
        self.assertIn('LATE_HEADER_INCLUDE_OUTSIDE_CLOSED_WORLD',r['rejection_counts'])

    def test_parse_failure_not_rejection(self):
        self.assertEqual(self.plan('int main( {')['status'],'PARSE_FAILED')
        self.assertEqual(plan_enrichment(self.root/'missing.c',self.runtime)['status'],'SCAN_FAILED')

    def test_output_never_overwrites(self):
        with self.assertRaises(ValueError):enrich_seed(self.p,self.p,self.runtime)
        self.out.write_text('keep')
        with self.assertRaises(FileExistsError):enrich_seed(self.p,self.out,self.runtime)
        self.assertEqual(self.out.read_text(),'keep')

    def test_name_collision_checked(self):
        r=self.plan();tag=r['new_names']['tag']
        # Test the actual candidate name reservation without changing source SHA.
        from edgm_second import seed_enrichment as module
        original=module.Context
        class Reserved(original):
            def __init__(self,*a,**k):super().__init__(*a,**k);self.names.add(tag)
        with patch.object(module,'Context',Reserved):s=self.plan()
        self.assertNotEqual(s['new_names']['tag'],tag)

    def test_dynamic_both_compilers_extreme_values(self):
        self.p.write_text('#include <stdio.h>\n#include <limits.h>\nint f(int x){return x ^ 3;}\nint main(void){printf("%d %d %d %d\\n",f(0),f(-7),f(INT_MAX),f(INT_MIN));return 0;}')
        r=enrich_seed(self.p,self.out,self.runtime);self.assertEqual(r['status'],'ENRICHED_UNVALIDATED')
        for compiler in ('gcc','clang'):
            runs=[]
            for i,p in enumerate((self.p,self.out)):
                exe=self.root/f'{compiler}{i}'
                build=run_process([self.env['compilers'][compiler]['path'],'-std=c11','-w',str(p),'-o',str(exe)],10)
                self.assertEqual(build['returncode'],0,build)
                runs.append(run_process([exe],5))
            self.assertEqual(runs[0]['returncode'],0)
            self.assertEqual((runs[0]['returncode'],runs[0]['stdout'],runs[0]['stderr']),
                             (runs[1]['returncode'],runs[1]['stdout'],runs[1]['stderr']))

    def native(self,text=NATIVE):
        self.p.write_text(text);return scan_native(self.p,self.runtime)

    def test_native_positive_all_addresses_enumerated_and_readonly(self):
        text=NATIVE.replace('return a.x+*p','int *q=&a.x; return a.x+*p+*q')
        a=self.native(text);b=scan_native(self.p,self.runtime)
        self.assertEqual(a,b);self.assertEqual(a['candidate_count'],1,a)
        self.assertEqual(len(a['candidates'][0]['address_anchors']),2)
        self.assertEqual(self.p.read_text(),text)

    def test_native_layout_escape_initialization_and_type_rejected(self):
        for text in (NATIVE.replace('int x','long x'),NATIVE.replace('struct A {','union A {').replace('struct A a','union A a'),
                     NATIVE.replace('return a.x+*p','return sizeof(a)+a.x+*p'),
                     NATIVE.replace('struct A a={0}','struct A a={1}'),
                     NATIVE.replace('int *p=&a.x','struct A *b=&a; int *p=&a.x'),
                     NATIVE.replace('int x;','int x, y;')):
            r=self.native(text);self.assertEqual(r['status'],'NOT_APPLICABLE',r)

    def test_native_macros_and_same_named_fields(self):
        r=self.native('#define ADDR(o) (&(o).x)\n'+NATIVE.replace('&a.x','ADDR(a)'))
        self.assertEqual(r['candidate_count'],0,r)
        r=self.native('struct B { long x; };'+NATIVE)
        self.assertEqual(r['candidate_count'],1,r)
        self.assertIn('@S@A@FI@x',r['candidates'][0]['field']['usr'])

    def test_native_parse_failed_not_zero_candidates(self):
        r=self.native('int {');self.assertEqual(r['status'],'PARSE_FAILED');self.assertIsNone(r['candidate_count'])

    def test_native_header_address_not_counted(self):
        (self.root/'h.h').write_text(NATIVE)
        r=self.native('#include "h.h"\nint main(void){return f();}')
        self.assertEqual(r['candidate_count'],0);self.assertEqual(r['raw_address_relation_count'],0)

if __name__=='__main__':unittest.main()
