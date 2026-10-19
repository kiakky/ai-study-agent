"""Layout-heavy synthetic sources; never use production source contents."""
import unittest
from unittest.mock import patch
from quiz_refinement_fixture import Fixture,NOTES
from services.learning_text import source_text
from services import practice
from services.practice_native import operation
from services.llm_contracts import LLMError
from models.practice import EvidenceSelection


class ReadableViewTests(unittest.TestCase):
    def test_preparation_diagnostic_has_only_constant_stage_and_category(self):
        with self.assertLogs('services.practice',level='WARNING') as recorded:
            with self.assertRaises(ValueError):
                with practice.preparation_stage('source_evidence'):
                    raise ValueError('synthetic private text must not be logged')
        self.assertIn('source_evidence; ValueError',recorded.output[0])
        self.assertNotIn('synthetic private text',recorded.output[0])

    def test_page_padding_is_removed_but_code_and_columns_are_preserved(self):
        original='\n'*30+'       Queue notes     \n\n\n       if ready:\n           enqueue(item)   \n       Name      Meaning\n       FIFO      Oldest first\n'+' '*3000
        result=source_text([{'cleaned_text':original}])
        self.assertEqual(result,'Queue notes\n\nif ready:\n    enqueue(item)\nName      Meaning\nFIFO      Oldest first')
        self.assertEqual(' '.join(result.split()),' '.join(original.split()))

    def test_blank_pages_and_short_titles_are_retained_correctly(self):
        self.assertEqual(source_text([{'cleaned_text':' '*6000},{'cleaned_text':'  W3\n\n\n  FIFO  '}]),'W3\n\nFIFO')


class ReadPreparationTests(Fixture):
    def seed_padded_notes(self):
        original=self.backend.request
        import json
        async def request(name,**args):
            if name=='download_file':
                text='\n'*2670+'    '+NOTES.replace('. ','.\n    ')+'\n'+' '*7000
                return {'content':[{'type':'text','text':json.dumps({'mode':'inline','representation':'extracted_text','mimeType':'application/pdf','truncated':False})},
                    {'type':'text','text':text}]}
            return await original(name,**args)
        self.backend.request=request
        self.facade.run('brightspace_courses',{})
        rows=self.facade.run('brightspace_materials',{'course_id':'501'})['materials']
        return next(row for row in rows if 'learning' in row['title'])['source_id']

    def test_reader_and_preparation_share_coordinates_without_mutating_revision(self):
        source=self.seed_padded_notes()
        read=self.facade.run('brightspace_read',{'source_id':source})
        self.assertIn('Enqueue adds',read['text'])
        self.assertIsNone(read['next_offset'])
        evidence=practice.evidence_for(101,[EvidenceSelection(reference=read['citation'],offset=0,length=100)])
        self.assertEqual(evidence[0]['text'],read['text'][:100])
        _,_,revision=read['citation'].split(':')
        stored=self.store.evidence(source,revision)
        original=stored['segments'][0]['cleaned_text']
        self.assertGreater(len(original)-len(original.lstrip()),2000)
        self.assertIn(' '.join(evidence[0]['text'].split()),' '.join(original.split()))

    def test_sparse_original_was_an_empty_short_selection_but_readable_view_is_not(self):
        source=self.seed_padded_notes()
        read=self.facade.run('brightspace_read',{'source_id':source})
        _,_,revision=read['citation'].split(':')
        original='\n\n'.join(s['cleaned_text'] for s in self.store.evidence(source,revision)['segments'])
        self.assertEqual(original[:2000].strip(),'')
        evidence=practice.evidence_for(101,[EvidenceSelection(reference=read['citation'],length=2000)])
        self.assertGreater(len(evidence[0]['text'].strip()),30)

    def test_empty_selection_has_specific_recovery_not_generic_upload_error(self):
        result=operation(101,8101,'Make practice','prepare_practice',{},provider=None)
        self.assertEqual(result['error_code'],'practice_unavailable')  # Other failures keep existing behavior.
        with patch('services.practice_native.request_practice',side_effect=practice.PracticeEvidenceError('private detail')):
            result=operation(101,8102,'Make practice','prepare_practice',{})
        self.assertEqual(result['error_code'],'practice_sources_incomplete')
        self.assertNotIn('private detail',str(result))
        with patch('services.practice_native.request_practice',side_effect=LLMError('inference_uncertain')):
            with self.assertRaises(LLMError):operation(101,8103,'Make practice','prepare_practice',{})
