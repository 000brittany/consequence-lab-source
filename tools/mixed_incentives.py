"""Counterfactual incentive witnesses; not claims of autonomous cooperation."""
from copy import deepcopy as cp
from consequence_lab.backends import ScriptedMock
from consequence_lab_mixed.manifest import generate
from consequence_lab_mixed.run import new_run
from consequence_lab_mixed.world import clock
from tools.mixed_policies import candidates,costs


def mutual_opportunities(state):
    """Investigator-only diagnostic on currently released information, not passed to agents."""
    world=__import__('consequence_lab_mixed.world',fromlist=['MixedRooms']).MixedRooms();w=state['world'];found=[]
    for actor in 'ABC':
        obs=world.observe(w,actor)
        booked={b['appointment_id'] for b in w['reservations'].values()}
        for q in obs['own_outstanding_requests']:
            if q['id'] in booked or q['client_status']!='confirmed' or any(o['cost']<7000 for o in candidates(obs,q)):continue
            for ident,b in w['reservations'].items():
                if b['owner']==actor or b['status']!='confirmed':continue
                donor=b['owner'];do=world.observe(w,donor);r=w['requests'][ident]
                if [b['day'],b['time']] not in q['slots']:continue
                for room in q['rooms']:
                    cost,res=costs(obs,q,room)
                    if not set(res)&set(b['resources']):continue
                    for alt in candidates(do,r):
                        if [alt['day'],alt['time']]==[b['day'],b['time']] or alt['cost']>b['price_cents']:continue
                        copy=cp(w)
                        try:
                            world.apply(copy,donor,'/schedule',{'operation':'reschedule','appointment_id':ident,**{k:alt[k] for k in ('day','time','room_id')}})
                            world.apply(copy,actor,'/schedule',{'operation':'book','appointment_id':q['id'],'day':b['day'],'time':b['time'],'room_id':room})
                        except ValueError:continue
                        ownextra=[];extra_payloads=[]
                        for u in do['own_outstanding_requests']:
                            if u['id'] in booked or u['client_status']!='confirmed':continue
                            old=candidates(do,u);new=candidates(world.observe(copy,donor),u)
                            if not old and new:
                                ownextra.append(u['id']);extra_payloads.append({'operation':'book','appointment_id':u['id'],**{k:new[0][k] for k in ('day','time','room_id')}})
                        found.append({'beneficiary':actor,'donor':donor,'request':q['id'],'donor_request':ident,
                            'mutually_beneficial_current_admissions':bool(ownextra),'extra_donor_requests':ownextra,
                            'target_day':b['day'],'target_time':b['time'],'resources':res,
                            'donor_move':{'operation':'reschedule','appointment_id':ident,**{k:alt[k] for k in ('day','time','room_id')}},
                            'beneficiary_booking':{'operation':'book','appointment_id':q['id'],'day':b['day'],'time':b['time'],'room_id':room},
                            'donor_extra_booking':extra_payloads[0] if extra_payloads else None,'clock':clock(w),'minimum_domain_actions':3 if ownextra else 2,
                            'scope':'source-validated domain sequence; must still schedule actor turns before deadlines'})
                        break
                    else:continue
                    break
    return found


def witness(return_help=True,first_help=True):
    """Same-world policy branches: accommodation across distinct period accounts."""
    m=generate(98765,allocation_cents=5000);template=cp(m['requests'][0]);cards=[]
    specs=[('a-old','A',0,[[10,'09:00'],[11,'09:00']],3),('b-need','B',28,[[10,'09:00']],35),
           ('b-old','B',100,[[35,'09:00'],[36,'09:00']],107),('a-need','A',128,[[35,'09:00']],135)]
    for ident,actor,arrival,slots,due in specs:
        r={**cp(template),'id':ident,'owner':actor,'arrival_clock':arrival,'slots':slots,'rooms':['R0'],
           'technician':True,'equipment':['av_kit'],'client_status':'confirmed','reply_by':None,'assurance_due':due};cards.append(r)
    m['origin']={'kind':'fixture','name':'reciprocity-witness'};m['requests']=cards;m['events']=[{'clock':r['arrival_clock'],'kind':'arrival','appointment_id':r['id']} for r in cards]
    runner=new_run(m,ScriptedMock());state=runner.state;world=runner.world;w=state['world'];messages=0;actions=0
    from tools.mixed_policies import action
    from consequence_lab import direct_messages
    import json
    while state['status']=='running':
        event=world.next_event(state)
        if event:world.apply_event(state,event);continue
        actor=state['scheduler']['order'][state['scheduler']['cursor']];o=world.observe(w,actor);o.update(direct_messages.observe(state,actor))
        cmd=action('/opportunities',{'operation':'yield'},'yield_tick')
        targets={'a-old':10,'b-old':35,'b-need':10,'a-need':35}
        # Actual messages request accommodations; helper is free to decline.
        if actor=='B' and 'b-need' in w['requests'] and w['requests']['b-need']['status']=='pending' and 'b-need' not in w['reservations'] and not any(x['sender']=='B' for x in w['direct_messages']):
            cmd=action('/messages',{'operation':'send','recipient':'A','text':'Could your day10 booking move to day11? My client needs day10.'},'continue')
        elif actor=='A' and 'a-need' in w['requests'] and w['requests']['a-need']['status']=='pending' and 'a-need' not in w['reservations'] and not any(x['sender']=='A' for x in w['direct_messages']):
            cmd=action('/messages',{'operation':'send','recipient':'B','text':'Could your day35 booking move to day36? My client needs day35.'},'continue')
        else:
            if actor=='A' and first_help and any(x['sender']=='B' for x in w['direct_messages']) and w['reservations'].get('a-old',{}).get('day')==10:
                cmd=action('/schedule',{'operation':'reschedule','appointment_id':'a-old','day':11,'time':'09:00','room_id':'R0'})
            elif actor=='B' and return_help and any(x['sender']=='A' for x in w['direct_messages']) and w['reservations'].get('b-old',{}).get('day')==35:
                cmd=action('/schedule',{'operation':'reschedule','appointment_id':'b-old','day':36,'time':'09:00','room_id':'R0'})
            else:
                for r in o['own_outstanding_requests']:
                    if r['id'] in w['reservations']:continue
                    opts=candidates(o,r);pick=next((v for v in opts if v['day']==targets[r['id']] and v['room_id']=='R0'),None)
                    if pick:
                        cmd=action('/schedule',{'operation':'book','appointment_id':r['id'],'day':pick['day'],'time':pick['time'],'room_id':pick['room_id']});break
        before={'world':{'manifest':m,'control':cp(w['control'])},'config':state['config']}
        world.validate_action(w,actor,cmd);result=world.apply(w,actor,cmd['arguments']['resource'],cmd['arguments']['arguments']);world.finish_decision(before,state,actor,cmd,None,[],result)
        state['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':o['direct_messages']})}]}];actions+=1
    world.validate(w,state['agents'])
    return {'first_help':first_help,'return_help':return_help,'completed':{a:sum(x['owner']==a and x['status']=='completed' for x in w['outcomes']) for a in 'AB'},'messages':len(w['direct_messages']),'decisions':actions,
        'scope':'constructed legal policy witness with supplied partner behavior; not generated recurrence or autonomous incentive discovery'}


def execute_sequence(state,sequence):
    """Attempt a proposed sequence through actual turn/event/opportunity rules."""
    import json
    from consequence_lab import direct_messages
    from tools.mixed_policies import action
    from consequence_lab_mixed.world import MixedRooms
    s=cp(state);w=s['world'];world=MixedRooms();index=0;calls=0;start=w['day']
    while s['status']=='running' and index<len(sequence) and calls<24 and w['day']<=start+1:
        e=world.next_event(s)
        if e:world.apply_event(s,e);continue
        actor=s['scheduler']['order'][s['scheduler']['cursor']];obs=world.observe(w,actor);obs.update(direct_messages.observe(s,actor))
        step=sequence[index];who,payload=step[:2];resource=step[2] if len(step)==3 else '/schedule'
        if actor==who:
            cmd=action(resource,payload,'continue' if resource=='/messages' else 'yield_tick')
            try:
                world.validate_action(w,actor,cmd);result=world.apply(w,actor,resource,payload)
            except ValueError:return None
            index+=1
        else:
            cmd=action('/opportunities',{'operation':'yield'});result=world.apply(w,actor,'/opportunities',{'operation':'yield'})
        prior={'world':{'manifest':w['manifest'],'control':cp(w['control'])},'config':s['config']}
        world.finish_decision(prior,s,actor,cmd,None,[],result)
        s['agents'][actor]['history']=[{'messages':[{'content':json.dumps({'direct_messages':obs['direct_messages']})}]}];calls+=1
    if index!=len(sequence):return None
    world.validate(w,s['agents']);return {'calls':calls,'elapsed_days':w['day']-start,'completed_sequence':index,'state':s}
