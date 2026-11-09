import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from edgm_second.environment import PROJECT, load_config
from edgm_second.semantic_profile import profile_file
from edgm_second.semantic_profile_run import isolated_profile, aggregate, semantic_projection

SOURCE = '''/* 中文位置 */
enum Limits { BOUND=4, NEXT=BOUND+1 };
typedef int Scalar;
typedef Scalar Chain;
struct Leaf { int member; unsigned bits:3; };
struct Box { struct Leaf leaf; };
int use(int **p, int (*grid)[BOUND], Scalar value);
int use(int **p, int (*grid)[BOUND], Scalar value) { **p=value; return grid[0][0]; }
int main(void) {
    int array[2][BOUND] = {{0}};
    const int local_bound=2;
    int variable_array[local_bound];
    int target=0; int *pointer=&target; int **outer=&pointer;
    Chain value=1;
    struct Leaf leaf={0}; struct Box a={0},b={0};
    int *member_pointer=&leaf.member;
    const volatile int qualified=0;
    *pointer=value;
    a=b;
    (void)use(outer,array,value);
    switch(value) { case BOUND: switch(target) { case 1: break; default: break; } break; case NEXT: break; default: break; }
    goto done;
    done: return qualified + *member_pointer;
}
'''


class Stage2CProfileTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir=PROJECT,prefix='stage2c-test-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.path=self.root/'P0.c';self.path.write_text(SOURCE)
        self.runtime=load_config()['runtime']

    def profile(self):
        result=profile_file(self.path,self.runtime)
        self.assertEqual(result['status'],'PROFILED',result)
        return result

    def test_twelve_structure_families_and_cross_node_edges(self):
        r=self.profile()
        for name in ('typedef_declarations','main_function_calls','structs','unions','pointer_declarations',
                     'qualified_declarations','arrays','enum_constants','switches','gotos','bitfields','integer_operations'):
            if name!='unions':self.assertGreater(r['structures'][name]['instances'],0,name)
        for name in ('typedef_to_operation','formal_call_argument','member_to_address','pointer_to_indirect_write',
                     'constant_to_array_bound','switch_to_label','goto_to_label','bitfield_width_to_field',
                     'record_type_to_assignment','nested_pointer_formal_actual','record_contains_record'):
            self.assertGreater(r['relations'][name]['instances'],0,name)
        self.assertIsNone(r['confirmed_mr_candidates'])
        self.assertEqual(r['mr_applicability'],'NOT_ASSESSED')

    def test_nested_switch_nearest_owner_and_converted_values(self):
        r=self.profile()
        self.assertEqual(r['structures']['nested_switches']['instances'],1)
        self.assertEqual(r['relations']['switch_to_label']['instances'],5)
        switches=r['details']['switches']
        self.assertEqual(sorted(len(s['labels']) for s in switches),[2,3])
        values=[label['converted_value'] for s in switches for label in s['labels'] if label['kind']=='CASE_STMT']
        self.assertEqual(sorted(values),[1,4,5])

    def test_const_object_not_falsely_certified_as_c11_ice(self):
        r=self.profile()
        rows=r['relations']['constant_to_array_bound']['examples']
        self.assertTrue(any(e['enum_constant'] and e['c11_ice_reference'] for e in rows))
        self.assertTrue(any(e['const_object'] and not e['c11_ice_reference'] for e in rows))
        arrays=r['details']['arrays']
        vla=next(a for a in arrays if a['type']['canonical_kind']=='VARIABLEARRAY')
        self.assertFalse(vla['bound_expressions'][0]['ice_syntax_subset'])

    def test_typedef_scope_identity_and_propagation(self):
        self.path.write_text('typedef int A; int main(void){ A x=0; { typedef unsigned A; A x=1; x+=1; } return x+1; }')
        r=self.profile();edges=r['relations']['typedef_to_operation']['examples']
        self.assertEqual(len({e['source']['usr'] for e in edges}),2)
        self.assertEqual(len({e['variable_usr'] for e in edges}),2)

    def test_array_shadowing_not_merged(self):
        self.path.write_text('int main(void){int a[2]={0}; {int a[3]={0}; a[1]=1;} a[0]=2; return a[0];}')
        r=self.profile()
        edges=r['relations']['array_decl_to_subscript']['examples']
        self.assertEqual(r['relations']['array_decl_to_subscript']['instances'],3)
        self.assertEqual(len({e['source']['usr'] for e in edges}),2)
        self.assertEqual(sorted(a['type']['array_bounds'][0] for a in r['details']['arrays']),[2,3])

    def test_formals_resolve_definition_and_parameter_position(self):
        self.path.write_text('int first(int x); int first(int x){return x;} int other(int x){return x;} int main(void){return first(1)+other(2);}')
        r=self.profile()
        edges=r['relations']['formal_call_argument']['examples']
        self.assertEqual(len(edges),2)
        self.assertEqual(len({e['function_usr'] for e in edges}),2)
        self.assertEqual(len({e['parameter_decl_usr'] for e in edges}),2)
        self.assertEqual(r['relations']['function_redeclaration']['instances'],1)
        self.assertTrue(r['relations']['function_redeclaration']['examples'][0]['canonical_types_equal'])

    def test_header_nodes_filtered_including_same_basename(self):
        include=self.root/'include';include.mkdir()
        (include/'P0.c').write_text('typedef int HeaderAlias; struct H{int x;}; int helper(int a){return a;}')
        self.path.write_text('#include "include/P0.c"\nint main(void){return helper(0);}')
        r=self.profile()
        self.assertEqual(r['structures']['typedef_declarations']['instances'],0)
        self.assertEqual(r['structures']['structs']['instances'],0)
        self.assertEqual(r['structures']['main_function_calls']['instances'],0)
        self.assertEqual(r['structures']['external_function_calls']['instances'],1)
        for entry in r['structures'].values():
            for example in entry['examples']:self.assertEqual(example['anchor']['file'],str(self.path))

    def test_readonly_reproducible_and_utf8_coordinates(self):
        before=self.path.read_bytes();first=self.profile();second=self.profile()
        self.assertEqual(semantic_projection(first),semantic_projection(second))
        self.assertEqual(before,self.path.read_bytes())
        for collection in (first['structures'],first['relations']):
            for entry in collection.values():
                for sample in entry['examples']:
                    a=sample.get('anchor',sample.get('target'))
                    prefix=before[:a['offset']]
                    self.assertEqual(a['line'],prefix.count(b'\n')+1)
                    self.assertEqual(a['column'],len(prefix.rsplit(b'\n',1)[-1])+1)

    def test_parse_failure_is_not_zero_structure_or_not_applicable(self):
        self.path.write_text('int main(void){return missing;}')
        r=profile_file(self.path,self.runtime)
        self.assertEqual(r['status'],'PARSE_FAILED')
        self.assertNotIn('structures',r)
        self.path.write_text('int main(void){return 0;}')
        r=self.profile()
        self.assertEqual(r['relations']['formal_call_argument']['instances'],0)
        self.assertEqual(r['status'],'PROFILED')

    def test_timeout_worker_failure_and_denominator(self):
        milestone=json.dumps(dict(event='ast_ready',ast_status='OK'))+'\n'
        with patch('edgm_second.semantic_profile_run.subprocess.run',side_effect=
                   subprocess.TimeoutExpired(['worker'],1,output=milestone.encode(),stderr=b'partial')):
            r=isolated_profile(self.path,self.runtime,1)
        self.assertEqual(r['status'],'PROFILE_TIMEOUT');self.assertEqual(r['ast_status'],'OK')
        self.assertEqual(r['worker_process']['stderr'],'partial')
        stats=aggregate([dict(seed_id='failed',profile=r)])
        self.assertEqual(stats['completed_seeds'],0)
        self.assertIsNone(stats['confirmed_mr_candidate_count'])
        with patch('edgm_second.semantic_profile_run.subprocess.run',return_value=subprocess.CompletedProcess([], -11,b'',b'crash')):
            self.assertEqual(isolated_profile(self.path,self.runtime,1)['status'],'WORKER_FAILED')

    def test_indirect_writes_not_confused_with_pointer_reassignment(self):
        self.path.write_text('int main(void){int a=0;int *p=&a;int *q=&a;p=q;*p=1;return a;}')
        r=self.profile()
        self.assertEqual(r['structures']['writes']['instances'],2)
        self.assertEqual(r['structures']['indirect_writes']['instances'],1)
        self.assertEqual(r['relations']['pointer_to_indirect_write']['instances'],1)

    def test_integer_evaluation_does_not_call_target_compilers(self):
        with patch.object(subprocess,'run',side_effect=AssertionError('no compiler calls')):
            r=self.profile()
        self.assertTrue(any(label['value']==4 for switch in r['details']['switches'] for label in switch['labels']))


    def test_pointer_subscript_not_reported_as_array_declaration(self):
        self.path.write_text('int main(void){int a[2]={0};int *p=a; p[0]=1;return a[0];}')
        r=self.profile()
        self.assertEqual(r['relations']['base_decl_to_subscript']['instances'],2)
        self.assertEqual(r['relations']['array_decl_to_subscript']['instances'],1)

    def test_nonconstant_vla_object_not_counted_as_constant_declaration(self):
        self.path.write_text('int main(void){int n=2;int a[n];return 0;}')
        r=self.profile()
        self.assertEqual(r['relations']['bound_declaration_dependency']['instances'],1)
        self.assertEqual(r['relations']['constant_to_array_bound']['instances'],0)

    def test_same_label_spelling_different_functions(self):
        self.path.write_text('int f(void){goto L; L:return 0;} int main(void){goto L;L:return f();}')
        r=self.profile()
        self.assertEqual(r['relations']['goto_to_label']['instances'],2)
        samples=r['relations']['goto_to_label']['examples']
        self.assertTrue(all(e['same_function'] for e in samples))
        self.assertEqual(len({e['target']['function_id'] for e in samples}),2)

    def test_same_member_spelling_keeps_record_declaration_identity(self):
        self.path.write_text('struct A{int f;};struct B{int f;};int main(void){struct A a={0};struct B b={0};int*p=&a.f;int*q=&b.f;return *p+*q;}')
        r=self.profile()
        self.assertEqual(r['relations']['member_to_address']['instances'],2)
        rows=r['relations']['member_to_address']['examples']
        self.assertEqual(len({e['source']['usr'] for e in rows}),2)
        self.assertTrue(all(e['field_type']['canonical_kind']=='INT' for e in rows))

    def test_relation_occurrences_are_dependency_counts_in_aggregate(self):
        self.path.write_text('int f(int x){return x;}int main(void){return f(1);}')
        r=self.profile()
        stats=aggregate([dict(seed_id='fixture',profile=r)])
        self.assertEqual(stats['relations']['formal_call_argument']['instances'],1)
        self.assertEqual(stats['relations']['formal_call_argument']['dependency_relations'],1)
        self.assertEqual(stats['relations']['formal_call_argument']['seed_count'],1)
