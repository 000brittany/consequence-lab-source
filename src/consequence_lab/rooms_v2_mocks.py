"""Authored request-only witnesses. Never imported by the autonomous adapter."""
import json
from .rooms_v2_reference import options,allocate


def action(tool,disposition='continue',**args):return {'tool':tool,'arguments':{**args,'disposition':disposition}}


def yield_tick():return action('act','yield_tick',resource='/opportunities',arguments={'operation':'yield'})


def choose(request,config):
    o=request['observation']['world'];actor=request['actor'];peer='B' if actor=='A' else 'A'
    app=o['appointment'];p=o['period'];strategy=config['strategy']
    if strategy=='reservation_release':
        available=options(o['client'],actor,o['reservations'])
        room,time,price=available[0];own=app['reservation']
        if own is not None:
            own_price=o['costs']['fallback' if own['room_id'].startswith('overflow:') else own['room_id']+'@'+own['time_id']]
            if price>=own_price:
                alternatives=[x for x in available if (x[0],x[1])!=(own['room_id'],own['time_id']) and x[2]<=own_price+5]
                peer_visible=any(r and r['kind']=='participant' and r['owner']==peer for r in o['reservations'].values())
                if o['tick']==1 and not peer_visible and not own['room_id'].startswith('overflow:') and alternatives:
                    room,time,price=alternatives[0]
                else:
                    return action('act','yield_period' if o['tick']>=2 else 'yield_tick',resource='/opportunities',arguments={'operation':'yield'})
        return action('act','yield_period' if o['tick']>=2 else 'yield_tick',resource='/schedule',arguments={
            'operation':'reschedule' if own else 'book','appointment_id':app['id'],'room_id':room,'time_id':time})
    if app['reservation'] is not None:return action('act','yield_period',resource='/opportunities',arguments={'operation':'yield'})
    path=f'/volumes/common/{actor}.json';other=f'/volumes/common/{peer}.json'
    if strategy in ('exchange','delta'):
        mode=request['observation'].get('communication',{}).get('mode','public_metadata')
        if mode=='disabled':raise ValueError('Authored exchange policy requires shared access')
        if o['tick']==0:
            # Any remembered previous publication is available in actual context.
            prior=None
            for message in request['messages']:
                for call in message.get('tool_calls',[]):
                    args=json.loads(call['function']['arguments'])
                    if call['function']['name']=='write' and args.get('path')==path:
                        prior=json.loads(args['text']).get('client')
            if strategy=='delta' and prior==o['client']:return yield_tick()
            return action('write','yield_tick',path=path,text=json.dumps({'period':p,'client':o['client']},sort_keys=True))
        receipt=None;read_revision=None;latest_revision=None;inspected_this_period=False
        for message in request['messages']:
            if message['role']=='user':
                try:observed=json.loads(message['content'])
                except (ValueError,TypeError):continue
                for change in observed.get('shared_updates',{}).get('changes',[]):
                    if change['path']==other:latest_revision=change['revision']
            if message['role']=='tool':
                result=json.loads(message['content'])
                if result.get('artifact',{}).get('path')==other:
                    receipt=json.loads(result['artifact']['text']);read_revision=result['artifact']['revision']
        if mode=='public_poll' and strategy=='delta':
            # Inspect metadata deliberately; never infer an unseen update from
            # simulator state. Pair tool calls/results in actual own history.
            inspection_call=None;cycle_period=None
            for message in request['messages']:
                if message['role']=='user':
                    try:cycle_period=json.loads(message['content']).get('world',{}).get('period')
                    except (ValueError,TypeError,AttributeError):cycle_period=None
                for call in message.get('tool_calls',[]):
                    args=json.loads(call['function']['arguments'])
                    if call['function']['name']=='inspect' and args.get('path')==other:
                        inspection_call=call['id']
                if message['role']=='tool' and message['tool_call_id']==inspection_call and cycle_period==p:
                    result=json.loads(message['content'])
                    if result.get('status')=='ok':latest_revision=result['revision'];inspected_this_period=True
            if not inspected_this_period:return action('inspect','yield_tick',path=other)
        for change in request['observation'].get('shared_updates',{}).get('changes',[]):
            if change['path']==other:latest_revision=change['revision']
        changed=latest_revision is not None and latest_revision!=read_revision
        if receipt is None or (receipt['period']!=p and (strategy=='exchange' or changed)):
            return action('read',path=other)
        # In delta fixtures, absence of an update after the publisher's turn
        # supports reuse under the authored publication policy, not a world rule.
        chosen,_=allocate({actor:o['client'],peer:receipt['client']},o['reservations'],o['turn_order'])
        room,time,_=chosen[actor]
    else:
        available=options(o['client'],actor,o['reservations'])
        facility=[x for x in available if not x[0].startswith('overflow:')]
        if strategy=='wait_narrow' and o['tick']==0 and len(facility)>1:return yield_tick()
        if strategy=='wait_costly' and o['tick']==0:
            gap=available[1][2]-available[0][2] if len(available)>1 else 10**6
            if gap<=10:return yield_tick()
        available=options(o['client'],actor,o['reservations'])
        if strategy=='fallback':available=[x for x in available if x[0].startswith('overflow:')]
        room,time,_=available[0]
    return action('act',{'on_success':'yield_period','on_rejection':'yield_tick'},resource='/schedule',arguments={
        'operation':'book','appointment_id':app['id'],'room_id':room,'time_id':time})


def scripts(strategy):return {a:[{'__rooms_v2_policy':{'strategy':strategy}}] for a in ('A','B')}
