import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from edgm_second.environment import PROJECT, inspect_environment, load_config, run_process
from edgm_second.seeds import validate_seed
from edgm_second.seed_enrichment import enrich_seed, Context
from edgm_second.source_edit import digest
from edgm_second.mr_member_bitfield_addressability import (
    scan_candidates, build_mutation, verify_bitfield_witness, verify_enrichment_provenance)
from edgm_second.bitfield_validation import collect_integer_profile, observe_errors
from edgm_second.bitfield_run import MANUAL, isolated_scan


class BitfieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env=inspect_environment(load_config())
        cls.profile=collect_integer_profile(cls.env)
        if not cls.env['ready']:raise RuntimeError('required target compilers unavailable')
        cls.runtime=cls.env['runtime']['path']

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=PROJECT)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.p0=self.root/'P0.c';self.p0.write_text(MANUAL)
        self.p1=self.root/'P1.c'

    def scan(self,text=None):
        if text is not None:self.p0.write_text(text)
        return scan_candidates(self.p0,self.runtime,self.profile)

    def build(self,provenance=None,admission=None):
        provenance=provenance or dict(source_kind='Native',original_path=str(self.p0),
            original_sha256=digest(self.p0.read_bytes()),original_to_p0=dict(kind='identity'))
        return build_mutation(self.p0,self.p1,self.runtime,self.profile,
            admission or validate_seed(self.p0,self.env),provenance)

    def enhanced(self):
        original=self.root/'S.c';original.write_text('int compute(int input) { return (input ^ 3) + 2; }\n')
        self.p0=self.root/'enhanced.c'
        receipt=enrich_seed(original,self.p0,self.runtime)
        return original,receipt,dict(source_kind='Enriched',original_path=str(original),
                                   original_sha256=receipt['original_sha256'],enrichment=receipt)

    def test_manual_native_two_addresses_exact_errors(self):
        scan,p1,w=self.build()
        self.assertEqual(scan['candidate_count'],1);self.assertEqual(len(w.affected_uses),2)
        self.assertEqual(w.width_proof['width'],32)
        self.assertIn(b'signed int value : 32',p1)
        self.p1.write_bytes(p1)
        result=validate_seed(self.p1,self.env)
        for r in result['checks'].values():
            observed=observe_errors(r,w)
            self.assertEqual(observed['status'],'TARGET_ERRORS_OBSERVED',r['stderr'])
            self.assertEqual(len(observed['errors']),2)

    def test_enriched_identity_original_path_and_target_error(self):
        original,receipt,provenance=self.enhanced()
        scan,p1,w=self.build(provenance)
        self.assertEqual(scan['candidate_count'],1)
        self.assertEqual(w.isolation['complexity'],'simple_local_enhancement')
        self.assertEqual(w.isolation['original_operation']['source_text'].split()[-2:],['^','3'])
        self.assertTrue(verify_bitfield_witness(w,self.p0.read_bytes(),p1,original.read_bytes()))
        self.p1.write_bytes(p1)
        for r in validate_seed(self.p1,self.env)['checks'].values():
            self.assertEqual(observe_errors(r,w)['status'],'TARGET_ERRORS_OBSERVED',r['stderr'])

    def test_oracle_before_compile_no_p1_ast_or_diagnostics(self):
        original,_,provenance=self.enhanced(); admission=validate_seed(self.p0,self.env)
        paths=[]
        def p0_only(path,runtime):
            self.assertEqual(Path(path),self.p0);paths.append(str(path));return Context(path,runtime)
        with patch('subprocess.run',side_effect=AssertionError('compiler forbidden')), \
             patch('edgm_second.mr_member_bitfield_addressability.Context',side_effect=p0_only):
            scan,p1,w=self.build(provenance,admission)
            self.assertTrue(verify_bitfield_witness(w,self.p0.read_bytes(),p1,original.read_bytes()))
        self.assertTrue(paths);self.assertFalse(self.p1.exists())

    def test_renamed_record_and_field_no_fixed_names(self):
        self.assertEqual(self.scan(MANUAL.replace('Payload','Renamed').replace('value','i'))['candidate_count'],1)
        _,p1,w=self.build()
        self.assertTrue(verify_bitfield_witness(w,self.p0.read_bytes(),p1,self.p0.read_bytes()))

    def test_same_name_fields_not_merged_and_all_candidates(self):
        text='struct A { int x; }; struct B { int x; }; int f(void) { struct A a={0}; struct B b={0}; int *p=&a.x; int *q=&b.x; return *p+*q; }'
        result=self.scan(text)
        self.assertEqual(result['candidate_count'],2)
        self.assertNotEqual(result['candidates'][0]['field']['usr'],result['candidates'][1]['field']['usr'])
        admission=validate_seed(self.p0,self.env)
        provenance=dict(source_kind='Native',original_path=str(self.p0),original_sha256=admission['sha256'])
        _,p1,w=build_mutation(self.p0,self.p1,self.runtime,self.profile,admission,provenance,candidate_index=1)
        self.assertEqual(w.changed_declaration['field_usr'],result['candidates'][1]['field']['usr'])

    def test_unsigned_and_bool_full_width(self):
        for base,width in [('unsigned int',32),('_Bool',1)]:
            with self.subTest(base=base):
                result=self.scan(MANUAL.replace('int value',base+' value').replace('int *',base+' *'))
                self.assertEqual(result['candidate_count'],1,result)
                self.assertEqual(result['candidates'][0]['width_proof']['width'],width)

    def test_unsupported_type_and_qualifiers(self):
        for base in ['long','volatile int','const int','_Atomic int']:
            with self.subTest(base=base):
                r=self.scan(MANUAL.replace('int value',base+' value').replace('int *',base+' *'))
                self.assertEqual(r['candidate_count'],0,r)

    def test_layout_escape_macro_and_initialization_refusals(self):
        variants=[MANUAL.replace('return input +','return sizeof(object) + input +'),
            MANUAL.replace('{0}','{2}'),MANUAL.replace('int value;','int value, other;'),
            MANUAL.replace('struct Payload object = {0};','static struct Payload object = {0};'),
            '#define TYPE int\n'+MANUAL.replace('int value','TYPE value'),
            MANUAL.replace('int *first =','struct Payload *escaped = &object; int *first =')]
        for text in variants:
            with self.subTest(text=text):self.assertEqual(self.scan(text)['candidate_count'],0)

    def test_compound_extra_member_use_type_escape_and_sizeof_rejected(self):
        _,receipt,_=self.enhanced();text=self.p0.read_text();tag=receipt['new_names']['tag']
        variants=[text.replace('return (','return sizeof('),
                  text.replace('; }','; struct '+tag+' other; }'),
                  text.replace('int '+receipt['new_names']['member']+';',
                               'int '+receipt['new_names']['member']+'; int extra;')]
        for t in variants:
            with self.subTest(text=t):self.assertNotEqual(self.scan(t)['status'],'APPLICABLE')

    def test_compound_address_escape_rejected(self):
        _,receipt,_=self.enhanced()
        # Preserve a legal P0 while returning the address without dereference.
        member=receipt['new_names']['member'];tag=receipt['new_names']['tag']
        text=f'int *f(int input) {{ return &((struct {tag} {{int {member};}}){{.{member}=(input)}}).{member}; }}'
        r=self.scan(text);self.assertEqual(r['candidate_count'],0)
        self.assertIn('ADDRESS_ESCAPES',r['rejected_fields'][0]['reason'])

    def test_invalid_parse_separate_from_zero(self):
        r=self.scan('int main( {');self.assertEqual(r['status'],'PARSE_FAILED');self.assertIsNone(r['candidate_count'])

    def test_profile_mismatch_and_unknown_refused(self):
        for profile in [dict(status='UNVERIFIED'),dict(self.profile,facts=dict(self.profile['facts'],int_width=31))]:
            r=scan_candidates(self.p0,self.runtime,profile);self.assertEqual(r['candidate_count'],0)

    def test_new_signed_keyword_macro_cannot_capture_edit(self):
        r=self.scan('#define signed unsigned\n'+MANUAL)
        self.assertEqual(r['candidate_count'],0)
        self.assertEqual(r['rejected_fields'][0]['reason'],'REPLACEMENT_TYPE_KEYWORD_IS_MACRO')

    def test_multiple_independent_compound_fields_all_recorded(self):
        text='''int f(int a,int b) {
return (*(&((struct A {int same;}){.same=(a)}).same)) +
       (*(&((struct B {int same;}){.same=(b)}).same));
}'''
        result=self.scan(text)
        self.assertEqual(result['candidate_count'],2,result)
        self.assertEqual([len(c['address_anchors']) for c in result['candidates']],[1,1])

    def test_header_fields_and_late_include_not_silently_closed(self):
        header=self.root/'header.h';header.write_text('struct Foreign {int x;};\n')
        result=self.scan('#include "header.h"\nint f(void){struct Foreign a={0};int *p=&a.x;return *p;}')
        self.assertEqual(result['candidate_count'],0)
        original,receipt,_=self.enhanced()
        result=self.scan(self.p0.read_text()+'\n#include "header.h"\n')
        self.assertEqual(result['candidate_count'],0)
        self.assertIn('LATE_HEADER_INCLUDE_UNCLOSED',result['rejected_fields'][0]['reason'])

    def test_utf8_crlf_exact_anchor_transport(self):
        self.p0.write_bytes(('/* 中文 */\r\n'+MANUAL.replace('\n','\r\n')).encode())
        _,p1,w=self.build()
        self.assertTrue(verify_bitfield_witness(w,self.p0.read_bytes(),p1,self.p0.read_bytes()))

    def test_stale_admission_and_existing_output_refused(self):
        admission=validate_seed(self.p0,self.env);self.p0.write_text(MANUAL+'\n')
        with self.assertRaises(ValueError):self.build(admission=admission)
        self.p1.write_text('keep')
        with self.assertRaises(ValueError):self.build()
        self.assertEqual(self.p1.read_text(),'keep')

    def test_tampered_width_identity_policy_and_maps_refused(self):
        _,p1,w=self.build();p0=self.p0.read_bytes()
        mutations=[lambda x:x['width_proof'].update(width=1),
            lambda x:x['affected_uses'][0].update(field_usr='different'),
            lambda x:x['expected_error'].update(warning_satisfies=True),
            lambda x:x['source_map']['segments'][0].update(p1=[0,1]),
            lambda x:x['affected_uses'][0]['address']['p1'].update(column=999),
            lambda x:x['isolation'].update(record_uses_closed=False),
            lambda x:(x['affected_uses'].pop(),x['expected_error'].update(expected_violation_count=1)),
            lambda x:x['changed_declaration'].update(after_bitfield=False)]
        for mutate in mutations:
            v=copy.deepcopy(w.to_dict());mutate(v)
            with self.assertRaises(ValueError):verify_bitfield_witness(v,p0,p1,p0)

    def test_enrichment_source_map_tampering_refused(self):
        original,r,provenance=self.enhanced()
        r['source_map']['segments'][1]['nested_original_read'][0]+=1
        with self.assertRaises(ValueError):self.build(provenance)

    def test_no_diagnostics_unknown_or_location_tolerance(self):
        _,_,w=self.build();a=w.affected_uses[0]['address']['p1']
        def process(message,line=None,col=None,status='nonzero',rc=1):
            return dict(status=status,returncode=rc,stderr=f'{a["file"]}:{line or a["line"]}:{col or a["column"]}: error: {message}\n')
        self.assertEqual(observe_errors(process('unknown'),w)['status'],'UNRELATED_ERROR_REVIEW')
        self.assertEqual(observe_errors(process('cannot take address of bit-field',col=a['column']+1),w)['status'],
                         'TARGET_LOCATION_REVIEW')
        for status,expected in [('timeout','COMPILER_TIMEOUT'),('crashed','COMPILER_CRASH'),('execution_failed','EXECUTION_FAILED')]:
            self.assertEqual(observe_errors(process('',status=status),w)['status'],expected)
        self.assertEqual(observe_errors(dict(status='ok',returncode=0,stderr=''),w)['status'],'MISSING_TARGET_ERROR_CANDIDATE')

    def test_real_worker_deadline_not_zero(self):
        profile=self.root/'profile.json';profile.write_text(json.dumps(self.profile))
        r=isolated_scan(self.p0,self.runtime,profile,timeout=0.00001)
        self.assertEqual(r['status'],'SCAN_TIMEOUT');self.assertIsNone(r['candidate_count'])

    def test_readonly_deterministic_and_all_50_hashes(self):
        data=self.p0.read_bytes();self.assertEqual(self.scan(),self.scan());self.assertEqual(self.p0.read_bytes(),data)
        records=json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text())
        self.assertEqual(len(records),50)
        for r in records:self.assertEqual(digest(Path(r['source']).read_bytes()),r['sha256'])

    def test_full_range_controlled_bitfields_both_targets(self):
        # Legal control: no address expression. Check extrema without assigning
        # out-of-range values or executing any real Csmith program.
        source=self.root/'range.c';source.write_text('''#include <limits.h>
struct V { signed int s:32; unsigned int u:32; _Bool b:1; };
int main(void) {
    struct V v={0};
    int values[]={INT_MIN,-1,0,1,INT_MAX};
    for(unsigned i=0;i<5;i++){v.s=values[i];if(v.s!=values[i])return 1;}
    v.u=UINT_MAX;v.b=1;
    return v.u!=UINT_MAX || v.b!=1;
}
''')
        for name,c in self.env['compilers'].items():
            binary=self.root/name
            build=run_process([c['path'],'-std=c11','-w',str(source),'-o',str(binary)],20)
            self.assertEqual(build['returncode'],0,build['stderr'])
            self.assertEqual(run_process([str(binary)],5)['returncode'],0)

    def test_existing_real_enhanced_smoke_identity_and_provenance(self):
        receipt=json.loads((PROJECT/'evidence/stage2d/experiment/enrichment_records.json').read_text())[0]
        self.p0=Path(receipt['enhanced_path'])
        provenance=dict(source_kind='Enriched',original_path=receipt['original_path'],
                        original_sha256=receipt['original_sha256'],enrichment=receipt)
        original=self.p0.read_bytes();scan,p1,w=self.build(provenance)
        self.assertEqual(scan['candidate_count'],1)
        self.assertEqual(self.p0.read_bytes(),original)
        self.assertEqual(w.expected_error['category'],'address_of_bitfield')
        self.assertNotEqual(digest(p1),digest(original))


if __name__=='__main__':unittest.main()
