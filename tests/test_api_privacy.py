"""Public information routes: no identity, account reads or external calls."""
from html.parser import HTMLParser
from pathlib import Path
import unittest
from unittest.mock import patch
import test_api as fixtures

ROOT=Path(__file__).resolve().parents[1]


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.scripts=0
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.append(dict(attrs).get('href'))
        if tag=='script':self.scripts+=1


class PublicPrivacyTests(unittest.TestCase):
    setUp=fixtures.QuizAPITests.setUp

    def test_home_and_privacy_are_public_static_pages_without_account_reads(self):
        with patch.object(fixtures.api,'get_connection',side_effect=AssertionError('Public page must not read accounts')), \
             patch.object(fixtures.database,'get_connection',side_effect=AssertionError('Public page must not read accounts')):
            for route,file in [('/','index.html'),('/privacy','privacy.html')]:
                for headers in [{},{'X-Telegram-Init-Data':'invalid-synthetic-identity'}]:
                    response=self.client.get(route+'?user_id=101',headers=headers)
                    self.assertEqual(response.status_code,200)
                    self.assertIn('text/html',response.headers['content-type'])
                    self.assertEqual(response.content,(ROOT/'frontend'/file).read_bytes())

    def test_home_link_and_policy_work_without_javascript(self):
        home=self.client.get('/');policy=self.client.get('/privacy')
        links=Links();links.feed(home.text)
        self.assertIn('/privacy',links.links)
        self.assertIn('https://t.me/AIStudyAgentTPBot',links.links)
        self.assertIn('Settings',home.text);self.assertIn('Connections',home.text)
        self.assertIn('About AI Study Agent',home.text)
        self.assertIn('Google Calendar access checks your availability',home.text)
        links=Links();links.feed(policy.text)
        self.assertEqual(links.scripts,0);self.assertIn('/',links.links)
        self.assertIn('https://t.me/AIStudyAgentTPBot',links.links)
        self.assertIn('Settings',policy.text);self.assertIn('Connections',policy.text)
        self.assertIn('mailto:1638ngshikai@gmail.com',links.links)
        self.assertIn('request deletion of all application records',policy.text)
        self.assertIn('Do not send passwords',policy.text)
        import re
        self.assertEqual(set(re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',policy.text)),{'1638ngshikai@gmail.com'})

    def test_static_information_cannot_expose_an_authenticated_students_data(self):
        from services import profiles
        from models.preferences import StudyPreferences
        secret='SYNTHETIC_PRIVATE_PREFERENCE_NOT_FOR_PUBLIC_PAGES'
        profiles.ensure_profile(101)
        profiles.save_preferences(101,StudyPreferences(notes=secret))
        for route in ['/','/privacy']:
            for headers in [{},self.headers,self.other]:
                response=self.client.get(route,headers=headers)
                self.assertNotIn(secret,response.text)
                self.assertEqual(response.headers['cache-control'],'no-store')
                self.assertEqual(response.headers['x-content-type-options'],'nosniff')
                self.assertIn("default-src 'self'",response.headers['content-security-policy'])
        for route in ['/api/me','/api/profile','/api/grades','/api/studyplan']:
            self.assertEqual(self.client.get(route).status_code,401)
        self.assertEqual(self.client.post('/api/integrations/google/connect').status_code,401)

    def test_new_public_route_does_not_expand_file_or_host_access(self):
        for route in ['/.env','/database/study_agent.db','/backend/credentials/google_multiuser_client.json',
                      '/static/privacy.html','/privacy/.env','/docs','/openapi.json']:
            self.assertEqual(self.client.get(route).status_code,404)
        self.assertEqual(self.client.get('/privacy',headers={'Host':'unapproved.example'}).status_code,400)


if __name__=='__main__':unittest.main()
