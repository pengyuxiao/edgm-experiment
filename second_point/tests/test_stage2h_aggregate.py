import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from edgm_second.environment import PROJECT,inspect_environment,load_config
from edgm_second.seed_enrichment import Context
from edgm_second.active_enrichment import enumerate_positions
from edgm_second.aggregate_enrichment import plan,FAMILIES
from edgm_second.mr_member_const_aggregate_write import scan,build,verify,seal
from edgm_second.aggregate_validation import observe
from edgm_second.seeds import validate_seed


class AggregateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.env=inspect_environment(load_config());cls.runtime=cls.env['runtime']['path']

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=PROJECT);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.s=self.root/'S.c';self.s.write_text('int f(int input){return input ^ 3;}')

    def prepared(self,family='aggregate_direct',count=1):
        c,p,_=enumerate_positions(self.s,self.runtime);r,d=plan(c,p[0],family,count)
        p0=self.root/f'{family}{count}.c';p0.write_bytes(d);return r,p0,self.root/f'{family}{count}_P1.c'

    def test_direct_nested_multiple_real_errors_and_initialization(self):
        for f in FAMILIES:
            for count in (1,2):
                r,p0,p1=self.prepared(f,count);a=validate_seed(p0,self.env);self.assertEqual(a['status'],'valid')
                w,data=build(p0,p1,self.runtime,a,r);self.assertEqual(len(w['affected_uses']),count)
                self.assertEqual(len(w['proof']['containment']),int(f==FAMILIES[1]))
                self.assertTrue(verify(w,self.s.read_bytes(),p0.read_bytes(),data));p1.write_bytes(data)
                for c in validate_seed(p1,self.env)['checks'].values():
                    result=observe(c,w);self.assertEqual(result['status'],'TARGET_ERRORS_OBSERVED',c['stderr']);self.assertEqual(len(result['errors']),count)

    def test_oracle_without_subprocess_or_p1_ast(self):
        for f in FAMILIES:
            r,p0,p1=self.prepared(f);admission=validate_seed(p0,self.env)
            from edgm_second.ast_extract import parse_translation_unit
            def guard(path,*a,**kw):
                self.assertEqual(Path(path),p0);return parse_translation_unit(path,*a,**kw)
            with patch('subprocess.run',side_effect=AssertionError('compiler forbidden')),patch('edgm_second.seed_enrichment.parse_translation_unit',side_effect=guard):
                w,d=build(p0,p1,self.runtime,admission,r);self.assertTrue(verify(w,self.s.read_bytes(),p0.read_bytes(),d))
            self.assertFalse(p1.exists())

    def test_read_only_and_initialization_are_not_assignments(self):
        r,p0,p1=self.prepared();text=p0.read_text();n=r['names']
        p0.write_text(text.replace(f'{n["object"]} = {n["left"]};',''))
        self.assertEqual(scan(p0,self.runtime)['status'],'NOT_APPLICABLE')
        p0.write_text(p0.read_text().replace('int '+n['value'],'const int '+n['value']))
        self.assertEqual(validate_seed(p0,self.env)['status'],'valid')
        self.assertEqual(scan(p0,self.runtime)['status'],'NOT_APPLICABLE')

    def test_same_spelling_different_record_identity_unaffected(self):
        r,p0,p1=self.prepared();name=r['names']['value']
        p0.write_text(f'struct Unrelated {{ int {name}; }};\n'+p0.read_text())
        a=validate_seed(p0,self.env);w,d=build(p0,p1,self.runtime,a)
        self.assertEqual(d.count(b'const '),1);self.assertEqual(len(w['affected_uses']),1)

    def test_const_field_or_const_object_rejected(self):
        for replacement in ('field','object'):
            r,p0,p1=self.prepared(count=1 if replacement=='field' else 2);t=p0.read_text();n=r['names']
            if replacement=='field':t=t.replace('int '+n['value'],'const int '+n['value'])
            else:t=t.replace('struct '+n['record']+' '+n['object'],'const struct '+n['record']+' '+n['object'])
            p0.write_text(t)
            with self.assertRaises(ValueError):scan(p0,self.runtime)

    def test_extra_member_write_address_escape_and_macro_rejected(self):
        for k in range(3):
            r,p0,p1=self.prepared(count=1 if k==0 else 2) if k<2 else self.prepared('aggregate_nested')
            n=r['names'];text=p0.read_text()
            insert=[f'{n["object"]}.{n["value"]} = 2;',f'(void)&{n["object"]};',f'(void)sizeof({n["object"]});'][k]
            p0.write_text(text.replace('return ',insert+'return '))
            self.assertEqual(scan(p0,self.runtime)['status'],'NOT_APPLICABLE')

    def test_tamper_identity_containment_maps_and_coverage(self):
        r,p0,p1=self.prepared('aggregate_nested',2);w,d=build(p0,p1,self.runtime,validate_seed(p0,self.env),r)
        for mutate in (lambda x:x['proof']['containment'][0].update(inner_usr='wrong'),
                       lambda x:x['proof']['destination'].update(usr='wrong-object'),
                       lambda x:x['proof']['leaf_reads'][0].update(referenced_usr='wrong-field'),
                       lambda x:x['affected_uses'].pop(),lambda x:x['affected_uses'][0]['operator']['p1'].update(column=1),
                       lambda x:x['proof'].update(p1_modifiable=True),lambda x:x['active_enrichment']['source_map'][0].update(s=[1,2])):
            bad=copy.deepcopy(w);mutate(bad);bad['seal']=seal(bad)
            with self.assertRaises((ValueError,KeyError)):verify(bad,self.s.read_bytes(),p0.read_bytes(),d)

    def test_unsigned_values_and_signatures_ignore_names(self):
        self.s.write_text('unsigned f(unsigned input){return input ^ 3u;}')
        r,p0,p1=self.prepared('aggregate_nested');w,d=build(p0,p1,self.runtime,validate_seed(p0,self.env),r)
        self.assertEqual(w['proof']['canonical_kind'],'UINT');sig=r['semantic_context_signature']
        self.s.write_text('unsigned other(unsigned renamed){return renamed ^ 3u;}')
        c,p,_=enumerate_positions(self.s,self.runtime);r,_=plan(c,p[0],'aggregate_nested');self.assertEqual(sig,r['semantic_context_signature'])

    def test_type_macro_and_unclosed_record_type_use_rejected(self):
        for count in (1,2):
            r,p0,_=self.prepared(count=count);n=r['names'];text=p0.read_text()
            if count==1:text='#define SCALAR int\n'+text.replace('int '+n['value'],'SCALAR '+n['value'])
            else:text=text.replace('return ',f'(void)sizeof(struct {n["record"]});return ')
            p0.write_text(text)
            self.assertEqual(scan(p0,self.runtime)['status'],'NOT_APPLICABLE')

    def test_strict_unknown_offset_coverage_timeout_and_ice(self):
        r,p0,p1=self.prepared();w,d=build(p0,p1,self.runtime,validate_seed(p0,self.env),r)
        a=w['affected_uses'][0]['operator']['p1'];header=f'{a["file"]}:{a["line"]}:{a["column"]}: error: '
        text=header+"assignment of read-only variable 'dst'\n"
        def proc(s,status='nonzero'):return dict(status=status,returncode=1,stderr=s)
        self.assertEqual(observe(proc(text),w)['status'],'TARGET_ERRORS_OBSERVED')
        self.assertEqual(observe(proc(header+'unknown\n'),w)['status'],'TARGET_CATEGORY_REVIEW')
        self.assertEqual(observe(proc(text.replace(f':{a["column"]}:',f':{a["column"]+1}:')),w)['status'],'TARGET_LOCATION_REVIEW')
        self.assertEqual(observe(proc(text+text),w)['status'],'TARGET_COVERAGE_REVIEW')
        self.assertEqual(observe(proc(text,'timeout'),w)['status'],'COMPILER_TIMEOUT')
        self.assertEqual(observe(proc(text+'internal compiler error: boom'),w)['status'],'COMPILER_CRASH')

    def test_unified_bitfield_observer_rejects_duplicate_errors(self):
        import json
        folder=PROJECT/'evidence/stage2g/manual/experiment/action_001'
        w=json.loads((folder/'Oracle.json').read_text())['formal_witness']
        raw=(folder/'gcc.stderr.txt').read_text()
        self.assertEqual(observe(dict(status='nonzero',returncode=1,stderr=raw),w)['status'],'TARGET_ERRORS_OBSERVED')
        self.assertEqual(observe(dict(status='nonzero',returncode=1,stderr=raw+raw),w)['status'],'TARGET_COVERAGE_REVIEW')


if __name__=='__main__':unittest.main()
