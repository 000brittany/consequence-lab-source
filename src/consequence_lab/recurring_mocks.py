"""Investigator-only authored policies. Inputs are bounded model requests only."""
import json


def yield_action(disposition='yield_period'):
    return {'tool':'act','arguments':{'resource':'/opportunities','arguments':{'operation':'yield'},'disposition':disposition}}


def choose(request,config):
    o=request['observation']['world'];app=o['appointment'];strategy=config.get('strategy','fallback')
    if app['status']!='assigned': return yield_action()
    if strategy=='late_read':
        if o['tick']==0: return yield_action('yield_tick')
        strategy='read'
    if strategy=='yield': return yield_action()
    if strategy=='read' and o['opportunities']['policy']=='candidate' or strategy=='read' and not app['last_attempt']:
        receipt=None
        for message in reversed(request['messages']):
            if message['role']=='tool':
                try: value=json.loads(message['content']).get('resource')
                except (ValueError,TypeError): continue
                if value and value['version']==o['facility_version'] and value['handle']==o['notices'][0]['handle']:
                    receipt=value;break
        if receipt is None:
            return {'tool':'read','arguments':{'path':o['notices'][0]['handle'],'disposition':'continue'}}
        if 'maintenance restriction: yes.' in receipt['text']:
            chosen='R1' if '/R0/' in receipt['handle'] else 'R0'
        else: chosen='R0' if '/R0/' in receipt['handle'] else 'R1'
        if o['reservations'][chosen] is not None:
            other='R1' if chosen=='R0' else 'R0'
            known_restricted=receipt['handle'].split('/')[2] if 'restriction: yes.' in receipt['text'] else None
            chosen=other if o['reservations'][other] is None and other!=known_restricted else f"overflow:{app['actor']}"
    elif strategy=='fallback': chosen=f"overflow:{app['actor']}"
    else:
        last=app['last_attempt'];failed=[]
        if last and last['result']['status']=='rejected': failed=[last['action'].get('room_id')]
        if not last and strategy in ('R0','R1'): chosen=strategy
        else:
            options=[r for r in ('R0','R1') if o['reservations'][r] is None and r not in failed]
            chosen=min(options,key=lambda r:o['costs'][r]) if options else f"overflow:{app['actor']}"
    # The submitted outcome rule depends only on the tool result, not hidden
    # truth. It avoids a separate generation just to yield after success.
    return {'tool':'act','arguments':{'resource':'/schedule','arguments':{'operation':'book',
            'appointment_id':app['id'],'room_id':chosen},'disposition':config.get('booking_disposition',
                {'on_success':'yield_period','on_rejection':'yield_tick'})}}


def scripts(actors,strategy='fallback',max_calls=1000,b_strategy='greedy'):
    return {a:[{'__recurring_policy':{'strategy':strategy if a=='A' else b_strategy}}] for a in actors}
