"""Run only against tests/preview.py --mock-events; no real integrations."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright,expect
from browser_check import browser_temp_directory

ROOT=Path(__file__).resolve().parents[1]


def main():
    with browser_temp_directory() as folder, sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='msedge',headless=True)
        try:
            context=browser.new_context(viewport={'width':390,'height':844},accept_downloads=False)
            page=context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:8765/study')
            expect(page.get_by_text('LOCAL TEST',exact=False)).to_be_visible()
            expect(page.locator('#goal-form')).to_have_count(0)
            expect(page.locator('#plan-form')).to_have_count(0)
            expect(page.get_by_role('heading',name=re.compile(r'^AI Study Agent Calendar TEST .* draft$'))).to_be_visible()
            expect(page.get_by_role('button',name=re.compile('Approve|Execute|Create'))).to_have_count(0)
            expect(page.get_by_text('Review, approve or request changes in Telegram',exact=False)).to_be_visible()
            nav=page.get_by_role('navigation',name='Study Agent sections')
            for destination in ['Dashboard','Study Plan','Quizzes','Grades','Settings']:
                expect(nav.get_by_role('link',name=destination,exact=True)).to_be_visible()
            nav.get_by_role('link',name='Settings',exact=True).click()
            expect(page.locator('#setup-content')).to_be_visible()
            page.get_by_role('navigation',name='Study Agent sections').get_by_role('link',name='Grades',exact=True).click()
            expect(page.get_by_role('heading',name='Your results',exact=True)).to_be_visible()
            page.get_by_role('navigation',name='Study Agent sections').get_by_role('link',name='Study Plan',exact=True).click()
            expect(page.get_by_role('heading',name=re.compile(r'^AI Study Agent Calendar TEST .* draft$'))).to_be_visible()
            page.reload()
            expect(page.locator('#study-content')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors,errors
            (ROOT/'tests'/'artifacts').mkdir(exist_ok=True)
            page.screenshot(path=str(ROOT/'tests'/'artifacts'/'calendar-mobile.png'),full_page=True)
            print('PASS: mobile read-only plan view, five-section authenticated navigation and reopen; no JavaScript errors.')
        finally:browser.close()


if __name__=='__main__':main()
