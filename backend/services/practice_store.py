"""Explicitly provisioned per-user practice storage; never initialized on reads."""
from contextlib import closing
from hashlib import sha256
import json
from uuid import uuid4
from database import get_connection
from models.practice import PracticeRequest, MaterialInput

SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_materials (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(telegram_user_id),
 request_key TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
 text TEXT NOT NULL, warnings_json TEXT NOT NULL, content_hash TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,request_key));
CREATE TABLE IF NOT EXISTS practice_jobs (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(telegram_user_id),
 request_key TEXT NOT NULL, request_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','ready','failed')),
 set_id TEXT, error_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(user_id,request_key));
CREATE TABLE IF NOT EXISTS practice_sets (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(telegram_user_id),
 job_id TEXT NOT NULL UNIQUE REFERENCES practice_jobs(id), title TEXT NOT NULL,
 topic TEXT NOT NULL, questions_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS practice_attempts (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(telegram_user_id),
 set_id TEXT NOT NULL REFERENCES practice_sets(id), request_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('in_progress','completed')),
 revision INTEGER NOT NULL DEFAULT 0, answers_json TEXT NOT NULL DEFAULT '{}',
 submission_id TEXT, result_json TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 completed_at TEXT, UNIQUE(user_id,request_key));
CREATE INDEX IF NOT EXISTS practice_jobs_queue ON practice_jobs(status,created_at);
"""


class PracticeConflict(ValueError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def require_actor(user):
    if type(user) is not int or user <= 0:raise ValueError('Authenticated student required.')


class PracticeStore:
    def __init__(self, user):
        require_actor(user);self.user=user

    def add_material(self, title, text, key, kind='notes', warnings=()):
        value=MaterialInput(title=title,text=text)
        digest=sha256(text.encode()).hexdigest()
        with closing(get_connection()) as db,db:
            db.execute('INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)',(self.user,))
            old=db.execute('SELECT * FROM practice_materials WHERE user_id=? AND request_key=?',(self.user,key)).fetchone()
            if old:
                if old['content_hash']!=digest or old['title']!=title:raise PracticeConflict('This material request was already used.')
                return self.material_summary(old)
            identifier=str(uuid4())
            db.execute('INSERT INTO practice_materials (id,user_id,request_key,title,kind,text,warnings_json,content_hash) VALUES (?,?,?,?,?,?,?,?)',
                (identifier,self.user,key,value.title,kind,value.text,encoded(list(warnings)),digest))
            return {'reference':'material:'+identifier,'title':title,'kind':kind,'characters':len(text),'warnings':list(warnings)}

    @staticmethod
    def material_summary(row):
        return {'reference':'material:'+row['id'],'title':row['title'],'kind':row['kind'],'characters':len(row['text']),'warnings':json.loads(row['warnings_json'])}

    def materials(self):
        with closing(get_connection()) as db:
            return [self.material_summary(r) for r in db.execute('SELECT * FROM practice_materials WHERE user_id=? ORDER BY rowid DESC LIMIT 50',(self.user,))]

    def material(self, reference):
        if not reference.startswith('material:'):raise LookupError('Material not found.')
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM practice_materials WHERE id=? AND user_id=?',(reference[9:],self.user)).fetchone()
        if not row:raise LookupError('Material not found.')
        return {**self.material_summary(row),'text':row['text'],'content_hash':row['content_hash']}

    def enqueue(self, request, evidence, key):
        request=PracticeRequest.model_validate(request).model_dump(mode='json')
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM practice_jobs WHERE user_id=? AND request_key=?',(self.user,key)).fetchone()
            if old:
                previous=PracticeRequest.model_validate(json.loads(old['request_json'])).model_dump(mode='json')
                if encoded(previous)!=encoded(request):raise PracticeConflict('This generation request was already used.')
                return self.job_summary(old)
            active=db.execute("SELECT COUNT(*) FROM practice_jobs WHERE user_id=? AND status IN ('queued','running')",(self.user,)).fetchone()[0]
            daily=db.execute("SELECT COUNT(*) FROM practice_jobs WHERE user_id=? AND created_at>=datetime('now','-1 day')",(self.user,)).fetchone()[0]
            if active>=3 or daily>=20:raise PracticeConflict('Practice generation limit reached. Complete pending requests or try later.')
            db.execute('INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)',(self.user,))
            identifier=str(uuid4())
            db.execute("INSERT INTO practice_jobs (id,user_id,request_key,request_json,evidence_json,status) VALUES (?,?,?,?,?,'queued')",
                (identifier,self.user,key,encoded(request),encoded(evidence)))
        return {'id':identifier,'status':'queued','set_id':None,'error_code':None}

    @staticmethod
    def job_summary(row):
        return {k:row[k] for k in ('id','status','set_id','error_code')}

    def job(self, identifier):
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM practice_jobs WHERE id=? AND user_id=?',(identifier,self.user)).fetchone()
        if not row:raise LookupError('Practice request not found.')
        return self.job_summary(row)

    def listing(self):
        with closing(get_connection()) as db:
            sets=[dict(r) for r in db.execute('SELECT id,title,topic,created_at FROM practice_sets WHERE user_id=? ORDER BY rowid DESC LIMIT 50',(self.user,))]
            attempts=[dict(r) for r in db.execute('SELECT id,set_id,status,revision,completed_at,result_json,answers_json FROM practice_attempts WHERE user_id=? ORDER BY rowid DESC LIMIT 100',(self.user,))]
            jobs=[self.job_summary(r) for r in db.execute("SELECT * FROM practice_jobs WHERE user_id=? AND status!='ready' ORDER BY rowid DESC LIMIT 10",(self.user,))]
        for a in attempts:
            a['answered']=len(json.loads(a.pop('answers_json')))
            result=json.loads(a.pop('result_json') or 'null');a['score']=result['score'] if result else None
        scores=[a['score'] for a in attempts if a['score'] is not None]
        return {'sets':sets,'attempts':attempts,'jobs':jobs,'stats':{'completed':len(scores),'average':round(sum(scores)/len(scores),1) if scores else None},'history_limit':100}

    def private_set(self, identifier):
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM practice_sets WHERE id=? AND user_id=?',(identifier,self.user)).fetchone()
        if not row:raise LookupError('Practice not found.')
        return row,json.loads(row['questions_json'])

    def start(self, identifier, key):
        self.private_set(identifier)
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT id,set_id FROM practice_attempts WHERE user_id=? AND request_key=?',(self.user,key)).fetchone()
            if old:
                if old['set_id']!=identifier:raise PracticeConflict('Start request already used.')
                attempt=old['id']
            else:
                attempt=str(uuid4())
                db.execute("INSERT INTO practice_attempts (id,user_id,set_id,request_key,status) VALUES (?,?,?,?,'in_progress')",(attempt,self.user,identifier,key))
        return self.attempt(attempt)

    def attempt(self, identifier):
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM practice_attempts WHERE id=? AND user_id=?',(identifier,self.user)).fetchone()
        if not row:raise LookupError('Attempt not found.')
        if row['status']=='completed':return {'id':row['id'],'status':'completed','result':json.loads(row['result_json'])}
        collection,questions=self.private_set(row['set_id'])
        return {'id':row['id'],'set_id':row['set_id'],'title':collection['title'],'status':row['status'],'revision':row['revision'],
            'answers':json.loads(row['answers_json']),'questions':[{'id':q['id'],'text':q['text'],'options':q['options']} for q in questions]}

    def update(self, identifier, request, submit=False):
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM practice_attempts WHERE id=? AND user_id=?',(identifier,self.user)).fetchone()
            if not row:raise LookupError('Attempt not found.')
            answers=encoded(request.answers)
            if row['status']=='completed':
                if submit and row['submission_id']==str(request.submission_id) and row['answers_json']==answers:return json.loads(row['result_json'])
                raise PracticeConflict('This attempt has already been submitted.')
            if row['revision']!=request.revision:raise PracticeConflict('A newer saved version exists. Reload this attempt before changing it.')
            collection=db.execute('SELECT * FROM practice_sets WHERE id=? AND user_id=?',(row['set_id'],self.user)).fetchone()
            if not collection:raise LookupError('Practice not found.')
            questions=json.loads(collection['questions_json']);ids={q['id'] for q in questions}
            if set(request.answers)-ids:raise ValueError('Unknown question.')
            if submit and set(request.answers)!=ids:raise ValueError('Answer every question before submitting.')
            if not submit:
                db.execute('UPDATE practice_attempts SET answers_json=?,revision=revision+1 WHERE id=? AND user_id=?',(answers,identifier,self.user))
                return {'revision':row['revision']+1,'saved':True}
            feedback=[]
            for q in questions:
                selected=request.answers[q['id']]
                feedback.append({'id':q['id'],'text':q['text'],'options':q['options'],'selected_option':selected,
                    'correct_option':q['correct_option'],'correct':selected==q['correct_option'],
                    'explanation':q['explanations'][selected],'correct_explanation':q['explanations'][q['correct_option']],
                    'citations':q['citations']})
            score=round(100*sum(q['correct'] for q in feedback)/len(feedback),1)
            result={'attempt_id':identifier,'title':collection['title'],'score':score,'feedback':feedback}
            db.execute("UPDATE practice_attempts SET status='completed',answers_json=?,revision=revision+1,submission_id=?,result_json=?,completed_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
                (answers,str(request.submission_id),encoded(result),identifier,self.user))
            return result
