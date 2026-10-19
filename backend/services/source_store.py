"""One owner-scoped source/revision store; no semantic examination fact store."""
from contextlib import closing
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

from database import get_connection
from models.sources import SourceMetadata
from models.exams import Evidence
from services.document_extraction import MAX_FILE_BYTES


class LMSAccessDenied(ValueError):
    pass


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(value):return sha256(canonical(value).encode()).hexdigest()


class SourceStore:
    def __init__(self,user_id,owner_id=None,cache_root=None,clock=None):
        self.user_id=user_id
        self.owner_id=owner_id if owner_id is not None else int(os.getenv('TELEGRAM_OWNER_ID','0'))
        self.cache_root=Path(cache_root) if cache_root else Path(__file__).resolve().parents[2]/'.private-lms'
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    def require_owner(self):
        if type(self.user_id) is not int or self.user_id<=0 or self.user_id!=self.owner_id:
            raise LMSAccessDenied('Brightspace is unavailable for this account; general study remains available.')

    def bind_authorized_developer_session(self,account_reference=None):
        """Administrative operation only after explicit owner authentication approval.

        Never exposed as a model/UI tool. No SSO bypass or credential handling.
        A different school account must use a new verified connection, not rebind.
        """
        self.require_owner()
        with closing(get_connection()) as db,db:
            exists=db.execute("SELECT id FROM lms_connections WHERE session_binding='developer_session'").fetchone()
            if exists:raise LMSAccessDenied('Existing binding needs explicit review; it is not silently replaced.')
            db.execute('INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)',(self.user_id,))
            identifier=str(uuid4())
            db.execute('INSERT INTO lms_connections (id,telegram_user_id,session_binding,account_reference) VALUES (?,?,?,?)',(identifier,self.user_id,'developer_session',account_reference))
        return identifier

    def connection(self):
        self.require_owner()
        with closing(get_connection()) as db:
            row=db.execute("SELECT id FROM lms_connections WHERE telegram_user_id=? AND session_binding='developer_session' AND provider='brightspace' AND status='authorized'",(self.user_id,)).fetchone()
        if not row:raise LMSAccessDenied('Brightspace connection has not been explicitly bound or is disconnected.')
        return row['id']

    def save_course(self,course):
        connection=self.connection();value=canonical(course)
        with closing(get_connection()) as db,db:
            previous=db.execute('SELECT metadata_hash FROM lms_courses WHERE connection_id=? AND course_id=?',(connection,course['course_id'])).fetchone()
            db.execute('INSERT INTO lms_courses (connection_id,course_id,name,activity,metadata_json,metadata_hash,checked_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(connection_id,course_id) DO UPDATE SET name=excluded.name,activity=excluded.activity,metadata_json=excluded.metadata_json,metadata_hash=excluded.metadata_hash,checked_at=excluded.checked_at',
                       (connection,course['course_id'],course['name'],course['activity'],value,digest(course),self.clock().isoformat()))
        return 'new' if previous is None else ('changed' if previous[0]!=digest(course) else 'unchanged')

    def courses(self):
        connection=self.connection()
        with closing(get_connection()) as db:return [json.loads(row[0]) for row in db.execute('SELECT metadata_json FROM lms_courses WHERE connection_id=? ORDER BY course_id',(connection,))]

    def require_course(self,course_id):
        if not any(course['course_id']==course_id for course in self.courses()):raise LookupError('Course is not indexed for this authorized connection.')

    def source(self,source_id):
        connection=self.connection()
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM lms_sources WHERE id=? AND connection_id=?',(str(source_id),connection)).fetchone()
        if not row:raise LookupError('Source not found for this connection.')
        return row

    def sources(self,course_id):
        self.require_course(course_id);connection=self.connection()
        with closing(get_connection()) as db:
            return [{'source_id':row['id'],**json.loads(row['metadata_json'])} for row in db.execute('SELECT * FROM lms_sources WHERE connection_id=? AND course_id=? ORDER BY resource_key',(connection,course_id))]

    def index(self,key,metadata,payload=None):
        connection=self.connection();data=metadata.model_dump(mode='json');hashed=digest(data);now=self.clock().isoformat()
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM lms_sources WHERE connection_id=? AND resource_key=?',(connection,key)).fetchone()
            identifier=old['id'] if old else str(uuid4())
            db.execute('INSERT INTO lms_sources (id,connection_id,course_id,resource_key,metadata_json,metadata_hash,checked_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(connection_id,resource_key) DO UPDATE SET metadata_json=excluded.metadata_json,metadata_hash=excluded.metadata_hash,checked_at=excluded.checked_at',
                       (identifier,connection,metadata.course_id,key,canonical(data),hashed,now))
            payload_changed=False
            if payload is not None:
                prior=db.execute('SELECT id,metadata_snapshot_json,original_payload_json FROM lms_source_revisions WHERE source_id=? AND original_payload_json IS NOT NULL ORDER BY rowid DESC LIMIT 1',(identifier,)).fetchone()
                payload_changed=prior is None or prior['metadata_snapshot_json']!=canonical(data) or prior['original_payload_json']!=canonical(payload)
                if payload_changed:
                    # A source can revert to earlier bytes; preserve that transition too.
                    version=digest({'metadata':data,'payload':payload,'previous_revision':prior['id'] if prior else None})
                    db.execute('INSERT INTO lms_source_revisions (id,source_id,version_hash,metadata_snapshot_json,original_payload_json,retrieved_at) VALUES (?,?,?,?,?,?)',(str(uuid4()),identifier,version,canonical(data),canonical(payload),now))
        change='new' if not old else ('changed' if old['metadata_hash']!=hashed or payload_changed else 'unchanged')
        return {'source_id':identifier,'change':change}

    def latest(self,source_id,revision_id=None):
        self.source(source_id)
        with closing(get_connection()) as db:
            if revision_id:
                row=db.execute('SELECT * FROM lms_source_revisions WHERE id=? AND source_id=?',(str(revision_id),str(source_id))).fetchone()
            else:
                row=db.execute('SELECT * FROM lms_source_revisions WHERE source_id=? ORDER BY retrieved_at DESC,rowid DESC LIMIT 1',(str(source_id),)).fetchone()
        return row

    def cache_file(self,value):
        connection=self.connection()
        if len(value)>MAX_FILE_BYTES:raise ValueError('Document exceeds storage limit.')
        folder=self.cache_root.resolve()/connection;folder.mkdir(parents=True,exist_ok=True)
        if os.name=='nt':
            # Protect this project-private cache only; never change system ACLs.
            account=os.environ.get('USERDOMAIN','')+'\\'+os.environ.get('USERNAME','')
            result=subprocess.run(['icacls.exe',str(folder),'/inheritance:r','/grant:r',account+':(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True,check=False)
            if result.returncode:raise LMSAccessDenied('Private cache permissions could not be established.')
        else:folder.chmod(0o700)
        target=folder/(sha256(value).hexdigest()+'.bin')
        if target.exists() and sha256(target.read_bytes()).hexdigest()!=sha256(value).hexdigest():
            # Retain an incomplete/corrupt earlier copy rather than overwrite it.
            target=folder/(sha256(value).hexdigest()+'-recovery-'+str(uuid4())+'.bin')
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=folder,suffix='.part',delete=False) as file:
                temporary=Path(file.name);file.write(value);file.flush();os.fsync(file.fileno())
            try:
                # Atomic publication; concurrent copies have the exact same hash.
                os.replace(temporary,target)
                if os.name!='nt':target.chmod(0o600)
            finally:
                if temporary.exists():temporary.unlink()
        return str(target.relative_to(self.cache_root.resolve()))

    def extracted(self,source_id,value,document,retain_original=True):
        row=self.source(source_id)
        reference=self.cache_file(value) if retain_original else None
        data=document.model_dump(mode='json');metadata=json.loads(row['metadata_json'])
        payload_revision=self.latest_payload(source_id)
        version=digest({'metadata':metadata,'content':document.original_sha256,'extraction':data,'original_file_reference':reference,'payload_revision':payload_revision['id'] if payload_revision else None})
        with closing(get_connection()) as db,db:
            db.execute('INSERT OR IGNORE INTO lms_source_revisions (id,source_id,version_hash,metadata_snapshot_json,extraction_json,original_file_reference,content_hash,retrieved_at) VALUES (?,?,?,?,?,?,?,?)',
                       (str(uuid4()),str(source_id),version,canonical(metadata),canonical(data),reference,document.original_sha256,self.clock().isoformat()))
        with closing(get_connection()) as db:
            return db.execute('SELECT * FROM lms_source_revisions WHERE source_id=? AND version_hash=?',(str(source_id),version)).fetchone()

    def evidence(self,source_id,revision_id=None,locator=None):
        source=self.source(source_id);revision=self.latest(source_id,revision_id)
        if not revision:raise LookupError('Source revision not found.')
        metadata=json.loads(revision['metadata_snapshot_json']);extraction=json.loads(revision['extraction_json']) if revision['extraction_json'] else None
        segments=extraction['segments'] if extraction else []
        if locator:
            segments=[segment for segment in segments if segment['locator']==locator]
            if not segments:raise LookupError('Source section not found.')
        text='\n\n'.join(segment['cleaned_text'] for segment in segments) if extraction else (revision['original_payload_json'] or '')
        def aware_timestamp(value):
            if not isinstance(value,str):return None
            try:
                parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                return parsed if parsed.tzinfo else None
            except ValueError:return None
        evidence=Evidence(source_reference=f"brightspace:{source_id}:{revision['id']}",excerpt=text[:4000],retrieved_at=revision['retrieved_at'],published_at=aware_timestamp(metadata['created_at']),updated_at=aware_timestamp(metadata['modified_at']),course_id=metadata['course_id'],source_id=str(source_id),resource_id=metadata['resource_id'],revision_id=revision['id'],locator=locator,content_hash=revision['content_hash'],extraction_warnings=(extraction['warnings']+[warning for segment in segments for warning in segment['warnings']]) if extraction else ['Metadata/source payload has not been semantically interpreted.'])
        return {'evidence':evidence.model_dump(mode='json'),'metadata':metadata,'segments':segments,
                'original_payload':json.loads(revision['original_payload_json']) if revision['original_payload_json'] else None,
                'original_text':extraction['original_text'] if extraction else None,'extraction_status':extraction['status'] if extraction else 'metadata_only',
                'excerpt_is_partial':len(text)>4000,'original_file_available':bool(revision['original_file_reference'])}

    def latest_payload(self,source_id):
        self.source(source_id)
        with closing(get_connection()) as db:
            return db.execute('SELECT * FROM lms_source_revisions WHERE source_id=? AND original_payload_json IS NOT NULL ORDER BY rowid DESC LIMIT 1',(str(source_id),)).fetchone()

    def cached_document(self,source_id):
        source=self.source(source_id)
        with closing(get_connection()) as db:
            rows=db.execute('SELECT r.* FROM lms_source_revisions r WHERE r.source_id=? AND r.extraction_json IS NOT NULL AND NOT EXISTS (SELECT 1 FROM lms_source_revisions p WHERE p.source_id=r.source_id AND p.original_payload_json IS NOT NULL AND p.rowid>r.rowid) ORDER BY r.retrieved_at DESC,r.rowid DESC',(str(source_id),)).fetchall()
        return next((row for row in rows if digest(json.loads(row['metadata_snapshot_json']))==source['metadata_hash']),None)

    def original_bytes(self,source_id,revision_id):
        source=self.source(source_id);revision=self.latest(source_id,revision_id)
        if not revision or not revision['original_file_reference']:raise LookupError('Original file not available.')
        folder=(self.cache_root.resolve()/source['connection_id']).resolve()
        target=(self.cache_root.resolve()/revision['original_file_reference']).resolve()
        if not target.is_relative_to(folder) or target.suffix!='.bin':raise LMSAccessDenied('Unsafe original file reference.')
        value=target.read_bytes()
        if sha256(value).hexdigest()!=revision['content_hash']:raise LMSAccessDenied('Original-file integrity check failed.')
        return value
