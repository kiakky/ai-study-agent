"""Run after `python tests/preview.py`; uses only its isolated synthetic service.

Optional test dependency: playwright. Uses installed Microsoft Edge headlessly.
"""
import os
from pathlib import Path
import tempfile
import time
from contextlib import contextmanager
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8765'


def assert_no_absent_child_text(page):
    assert page.locator('#content').evaluate(
        "element => ![...element.childNodes].some(child => "
        "child.nodeType === Node.TEXT_NODE && ['null','undefined'].includes(child.textContent.trim()))"
    ), 'Absent optional elements must not render as null/undefined text'


@contextmanager
def browser_temp_directory():
    temp = tempfile.TemporaryDirectory(prefix='browser-', dir=ROOT / 'tests')
    assert Path(temp.name).resolve().is_relative_to((ROOT / 'tests').resolve())
    try:
        yield temp.name
    finally:
        # Edge can release its temporary download files just after browser.close().
        for retry in range(20):
            try:
                temp.cleanup()
                break
            except PermissionError:
                if retry == 19:
                    # Edge/BITS can retain download handles after all assertions
                    # and browser.close(). Preserve assertion failures, but do not
                    # confuse deferred cleanup of ignored synthetic files with QA.
                    print('NOTE: Windows retained temporary Edge files under ignored tests/browser-*; cleanup deferred.')
                    break
                time.sleep(0.25)


def main():
    artifacts = ROOT / 'tests' / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    with browser_temp_directory() as temp:
        # Keep the browser's disposable profile and files inside this project.
        previous = {name: os.environ.get(name) for name in ('TEMP', 'TMP')}
        os.environ.update(TEMP=temp, TMP=temp)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge', headless=True)
                context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=1)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(URL)
                expect(page.get_by_text('LOCAL TEST', exact=False)).to_be_visible()
                expect(page.get_by_role('heading', name='Hey, Alex (test).')).to_be_visible()
                baseline = page.evaluate("async () => (await (await fetch('/api/me', {headers:{'X-Telegram-Init-Data':Telegram.WebApp.initData}})).json()).stats.completed")
                page.goto(URL + '/setup')
                expect(page.get_by_text('Resume setup: google.',exact=True)).to_be_visible()
                expect(page.get_by_role('button',name='Connect / reconnect Google',exact=True)).to_be_disabled()
                page.get_by_role('button',name='Skip Google Calendar',exact=True).click()
                expect(page.get_by_text('Resume setup: brightspace.',exact=True)).to_be_visible()
                page.get_by_role('button',name='Continue without Brightspace',exact=True).click()
                expect(page.get_by_text('Resume setup: preferences.',exact=True)).to_be_visible()
                page.get_by_role('checkbox',name='MON',exact=True).check()
                page.locator('#session-minutes').fill('15')
                page.locator('#question-count').fill('8')
                page.locator('#preference-notes').fill('Synthetic note: short weekday practice.')
                page.get_by_role('button',name='Add a preferred time window',exact=True).click()
                page.get_by_label('Preferred window start',exact=True).fill('18:00')
                page.get_by_label('Preferred window end',exact=True).fill('20:00')
                page.get_by_role('button',name='Save preferences',exact=True).click()
                expect(page.get_by_text('Setup complete. You can update your profile anytime.',exact=True)).to_be_visible()
                page.reload()
                expect(page.locator('#session-minutes')).to_have_value('15')
                expect(page.locator('#question-count')).to_have_value('8')
                expect(page.get_by_label('Preferred window start',exact=True)).to_have_value('18:00')
                expect(page.locator('#preference-notes')).to_have_value('Synthetic note: short weekday practice.')
                page.screenshot(path=str(artifacts / 'setup-mobile.png'),full_page=True)
                page.goto(URL + '/study')
                expect(page.get_by_role('heading',name='Your study plan',exact=True)).to_be_visible()
                expect(page.locator('#goal-form')).to_have_count(0)
                expect(page.locator('#plan-form')).to_have_count(0)
                expect(page.get_by_text('No study plan has been proposed yet. Your AI Study Agent will prepare one when it has the necessary information.',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Planning screen overflows mobile width'
                page.screenshot(path=str(artifacts / 'study-mobile.png'),full_page=True)
                page.goto(URL)
                expect(page.get_by_role('heading', name='Hey, Alex (test).')).to_be_visible()
                page.get_by_role('button', name='Grades', exact=True).click()
                expect(page.get_by_role('heading', name='Your results')).to_be_visible()
                assert_no_absent_child_text(page)
                page.get_by_role('button', name='Dashboard', exact=True).click()
                page.screenshot(path=str(artifacts / 'dashboard-mobile.png'), full_page=True)
                page.get_by_role('button', name='Start practice', exact=True).click()
                for index in range(10):
                    expect(page.get_by_text(f'Question {index + 1} of 10', exact=True)).to_be_visible()
                    page.get_by_role('radio').first.check()
                    if index == 3:
                        page.reload()
                        page.get_by_role('button', name='Continue practice', exact=True).click()
                        expect(page.get_by_text('Question 5 of 10', exact=True)).to_be_visible()
                        continue
                    if index < 9:
                        page.get_by_role('button', name='Next →', exact=True).click()
                page.screenshot(path=str(artifacts / 'quiz-mobile.png'), full_page=True)
                page.get_by_role('button', name='Submit answers', exact=True).click()
                expect(page.get_by_text('PRACTICE COMPLETE · RESULT SAVED')).to_be_visible()
                expect(page.get_by_text('3 of 10 correct · Sample MCQ Practice')).to_be_visible()
                page.screenshot(path=str(artifacts / 'result-mobile.png'), full_page=True)
                page.get_by_role('button', name='Grades', exact=True).click()
                assert_no_absent_child_text(page)
                expect(page.get_by_role('button', name='Load earlier results', exact=True)).to_have_count(0)
                page.locator('.grade-row').first.click()
                expect(page.get_by_text('PRACTICE COMPLETE · RESULT SAVED')).to_be_visible()
                expect(page.locator('.review-card')).to_have_count(10)
                page.get_by_role('button', name='Practise again ↗', exact=True).click()
                for index in range(10):
                    page.get_by_role('radio').nth(1).check()
                    if index < 9:
                        page.get_by_role('button', name='Next →', exact=True).click()

                # Server commits, but the response never reaches the browser.
                def lose_response(route):
                    response = route.fetch()
                    assert response.status == 200
                    route.abort('failed')
                page.route('**/api/quizzes/*/submit', lose_response, times=1)
                page.get_by_role('button', name='Submit answers', exact=True).click()
                expect(page.get_by_role('alert')).to_contain_text('Connection interrupted')
                expect(page.get_by_role('radio').first).to_be_disabled()
                page.reload()
                page.get_by_role('button', name='Recover submission', exact=True).click()
                page.get_by_role('button', name='Retry submission', exact=True).click()
                expect(page.get_by_text('PRACTICE COMPLETE · RESULT SAVED')).to_be_visible()
                completed = page.evaluate("async () => (await (await fetch('/api/me', {headers:{'X-Telegram-Init-Data':Telegram.WebApp.initData}})).json()).stats.completed")
                assert completed == baseline + 2, (baseline, completed)
                page.get_by_role('button', name='Grades', exact=True).click()
                expect(page.locator('.grade-row')).to_have_count(min(completed, 30))
                page.screenshot(path=str(artifacts / 'grades-mobile.png'), full_page=True)
                for width in (320, 390, 768, 1280):
                    page.set_viewport_size({'width': width, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), width
                page.get_by_role('button', name='Dashboard', exact=True).click()
                page.screenshot(path=str(artifacts / 'dashboard-desktop.png'), full_page=True)

                # Text-only rendering prevents untrusted source content becoming HTML.
                def malicious_question(route):
                    response = route.fetch()
                    data = response.json()
                    data['questions'][0]['text'] = '<img src=x onerror=alert(1)>'
                    route.fulfill(response=response, json=data)
                page.route('**/api/quizzes/*', malicious_question, times=1)
                page.get_by_role('button', name='Start practice', exact=True).click()
                expect(page.locator('legend')).to_have_text('<img src=x onerror=alert(1)>')
                expect(page.locator('legend img')).to_have_count(0)

                # An ordinary browser receives no signed identity and cannot use the API.
                page.route('**/__preview__/telegram.js', lambda route: route.fulfill(
                    content_type='application/javascript', body='window.Telegram={WebApp:{initData:""}};'))
                page.reload()
                expect(page.get_by_role('heading', name='Open inside Telegram')).to_be_visible()
                expect(page.get_by_role('button', name='Quizzes', exact=True)).to_be_disabled()
                page.screenshot(path=str(artifacts / 'signed-out.png'), full_page=True)
                assert not errors, errors
                context.close()
                browser.close()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    print('PASS: mobile quiz, draft reload, history review, retake, lost-response recovery, responsive widths, text safety, signed-out state; no JavaScript errors.')


if __name__ == '__main__':
    main()
