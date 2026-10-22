"""Headless Edge QA following browser_check.py; every request uses TestClient.

No server, open port, Telegram session, real model, production DB or network.
"""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
from browser_check import browser_temp_directory
from test_practice import PracticeTests,Model,NOTES
from test_api import signed_data
from services import practice

ROOT=Path(__file__).resolve().parents[1]


def main():
    fixture=PracticeTests();fixture.setUp()
    try:
        with browser_temp_directory() as temp:
            previous={k:os.environ.get(k) for k in ('TEMP','TMP')};os.environ.update(TEMP=temp,TMP=temp)
            try:
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='msedge',headless=True)
                    context=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=1)
                    context.add_init_script('window.Telegram={WebApp:{initData:'+json.dumps(signed_data(101))+',ready(){}}};')
                    state={'lose_submit':False,'submissions':0};errors=[]
                    def route(request_route):
                        req=request_route.request;parsed=urlsplit(req.url)
                        if parsed.hostname!='localhost':
                            if parsed.hostname=='telegram.org':request_route.fulfill(status=200,content_type='application/javascript',body='');return
                            request_route.abort();return
                        path=parsed.path+('?' + parsed.query if parsed.query else '')
                        response=fixture.client.request(req.method,path,headers=req.headers,content=req.post_data_buffer)
                        if req.method=='POST' and parsed.path=='/api/practice/jobs' and response.status_code==200:
                            practice.run_one(lambda:Model())
                        if req.method=='POST' and parsed.path.endswith('/submit'):
                            state['submissions']+=1
                            if state['lose_submit']:
                                state['lose_submit']=False;request_route.abort();return
                        headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding'}}
                        request_route.fulfill(status=response.status_code,headers=headers,body=response.content)
                    context.route('**/*',route)
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto('http://localhost/practice');expect(page.get_by_role('heading',name='Your generated practice')).to_be_visible()
                    page.get_by_role('button',name='Create practice from materials',exact=True).click()
                    page.get_by_label('Material title',exact=True).fill('Browser queue notes')
                    page.get_by_label('Study notes',exact=True).fill(NOTES+' <script>window.__bad=true</script>')
                    page.get_by_role('button',name='Save pasted notes',exact=True).click()
                    card=page.locator('article').filter(has=page.get_by_role('heading',name='Browser queue notes',exact=True))
                    expect(card).to_be_visible();card.get_by_label('Practice topic').fill('queue ordering')
                    card.get_by_label('Question count').select_option('2');card.get_by_role('button',name='Prepare practice',exact=True).click()
                    expect(page.get_by_role('button',name='Start a new attempt',exact=True)).to_be_visible()
                    page.get_by_role('button',name='Start a new attempt',exact=True).click()
                    expect(page.locator('fieldset')).to_have_count(2)
                    page.locator('fieldset').first.locator('input[value="B"]').check()
                    page.get_by_role('button',name='Back to all practice',exact=True).click()
                    expect(page.get_by_role('button',name=re.compile('Continue .*1 answered'))).to_be_visible()
                    page.get_by_role('button',name='Create practice from materials',exact=True).click()
                    card=page.locator('article').filter(has=page.get_by_role('heading',name='Browser queue notes',exact=True))
                    card.get_by_label('Practice topic').fill('queue ordering');card.get_by_label('Question count').select_option('2')
                    card.get_by_role('button',name='Prepare practice',exact=True).click()
                    expect(page.get_by_role('button',name='Start a new attempt',exact=True)).to_have_count(2)
                    page.get_by_role('button',name='Start a new attempt',exact=True).first.click()
                    page.locator('fieldset').first.locator('input[value="A"]').check()
                    page.get_by_role('button',name='Back to all practice',exact=True).click()
                    expect(page.get_by_role('button',name=re.compile('Continue '))).to_have_count(2)
                    page.get_by_role('button',name=re.compile('Continue ')).last.click()
                    expect(page.locator('fieldset').first.locator('input[value="B"]')).to_be_checked()
                    page.locator('fieldset').nth(1).locator('input[value="A"]').check()
                    state['lose_submit']=True
                    page.get_by_role('button',name='Submit completed practice',exact=True).click()
                    expect(page.locator('#notice')).to_be_visible()
                    # Server completed the request even though the response was lost.
                    page.reload();expect(page.get_by_role('button',name=re.compile('Review .*50%'))).to_be_visible()
                    page.get_by_role('button',name=re.compile('Review .*50%')).click()
                    expect(page.get_by_role('heading',name='50%',exact=True)).to_be_visible()
                    expect(page.get_by_text('The notes assign last-in, first-out to a stack, not a queue.',exact=True)).to_be_visible()
                    expect(page.get_by_text(re.compile('Source: Browser queue notes'))).to_have_count(2)
                    assert fixture.store.listing()['stats']['completed']==1
                    assert len([a for a in fixture.store.listing()['attempts'] if a['status']=='in_progress'])==1
                    assert page.evaluate('window.__bad') is None
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Mobile horizontal overflow'
                    page.get_by_role('link',name='Grades',exact=True).click()
                    expect(page.get_by_role('heading',name='Your results',exact=True)).to_be_visible()
                    grade=page.locator('button.grade-row').filter(has_text='Browser queue notes practice')
                    expect(grade).to_contain_text('50%');grade.click()
                    expect(page.get_by_role('heading',name='50%',exact=True)).to_be_visible()
                    artifacts=ROOT/'tests/artifacts';artifacts.mkdir(exist_ok=True)
                    page.screenshot(path=str(artifacts/'practice-result-mobile.png'),full_page=True)
                    page.get_by_role('link',name='Quizzes',exact=True).click()
                    page.get_by_role('button',name='Start practice',exact=True).click()
                    expect(page.locator('legend')).to_contain_text('CPU')
                    page.locator('input[value="A"]').check()
                    page.get_by_role('button',name='All quizzes',exact=False).click()
                    page.get_by_role('link',name='Generated Practice',exact=True).click()
                    expect(page.get_by_role('button',name=re.compile('Continue '))).to_have_count(1)
                    page.get_by_role('button',name=re.compile('Continue .*1 answered')).click()
                    expect(page.locator('fieldset').first.locator('input[value="A"]')).to_be_checked()
                    page.locator('fieldset').nth(1).locator('input[value="B"]').check()
                    page.get_by_role('button',name='Submit completed practice',exact=True).click()
                    page.get_by_role('button',name='Practice history',exact=True).click()
                    expect(page.get_by_text('NEXT REVIEW',exact=True)).to_be_visible()
                    expect(page.get_by_role('heading',name='queue ordering',exact=True)).to_be_visible()
                    page.get_by_role('button',name='Practice this topic from my materials',exact=True).click()
                    card=page.locator('article').filter(has=page.get_by_role('heading',name='Browser queue notes',exact=True))
                    expect(card.get_by_label('Practice topic',exact=True)).to_have_value('queue ordering')
                    page.get_by_role('button',name='Back to practice',exact=True).click()
                    page.get_by_role('button',name='Create practice from materials',exact=True).click()
                    card=page.locator('article').filter(has=page.get_by_role('heading',name='Browser queue notes',exact=True))
                    card.get_by_label('Practice topic').fill('queue ordering')
                    card.get_by_label('Question count').select_option('2')
                    card.get_by_role('button',name='Prepare practice',exact=True).click()
                    page.get_by_role('button',name='Start a new attempt',exact=True).first.click()
                    page.get_by_role('button',name='Back to all practice',exact=True).click()
                    expect(page.get_by_role('button',name=re.compile('Continue '))).to_have_count(1)
                    page.get_by_role('link',name='Quizzes',exact=True).click()
                    page.get_by_role('button',name='Continue practice',exact=True).click()
                    # Legacy practice resumes at the next unanswered question.
                    expect(page.locator('legend')).to_contain_text('FIFO')
                    assert not errors,errors
                    context.close();browser.close()
                    print('PASS mobile paste, model-backed synthetic generation, independent saved attempts, lost submission response, result review, shared Grades navigation, legacy demo draft preservation, XSS-as-data and layout checks.')
            finally:
                for k,v in previous.items():
                    if v is None:os.environ.pop(k,None)
                    else:os.environ[k]=v
    finally:fixture.doCleanups()


if __name__=='__main__':main()
