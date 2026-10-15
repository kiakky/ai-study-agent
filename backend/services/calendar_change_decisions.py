"""Whole-message human decisions for exact displayed changes. Never model callable."""
from contextlib import closing
import hashlib
import json
import re
from database import get_connection
from services.calendar_management import CalendarSessionManagement
from services.planning import PlanningError


def apply_change_decision(user, message_id, text, decision, service=None, reply_review=None):
    service=service or CalendarSessionManagement()
    digest=hashlib.sha256(text.encode()).hexdigest()
    with closing(get_connection()) as db,db:
        db.execute('BEGIN IMMEDIATE')
        receipt=db.execute('SELECT * FROM conversation_tool_effects WHERE telegram_user_id=? AND message_id=? AND ordinal=-2',(user,message_id)).fetchone()
        if receipt:
            if receipt['request_hash']!=digest or not receipt['result_json']:raise PlanningError('This decision message is stale or already used.')
            stored=json.loads(receipt['result_json'])
            if receipt['status']=='completed':return stored['result']
            identifier=stored['change_id']
        else:
            def deny(reason):
                result={'id':'','status':'blocked','review_kind':'change','error':reason}
                db.execute("INSERT INTO conversation_tool_effects (telegram_user_id,message_id,ordinal,request_hash,status,result_json) VALUES (?,?,-2,?,'completed',?)",
                    (user,message_id,digest,json.dumps({'result':result})))
                db.commit()
                raise PlanningError(reason)
            effects=db.execute("SELECT * FROM conversation_tool_effects WHERE telegram_user_id=? AND status='completed' AND ordinal>=0 ORDER BY rowid DESC",(user,)).fetchall()
            latest=None
            for effect in effects:
                value=json.loads(effect['result_json']) if effect['result_json'] else {}
                if value.get('review_kind') in {'change','plan'}:
                    latest=(effect,value);break
            if not latest or latest[1]['review_kind']!='change':
                if decision[0]=='reject_latest' and ' '.join(re.sub(r'[^a-z0-9\s]',' ',text.lower()).split()) in {'actually don t change anything','don t change anything'}:return None
                deny('Review your exact latest session change before deciding; use its buttons if several reviews are active.')
            effect,value=latest
            if not effect['presented']:deny('Review the new exact session change before deciding.')
            identifier=value['change_id']
            row=service.get(user,identifier,db)
            if reply_review is not None and reply_review!=identifier:deny('This reply identifies an older or different review.')
            if decision[1] is not None and row['kind']!=decision[1]:deny('That approval does not match the displayed change.')
            if row['status'] not in {'pending','approved','processing','uncertain','completed'}:deny('This session-change review is no longer available.')
            # Bind the message before external execution. A redelivery may never
            # migrate to a new proposal, even if execution/delivery was interrupted.
            db.execute("INSERT INTO conversation_tool_effects (telegram_user_id,message_id,ordinal,request_hash,status,result_json) VALUES (?,?,-2,?,'pending',?)",
                (user,message_id,digest,json.dumps({'change_id':identifier})))
    row=service.get(user,identifier)
    approve=decision[0]=='approve_change'
    if approve and row['status'] in {'approved','processing','uncertain'}:result=service.execute(user,identifier)
    else:result=service.decide(user,identifier,'approve' if approve else 'reject')
    result={k:v for k,v in result.items() if k in {'id','status','kind','error'}}
    result['review_kind']='change'
    with closing(get_connection()) as db,db:
        db.execute("UPDATE conversation_tool_effects SET status='completed',result_json=? WHERE telegram_user_id=? AND message_id=? AND ordinal=-2",
            (json.dumps({'change_id':identifier,'result':result}),user,message_id))
        db.execute('UPDATE conversation_tool_effects SET decided=1 WHERE telegram_user_id=? AND ordinal>=0 AND result_json IS NOT NULL AND json_extract(result_json,\'$.change_id\')=?',(user,identifier))
    return result
