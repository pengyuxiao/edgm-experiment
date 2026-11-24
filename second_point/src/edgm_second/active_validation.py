"""Necessary local observation for the typedef MR; no diagnostic oracle input."""
import re
from pathlib import Path
from .bitfield_validation import observe_errors


def observe_typedef(process,formal):
    target=formal['source_anchors']['argument']['p1'];errors=[]
    for line in process['stderr'].splitlines():
        m=re.match(r'^(.*):(\d+):(\d+): (fatal error|error): (.*)$',line)
        if not m:continue
        file,row,col,severity,message=m.groups()
        category='incompatible_struct_argument' if (re.search(r'incompatible type for argument 1',message) or
            re.search(r'passing .* to parameter of incompatible type',message)) else 'unknown'
        exact=(str(Path(file).resolve()),int(row),int(col))==(target['file'],target['line'],target['column'])
        errors.append(dict(file=file,line=int(row),column=int(col),severity=severity,category=category,message=message,raw=line,exact_target_anchor=exact))
    if process['status'] in ('timeout','crashed','execution_failed'):status={'timeout':'COMPILER_TIMEOUT','crashed':'COMPILER_CRASH','execution_failed':'EXECUTION_FAILED'}[process['status']]
    elif not errors or process['returncode']==0:status='MISSING_TARGET_ERROR_CANDIDATE'
    elif any(e['category']=='unknown' for e in errors):status='UNRELATED_ERROR_REVIEW'
    elif len(errors)!=1 or not errors[0]['exact_target_anchor']:status='TARGET_LOCATION_OR_COUNT_REVIEW'
    else:status='TARGET_ERRORS_OBSERVED'
    return dict(status=status,errors=errors,confirmed_bug=False)


def observe_active(process,formal):
    result=observe_typedef(process,formal) if 'TYPEDEF' in formal['mr_id'] else observe_errors(process,formal)
    if result['status'] in ('COMPILER_TIMEOUT','COMPILER_CRASH','EXECUTION_FAILED'):return result
    if 'internal compiler error:' in process['stderr'] or 'PLEASE submit a bug report' in process['stderr']:
        return dict(result,status='COMPILER_CRASH',driver_reported_internal_error=True)
    parsed={e['raw'] for e in result['errors']}
    headers=[line for line in process['stderr'].splitlines()
             if re.match(r'^(?:[^:\n|]+(?::\d+){0,2}:\s*)?(?:fatal )?error:\s',line) and line not in parsed]
    if headers:return dict(result,status='UNPARSED_ERROR_REVIEW',unparsed_error_headers=headers)
    return result
