"""Resource authorization and public metadata projection, independent of booking.

No delivery worker, content injection, retrieval policy or agent decisions. Future
channel resources must enforce permissions here before returning any metadata.
Missing configuration retains the pre-controls calendar interface (no notices).
"""
import json
from .jsonio import clone, keys

MODES=('disabled','public_poll','public_metadata')


def configuration(mode='public_metadata'):
    value={'version':1,'mode':mode,'notification_limit':64}
    validate(value)
    return value


def validate(value):
    keys(value,('version','mode','notification_limit'))
    if (type(value['version']) is not int or value['version']!=1 or value['mode'] not in MODES
            or type(value['notification_limit']) is not int or value['notification_limit']!=64):
        raise ValueError('Invalid communication policy')


def validate_state(state):
    value=state['config'].get('communication')
    declared=state['world'].get('manifest',{}).get('communication')
    if value is not None:validate(value)
    if declared is not None and value!=declared:
        raise ValueError('Manifest and resource communication policies differ')


def public_allowed(state):
    value=state['config'].get('communication')
    return value is None or value['mode']!='disabled'


def authorize(path,policy):
    if policy is not None and policy['mode']=='disabled' and (path=='/volumes' or path.startswith('/volumes/')):
        raise ValueError('not_found_or_denied')


def augment_observation(state,actor):
    policy=state['config'].get('communication')
    if policy is None:return {}
    result={'communication':{**clone(policy),'public_workspace':'/volumes/common' if public_allowed(state) else None,
            'content_exposure':'Only explicit authorized reads or retained conversation; no automatic retrieval'}}
    if policy['mode']!='public_metadata':return result
    public=[op for op in state['artifact_revisions'] if op['after']['scope']=='shared']
    history=state['agents'][actor]['history'];cursor=0
    if history:
        previous=json.loads(history[-1]['messages'][0]['content'])
        cursor=previous.get('shared_updates',{}).get('cursor',0)
    start=max(cursor,len(public)-policy['notification_limit'])
    changes=[{'sequence':i+1,'operation':op['kind'],'path':op['after']['path'],
              'handle':op['after']['visible_id'],'revision':op['after']['revision']}
             for i,op in enumerate(public) if i>=start]
    result['shared_updates']={'namespace':'/volumes/common','cursor':len(public),'since':cursor,
        'omitted_changes':max(0,start-cursor),'changes':changes,
        'exposure':'filenames and revision metadata only; contents require an authorized read'}
    return result
