"""Small target-specific diagnostic observer; not a general comparator."""
import re
from pathlib import Path
from .environment import run_process


def collect_integer_profile(environment):
    evidence={}; facts=[]
    for name,identity in environment['compilers'].items():
        result=run_process([identity['path'],'-std=c11','-dM','-E','-x','c','/dev/null'],20)
        macros=dict(re.findall(r'^#define (\w+) ([^\n]+)$',result['stdout'],re.M))
        evidence[name]=result
        if result['status']!='ok':return dict(status='UNVERIFIED',evidence=evidence)
        try:
            facts.append(dict(int_width=int(macros['__INT_WIDTH__'],0),
                sizeof_int=int(macros['__SIZEOF_INT__'],0),char_bit=int(macros['__CHAR_BIT__'],0),
                int_max=int(macros['__INT_MAX__'],0)))
        except (ValueError,KeyError):return dict(status='UNVERIFIED',evidence=evidence)
    return dict(status='VERIFIED' if len(facts)==2 and facts[0]==facts[1] else 'UNVERIFIED',
                facts=facts[0], evidence=evidence,
                scope='default local GCC/Clang targets only; no cross compilation or user ABI flags')


def observe_errors(process, witness):
    """Exact coordinates retained; category recognition is deliberately local.

    Locations may equal the & token or member token. Other locations are kept
    as review candidates, never silently tolerated or labelled compiler bugs.
    """
    w=witness.to_dict() if hasattr(witness,'to_dict') else witness
    errors=[]
    for line in process['stderr'].splitlines():
        m=re.match(r'^(.*):(\d+):(\d+): (fatal error|error): (.*)$',line)
        if not m:continue
        file,row,col,severity,message=m.groups()
        target=bool(re.search(r'(cannot take address of bit-field|address of bit-field requested)',message))
        item=dict(file=file,line=int(row),column=int(col),severity=severity,message=message,
                  category='address_of_bitfield' if target else 'unknown',raw=line,matched_anchors=[])
        for number,use in enumerate(w['affected_uses']):
            for key in ('address','member'):
                a=use[key]['p1']
                if (str(Path(file).resolve()),int(row),int(col))==(a['file'],a['line'],a['column']):
                    item['matched_anchors'].append(dict(violation=number,anchor=key))
        errors.append(item)
    if process['status'] in ('timeout','crashed','execution_failed'):
        status={'timeout':'COMPILER_TIMEOUT','crashed':'COMPILER_CRASH','execution_failed':'EXECUTION_FAILED'}[process['status']]
    elif any(e['category']=='unknown' for e in errors):status='UNRELATED_ERROR_REVIEW'
    elif not errors or process['returncode']==0:status='MISSING_TARGET_ERROR_CANDIDATE'
    elif any(not e['matched_anchors'] for e in errors):status='TARGET_LOCATION_REVIEW'
    elif {x['violation'] for e in errors for x in e['matched_anchors']}!=set(range(len(w['affected_uses']))):
        status='TARGET_COVERAGE_REVIEW'
    else:status='TARGET_ERRORS_OBSERVED'
    return dict(status=status,errors=errors,confirmed_bug=False,
                expected_violation_count=len(w['affected_uses']),exact_coordinate_comparison=True)
