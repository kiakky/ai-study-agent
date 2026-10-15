"""Explicit chat decisions are handled by Python, never by an LLM write tool."""
from contextlib import closing
import hashlib,json,re
from database import get_connection
from services.calendar_conversation import CalendarConversation
from services.planning import PlanningError


def explicit_decision(text):
    # Match the entire message. An adjustment plus 'approve' is never approval.
    if text.strip().startswith(('\"','\'', '\u201c','\u2018')):return None
    normalized=re.sub(r'[^a-z0-9\s]',' ',text.lower())
    normalized=' '.join(normalized.split())
    if normalized in {'approve this change','yes approve this change','approve this move','approve this reschedule','approve this cancellation'}:
        kind='cancel' if normalized.endswith('cancellation') else 'reschedule' if normalized.endswith(('move','reschedule')) else None
        return ('approve_change',kind)
    if normalized in {'reject this change','reject this move','reject this cancellation','actually don t change anything','don t change anything'}:
        return ('reject_latest',None)
    if normalized in {'reject this plan','reject the proposal','reject all','no reject all'}:return ('reject',None)
    if normalized in {'looks good add them to my calendar','add these to my calendar','approve this plan','approve the latest proposal','approve all','yes approve all'}:return ('approve',None)
    match=re.fullmatch(r'(?:(?:yes|that looks good|looks good) )?approve all (\d+|one|two|three|four|five|six|seven|eight|nine|ten)',normalized)
    if match:
        words=['one','two','three','four','five','six','seven','eight','nine','ten']
        value=match[1];return ('approve',int(value) if value.isdigit() else words.index(value)+1)
    return None


def apply_decision(user,message_id,text,service=None,reply_review=None,change_service=None):
    decision=explicit_decision(text)
    if decision is None:return None
    if decision[0] in {'approve_change','reject_latest'}:
        from services.calendar_change_decisions import apply_change_decision
        result=apply_change_decision(user,message_id,text,decision,change_service,reply_review)
        if result is not None:return result
        decision=('reject',None)
    service=service or CalendarConversation()
    digest=hashlib.sha256(text.encode()).hexdigest()
    with closing(get_connection()) as db,db:
        db.execute('BEGIN IMMEDIATE')
        receipt=db.execute('SELECT * FROM conversation_tool_effects WHERE telegram_user_id=? AND message_id=? AND ordinal=-1',(user,message_id)).fetchone()
        if receipt:
            if receipt['request_hash']!=digest:raise PlanningError('This message was already used for another decision.')
            if not receipt['result_json']:raise PlanningError('This decision receipt was cleared. Review the current proposal before approving.')
            stored=json.loads(receipt['result_json'])
            if receipt['status']=='completed':return stored['result']
            identifier=stored['review_id']
        else:
            def deny(reason):
                # A redelivered old text must never bind to a newer schedule.
                blocked={'id':'','status':'blocked','error':reason}
                db.execute("INSERT INTO conversation_tool_effects (telegram_user_id,message_id,ordinal,request_hash,status,result_json) VALUES (?,?,-1,?,'completed',?)",
                    (user,message_id,digest,json.dumps({'result':blocked})))
                db.commit()
                raise PlanningError(reason)
            effects=db.execute("SELECT result_json FROM conversation_tool_effects WHERE telegram_user_id=? AND status='completed' AND ordinal>=0 ORDER BY rowid DESC",(user,)).fetchall()
            latest_review=next((value for effect in effects if effect[0] and (value:=json.loads(effect[0])).get('review_kind') in {'change','plan'}),None)
            if latest_review and latest_review['review_kind']=='change':
                deny('The latest displayed review is a session change. Approve or reject that exact change; an older plan cannot be approved by this message.')
            row=db.execute('SELECT * FROM calendar_reviews WHERE telegram_user_id=? ORDER BY rowid DESC LIMIT 1',(user,)).fetchone()
            latest=db.execute('SELECT id FROM study_proposals WHERE telegram_user_id=? ORDER BY rowid DESC LIMIT 1',(user,)).fetchone()
            if (not row or not latest or row['proposal_id']!=latest[0] or not row['presented'] or
                reply_review is not None and reply_review!=row['id']):
                deny('Review the complete latest proposal first, then approve that exact schedule using its buttons or an explicit message.')
            if row['status'] not in {'pending','approved','consent','partial','processing','completed'}:
                deny('This proposal was rejected or revised. Review the latest complete schedule before approving.')
            if decision[0]=='reject' and row['status']!='pending':deny('Only an unapproved proposal can be rejected; manage created sessions separately.')
            data=json.loads(service.actions.proposal(user,row['proposal_id'])['proposal_json'])
            if decision[1] is not None and decision[1]!=len(data['sessions']):deny('That approval count does not match the latest complete proposal. Please review it again.')
            identifier=row['id']
            db.execute("INSERT INTO conversation_tool_effects (telegram_user_id,message_id,ordinal,request_hash,status,result_json) VALUES (?,?,-1,?,'pending',?)",
                (user,message_id,digest,json.dumps({'review_id':identifier})))
    # Existing review/consent/outbox enforce expiry, frozen digest, identity, conflicts
    # and duplicate protection. Interrupted receipts always reuse the same review.
    review=service.get(user,identifier)
    if decision[0]=='approve' and review['status'] in {'approved','partial','processing'}:
        result=service.execute(user,identifier)
    else:result=service.decide(user,identifier,decision[0])
    result={k:v for k,v in result.items() if k in {'id','status','error','authorization_start_url'}}
    with closing(get_connection()) as db,db:
        db.execute("UPDATE conversation_tool_effects SET status='completed',result_json=? WHERE telegram_user_id=? AND message_id=? AND ordinal=-1",
            (json.dumps({'review_id':identifier,'result':result}),user,message_id))
    return result
