"""Synthetic regression tests for completion, time and factual progress."""
import json
from unittest.mock import patch
from quiz_refinement_fixture import Fixture, NOTES
import test_calendar_native as native
from services.llm_contracts import ChatReply, ToolCall, LLMError
from services.native_progress import ToolProgress, TurnDeadline
from services.practice_store import PracticeStore


class CompletionTests(Fixture):
    def test_more_than_eight_rounds_and_fifteen_operations_can_complete(self):
        store=PracticeStore(101)
        for i in range(40):store.add_material('Synthetic notes '+str(i),NOTES,'many-'+str(i))
        model=native.ScriptedModel([native.call('study_material',operation='list',offset=i)
            for i in range(17)]+[ChatReply('Your saved notes are available.'),
            native.call('check_practice_delivery',outcome='conversation')])
        result=self.service(model).respond(101,9001,'Show my saved note catalog.')
        self.assertEqual(result['status'],'completed')
        primary=[r for r in model.requests if 'Authoritative local calendar arithmetic' in r[0]['content']]
        self.assertEqual(len(primary),18)
        self.assertEqual(store.listing()['jobs'],[])

    def test_new_evidence_allows_another_completion_check_and_preparation(self):
        item=PracticeStore(101).add_material('Queue notes',NOTES,'recover')
        class Model(native.ScriptedModel):
            def complete(self,messages,**options):
                if len(options.get('tools',[]))>1:
                    self_test.assertNotEqual(options.get('tool_choice'),'required')
                return super().complete(messages,**options)
        self_test=self
        model=Model([ChatReply('I can make that quiz.'),
            native.call('check_practice_delivery',outcome='practice'),
            native.call('study_material',operation='list'),
            ChatReply('I found your notes.'),
            native.call('check_practice_delivery',outcome='practice'),
            native.call('study_material',operation='read',reference=item['reference']),
            native.call('prepare_practice',title='Queues',topic='Queue operations',
                sources=[{'reference':item['reference']}],question_count=3)])
        result=self.service(model).respond(101,9002,'Make three questions from my saved queue notes now.')
        self.assertIn('preparing',result['text'])
        self.assertEqual(len(PracticeStore(101).listing()['jobs']),1)

    def test_prose_without_new_information_stops_instead_of_looping(self):
        model=native.ScriptedModel([ChatReply('I will prepare a quiz.'),
            native.call('check_practice_delivery',outcome='practice'),
            ChatReply('I will prepare a quiz.')])
        with self.assertRaises(LLMError) as error:
            self.service(model).respond(101,9003,'Make a quiz from my course notes.')
        self.assertEqual(error.exception.code,'practice_delivery')
        self.assertEqual(PracticeStore(101).listing()['jobs'],[])

    def test_repeat_smaller_excerpt_is_not_new_information(self):
        tracker=ToolProgress()
        def call(length):return ToolCall(id='x',name='brightspace_read',arguments={'source_id':'owned','limit':length})
        tracker.record(call(1000),{'citation':'owned:revision','offset':0,'text':'x'*1000})
        self.assertFalse(tracker.record(call(500),{'citation':'owned:revision','offset':0,'text':'x'*500}))
        with self.assertRaises(LLMError) as error:
            tracker.record(call(800),{'citation':'owned:revision','offset':0,'text':'x'*800})
        self.assertEqual(error.exception.code,'tool_loop')

    def test_new_document_range_is_progress(self):
        tracker=ToolProgress()
        for offset in (0,1000,2000):
            call=ToolCall(id='x',name='brightspace_read',arguments={'source_id':'owned','offset':offset})
            self.assertTrue(tracker.record(call,{'citation':'owned:revision','offset':offset,'text':'x'*1000}))
        self.assertEqual(tracker.version,3)

    def test_expired_model_reply_cannot_save_a_job(self):
        material=PracticeStore(101).add_material('Queues',NOTES,'deadline')
        ticks=[0]
        class Slow(native.ScriptedModel):
            def complete(self,*args,**options):
                reply=super().complete(*args,**options)
                ticks[0]=91
                return reply
        model=Slow([native.call('prepare_practice',title='Queues',topic='Queues',
            sources=[{'reference':material['reference']}])])
        with patch('services.native_progress.time.monotonic',side_effect=lambda:ticks[0]):
            with self.assertRaises(LLMError) as error:
                self.service(model).respond(101,9004,'Make a queue quiz.')
        self.assertEqual(error.exception.code,'timeout')
        self.assertEqual(PracticeStore(101).listing()['jobs'],[])

    def test_snapshot_rechecks_owner_and_preserves_parent_context(self):
        self.backend.content={'contentTree':[{'id':900,'title':'Week four', 'children':[
            {'id':90,'topicId':90,'topicType':'file','title':'Workbook.pdf'}]}]}
        self.facade.run('brightspace_courses',{})
        first=self.facade.run('brightspace_materials',{'course_id':'501'})
        workbook=next(row for row in first['materials'] if row['title']=='Workbook.pdf')
        self.assertEqual(workbook['parent_title'],'Week four')
        self.facade.run('brightspace_materials',{'course_id':'501','offset':0})
        self.assertEqual(sum(n=='get_course_content' for n,a in self.backend.calls),1)
        from contextlib import closing
        import database
        with closing(database.get_connection()) as db,db:
            db.execute("UPDATE lms_connections SET status='disconnected'")
        result=self.facade.run('brightspace_materials',{'course_id':'501'})
        self.assertEqual(result['error_code'],'brightspace_access')

    def test_partial_read_is_not_reported_as_exhausted(self):
        self.facade.run('brightspace_courses',{})
        rows=self.facade.run('brightspace_materials',{'course_id':'501'})['materials']
        agent=self.agent()
        def operate(name,**args):
            call=agent.prepared_call(ToolCall(id='x',name=name,arguments=args))
            return agent.operation(call,1)
        read=operate('brightspace_read',source_id=rows[0]['source_id'],limit=100)
        with patch('services.practice_native.request_practice',return_value={'missing_material':'Read learning explanations.'}):
            result=operate('prepare_practice',title='Quiz',topic='Queues',sources=[{'reference':read['citation']}])
        self.assertNotIn('returned_sources_exhausted',result)
        self.assertTrue(result['unread_document_chunks'])
