"""Authenticated per-user materials, generation jobs and resumable attempts."""
import asyncio
import sqlite3
from uuid import UUID
from fastapi import Depends, HTTPException, Request, Query
from models.practice import MaterialInput, CreatePractice, StartAttempt, SaveAttempt, SubmitAttempt
from services import practice
from services.practice_store import PracticeStore, PracticeConflict
from services.practice_learning import learning_snapshot
from services.practice_pdf import read_pdf, MAX_BYTES
from services.source_store import LMSAccessDenied


class CreateMaterial(MaterialInput):
    request_id: UUID


def perform(user, action):
    if not practice.enabled():raise HTTPException(503,'Generated practice is not enabled yet.')
    try:return action(PracticeStore(user['id']))
    except LMSAccessDenied:raise HTTPException(403,'This source is not authorized for your account.') from None
    except LookupError:raise HTTPException(404,'Practice or source not found.') from None
    except PracticeConflict as error:raise HTTPException(409,str(error)) from None
    except ValueError:raise HTTPException(422,'Check the material, selected sources, answers and required fields.') from None
    except sqlite3.Error:raise HTTPException(503,'Practice storage is not provisioned or is temporarily unavailable.') from None


def register_practice_routes(app, authenticate):
    @app.get('/api/practice')
    def listing(user=Depends(authenticate)):
        result=perform(user,lambda store:store.listing())
        result['learning']=learning_snapshot(user['id'])
        return result

    @app.get('/api/practice/materials')
    def materials(user=Depends(authenticate)):
        return perform(user,lambda store:store.materials())

    @app.post('/api/practice/materials')
    def material(body:CreateMaterial,user=Depends(authenticate)):
        return perform(user,lambda store:store.add_material(body.title,body.text,'notes:'+str(body.request_id)))

    @app.post('/api/practice/materials/pdf')
    async def pdf(request:Request,request_id:UUID,title:str=Query(min_length=1,max_length=160),user=Depends(authenticate)):
        if not practice.enabled():raise HTTPException(503,'Generated practice is not enabled yet.')
        if request.headers.get('content-type','').split(';')[0]!='application/pdf':raise HTTPException(415,'Upload a PDF only.')
        value=bytearray()
        async for chunk in request.stream():
            if len(value)+len(chunk)>MAX_BYTES:raise HTTPException(413,'PDF limit is 5 MB.')
            value.extend(chunk)
        try:data=await asyncio.to_thread(read_pdf,bytes(value))
        except Exception:raise HTTPException(422,'PDF has no supported readable text, is too complex or exceeded the reading limit. Paste notes instead.') from None
        return perform(user,lambda store:store.add_material(title,data['text'],'pdf:'+str(request_id),'pdf',data['warnings']))

    @app.get('/api/practice/material')
    def read_material(reference:str,offset:int=Query(default=0,ge=0,le=100000),user=Depends(authenticate)):
        def read(store):
            data=store.material(reference);text=data.pop('text');data['text']=text[offset:offset+6000]
            data['next_offset']=offset+6000 if offset+6000<len(text) else None;data['untrusted_source_material']=True
            return data
        return perform(user,read)

    @app.post('/api/practice/jobs')
    def create(body:CreatePractice,user=Depends(authenticate)):
        args=body.model_dump(mode='json');key=args.pop('request_id')
        return perform(user,lambda store:practice.request_practice(user['id'],args,'web:'+key))

    @app.get('/api/practice/jobs/{identifier}')
    def job(identifier:UUID,user=Depends(authenticate)):
        return perform(user,lambda store:store.job(str(identifier)))

    @app.post('/api/practice/sets/{identifier}/attempts')
    def start(identifier:UUID,body:StartAttempt,user=Depends(authenticate)):
        return perform(user,lambda store:store.start(str(identifier),str(body.request_id)))

    @app.get('/api/practice/attempts/{identifier}')
    def attempt(identifier:UUID,user=Depends(authenticate)):
        return perform(user,lambda store:store.attempt(str(identifier)))

    @app.put('/api/practice/attempts/{identifier}')
    def save(identifier:UUID,body:SaveAttempt,user=Depends(authenticate)):
        return perform(user,lambda store:store.update(str(identifier),body))

    @app.post('/api/practice/attempts/{identifier}/submit')
    def submit(identifier:UUID,body:SubmitAttempt,user=Depends(authenticate)):
        return perform(user,lambda store:store.update(str(identifier),body,submit=True))
