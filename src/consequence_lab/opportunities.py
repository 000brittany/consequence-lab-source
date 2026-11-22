"""Scenario scheduling rules; legacy runner behavior is unchanged without these hooks."""
from .jsonio import clone, keys


def category(action,w=None):
    if action and action.get('tool') in ('inspect','read','write','remove'): return 'information'
    if action and action.get('tool')=='act' and w is not None and action.get('arguments',{}).get('resource') in w.get('manifest',{}).get('information_resources',[]): return 'information'
    if action and action.get('tool')=='act' and action.get('arguments',{}).get('resource')=='/opportunities': return 'control'
    return 'domain'


def has_budget(w,actor):
    slot=w['control']['slots'][actor]
    if slot['yield_tick'] or slot['yield_period']: return False
    return not slot['domain_used'] or (w['manifest']['policy']=='candidate' and slot['info_used']<w['manifest']['information'])


def validate_action(w,actor,action):
    keys(action,('tool','arguments','disposition') if 'disposition' in action else ('tool','arguments'))
    disposition=action.get('disposition','yield_tick')
    if type(disposition) is dict:
        keys(disposition,('on_success','on_rejection'))
        choices=list(disposition.values())
    else: choices=[disposition]
    if any(type(x) is not str or x not in ('continue','yield_tick','yield_period') for x in choices):
        raise ValueError('Invalid scheduler disposition')
    slot=w['control']['slots'][actor]
    c=category(action,w)
    if c=='control':
        if action['arguments']!={'resource':'/opportunities','arguments':{'operation':'yield'}}:
            raise ValueError('Invalid yield operation')
        if 'continue' in choices: raise ValueError('Standalone yield must relinquish opportunities')
    elif w['manifest']['policy']=='legacy' or c=='domain':
        if slot['domain_used']: raise ValueError('Domain opportunity exhausted')
    elif slot['info_used']>=w['manifest']['information']:
        raise ValueError('Information opportunity exhausted')


def end_turn(state):
    n=len(state['agents'])
    cursor=state['scheduler']['cursor']+1
    state['scheduler']['cursor']=cursor%n
    if cursor==n: state['world']['control']['round_done']=True


def grant_tick(w):
    for actor,slot in w['control']['slots'].items():
        slot.update(domain_used=False,info_used=0,yield_tick=False)
        counts=w['control']['counts'][actor]
        if not slot['yield_period']:
            counts['domain_offered']+=1
            counts['information_offered']+=w['manifest']['information'] if w['manifest']['policy']=='candidate' else 0
        else:
            counts['domain_foregone']+=1
            counts['information_foregone']+=w['manifest']['information'] if w['manifest']['policy']=='candidate' else 0
    w['control']['round_done']=False


def finish(before,after,actor,action,error,proposals,result):
    w=after['world']; slot=w['control']['slots'][actor]; counts=w['control']['counts'][actor]
    counts['generations']+=1
    c=category(action,w)
    protocol_ok=True
    if action is not None:
        try: validate_action(before['world'],actor,action)
        except (ValueError,KeyError,TypeError): protocol_ok=False
    continuation=before['config'].get('continuation_policy')=='rooms_v3_recoverable_v1'
    attempted=error is None and (action is not None or not continuation)
    if attempted:
        counts['operations']+=1
        counts[c+'_attempts']+=1
    budget=None
    if c!='control' or not protocol_ok or error is not None:
        budget='domain' if (w['manifest']['policy']=='legacy' or c!='information') else 'information'
        if budget=='domain' and slot['domain_used']: budget='information'
        if budget=='information' and (w['manifest']['policy']=='legacy' or slot['info_used']>=w['manifest']['information']): budget='domain'
        if budget=='domain': slot['domain_used']=True
        else: slot['info_used']+=1
        counts[budget+'_consumed']+=1
    requested=action.get('disposition') if action else None
    disposition=requested if requested is not None else 'yield_tick'
    basis='agent' if requested is not None else 'omitted_default'
    if type(disposition) is dict and protocol_ok:
        disposition=disposition['on_success' if result and result.get('status')=='ok' else 'on_rejection']
        basis='agent_outcome_rule'
    if error is not None or not protocol_ok or action is None:
        disposition='yield_tick'
        if continuation:
            basis = ('platform_missed_decision' if error and error.get('technical_kind') == 'missed'
                     else 'platform_integrity_stop' if error and error.get('technical_kind') == 'fatal'
                     else 'platform_invalid_or_rejected_decision')
        else:
            basis='failed_decision_policy'
    if disposition=='yield_period': slot['yield_period']=True
    if disposition!='continue':
        counts['domain_relinquished']+=int(not slot['domain_used'])
        counts['information_relinquished']+=max(0,w['manifest']['information']-slot['info_used']) if w['manifest']['policy']=='candidate' else 0
        slot['yield_tick']=True
    continued=disposition=='continue' and has_budget(w,actor)
    if not continued: end_turn(after)
    return {'operation_category':c if attempted else None,'budget_consumed':budget,
            'requested_disposition':requested,'applied_disposition':disposition,'basis':basis,
            'retained_current_slot':continued,'generation_counted':1,'operation_attempted':attempted}


def initial(actors):
    names=('domain_consumed','information_consumed','domain_offered','information_offered','domain_foregone','information_foregone',
           'domain_relinquished','information_relinquished','generations','operations',
           'domain_attempts','information_attempts','control_attempts')
    return {'round_done':False,'slots':{a:{'domain_used':False,'info_used':0,'yield_tick':False,'yield_period':False} for a in actors},
            'counts':{a:{n:0 for n in names} for a in actors},'ticks_advanced':0,'periods_closed':0}
