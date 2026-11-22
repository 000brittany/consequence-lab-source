"""Independent arithmetic, capacity, eligibility and event-causation auditor.
Does not import the mixed world, pricing, transaction or mutation helpers.
"""
from copy import deepcopy


def check_world(w):
    money={str(i+1):{'authorized':n,'available':n,'encumbered':0,'spent':0,'expired':0,'closed':False} for i,n in enumerate(w['manifest']['allocations_cents'])}
    escrow={};counts=0
    for i,t in enumerate(w['transactions'],1):
        assert t['sequence']==i and type(t['amount_cents']) is int and t['amount_cents']>=0
        p=t['period'];a=money[p];n=t['amount_cents'];ident=t['appointment_id'];assert not a['closed']
        if t['kind']=='reserve':
            assert ident not in escrow and a['available']>=n
            escrow[ident]=(p,n);a['available']-=n;a['encumbered']+=n
        elif t['kind'] in ('charge','refund'):
            assert escrow.pop(ident)==(p,n)
            a['encumbered']-=n;a['spent' if t['kind']=='charge' else 'available']+=n
        else:
            assert t['kind']=='expire' and not a['encumbered'] and n==a['available'] and t['day']==5*int(p)
            a.update(available=0,expired=n,closed=True)
        assert min(a[x] for x in ('available','encumbered','spent','expired'))>=0
    assert money==w['accounts']
    expected={};cells=set()
    for ident,b in w['reservations'].items():
        r=w['requests'][ident];assert r['status']=='pending' and r['owner']==b['owner']
        assert [b['day'],b['time']] in r['slots'] and b['day']>=w['day']
        if b['room_id'].startswith('external:'):
            assert b['room_id']=='external:'+r['owner'] and r['external_allowed'] and b['resources']==[];cost=7000
        else:
            assert b['room_id'] in r['rooms']
            needed={b['room_id']}|({'technician'} if r['technician'] else set())|set(r['equipment'])
            assert needed<=set(b['resources']);cost=sum(w['manifest']['prices_cents'][k] for k in b['resources'])
        assert cost==b['price_cents']
        for res in ['owner:'+b['owner']]+b['resources']:
            cell=(b['day'],b['time'],res);assert cell not in cells;cells.add(cell)
        expected[ident]=(str((b['day']-1)//5+1),cost)
    assert expected==escrow
    assert sum(a['spent'] for a in money.values())==sum(o['reservation']['price_cents'] for o in w['outcomes'] if o['status']=='completed')
    assert len(w['outcomes'])==len({o['appointment_id'] for o in w['outcomes']})
    for o in w['outcomes']:assert w['requests'][o['appointment_id']]['status']==o['status']
    return len(w['transactions'])


def check_transition(before,after,item):
    """Check event-specific causation independently of source event handlers."""
    b,a=before['world'],after['world'];check_world(a)
    assert b['manifest']==a['manifest']
    assert a['transactions'][:len(b['transactions'])]==b['transactions']
    assert a['outcomes'][:len(b['outcomes'])]==b['outcomes']
    new=a['transactions'][len(b['transactions']):];newout=a['outcomes'][len(b['outcomes']):]
    allowed=set()
    if item['type']=='decision':
        action=item['action'];res=item['result'];actor=item['actor']
        if res['status']=='rejected':assert b==a;return
        resource=action['arguments']['resource'];args=action['arguments']['arguments']
        if resource=='/messages':
            assert a['direct_messages'][:-1]==b['direct_messages'] and a['direct_messages'][-1]['sender']==actor
            assert a['direct_messages'][-1]['text']==args['text'] and a['direct_messages'][-1]['recipient']==args['recipient']
            assert not new and not newout
        elif resource=='/schedule':
            ident=args['appointment_id'];op=args['operation'];r=b['requests'][ident];assert r['owner']==actor
            allowed.add(ident)
            old=b['reservations'].get(ident);now=a['reservations'].get(ident)
            kinds=[t['kind'] for t in new]
            assert kinds==({'book':['reserve'],'hold':['reserve'],'confirm':[],'release':['refund'],'reschedule':['refund','reserve']}[op])
            if op in ('book','hold','reschedule'):
                assert now is not None and all(now[k]==args[k] for k in ('day','time','room_id'))
                assert now['status']==(old['status'] if old and op=='reschedule' else 'held' if op=='hold' else 'confirmed')
                if now['status']=='held':assert now['hold_expires_tick']==(old['hold_expires_tick'] if old else (b['day']-1)*4+b['tick']+8)
            if op in ('book','confirm'):assert r['client_status']=='confirmed'
            if op=='confirm':assert old['status']=='held' and now['status']=='confirmed' and now['hold_expires_tick'] is None
            for t in new:
                ref=old if t['kind']=='refund' else now
                assert t['period']==str((ref['day']-1)//5+1) and t['amount_cents']==ref['price_cents']
        else:assert resource=='/opportunities' and not new and not newout
    else:
        e=item['event'];kind=e['kind'];now=(b['day']-1)*4+b['tick']
        if kind=='potential.event':
            potential=b['manifest']['events'][b['event_cursor']];assert potential['clock']<=now
            assert a['event_cursor']==b['event_cursor']+1
            ident=potential['appointment_id'];allowed.add(ident);r=b['requests'].get(ident)
            if potential['kind']=='arrival':
                original=next(r for r in b['manifest']['requests'] if r['id']==ident)
                assert all(a['requests'][ident][k]==v for k,v in original.items())
            elif r is None or r['status']!='pending':assert a['requests'].get(ident)==r and not newout and not new
            elif potential['kind']=='amend':
                assert all(a['requests'][ident][k]==v for k,v in potential['changes'].items())
                assert all(t['kind']=='refund' for t in new)
            elif potential['kind']=='cancel':assert newout and newout[0]['status']=='cancelled'
            elif potential['kind']=='confirm':assert a['requests'][ident]['client_status']=='confirmed'
        elif kind=='service.pass':
            for o in newout:
                ident=o['appointment_id'];allowed.add(ident)
                if o['status']=='completed':
                    old=b['reservations'][ident];assert old['day']==b['day'] and old['time']==e['time'] and old['status']=='confirmed' and o['reservation']==old
                else:assert max((d,('09:00','14:00').index(t)) for d,t in b['requests'][ident]['slots'])<=(b['day'],('09:00','14:00').index(e['time']))
            assert all(t['kind'] in ('charge','refund') for t in new)
        elif kind=='assurance.check':
            for o in newout:
                ident=o['appointment_id'];allowed.add(ident);r=b['requests'][ident]
                due=r['repair_due'] if r['repair_due'] is not None else r['assurance_due']
                assert due is not None and due<=now and (ident not in b['reservations'] or b['reservations'][ident]['status']!='confirmed')
        elif kind=='hold.expire':
            ident=e['appointment_id'];allowed.add(ident);r=b['reservations'][ident]
            assert r['status']=='held' and (r['hold_expires_tick']<=now or b['phase']=='service' and r['day']==b['day'])
            assert len(new)==1 and new[0]['kind']=='refund'
        elif kind=='account.close':assert len(new)==1 and new[0]['kind']=='expire' and not newout
        else:assert not new and not newout
    for ident,r in b['requests'].items():
        if ident not in allowed:assert a['requests'][ident]==r
