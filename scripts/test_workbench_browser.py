"""Offline browser verification. Run with the project's Python and Playwright installed."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import threading
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import toml
from playwright.sync_api import sync_playwright, expect
from pypdf import PdfWriter
from src.gui.server import Workbench, WorkbenchServer
from src.gui.jobs import TranslationJob


class ModelAPI(BaseHTTPRequestHandler):
    translation_count = 0

    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.reply(200, {'data': [{'id': model} for model in ['test-flash', 'test-flash-fast', 'gpt-6-luna', 'gpt-5.6-luna'] + [f'test-model-{index:02d}' for index in range(28)]]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        content = body['messages'][-1]['content']
        if content == 'Reply with exactly OK.':
            return self.reply(200, {'choices': [{'message': {'content': 'OK'}}]})
        if body['messages'][0]['content'].startswith('Translate the following LaTeX into Simplified Chinese.'):
            return self.reply(200, {'choices': [{'message': {'content': r'\section{实验设置} 这是离线翻译测试。'}}]})
        ModelAPI.translation_count += 1
        if ModelAPI.translation_count == 1:
            return self.reply(429, {'error': {'message': 'offline test rate limit'}})
        time.sleep(40)
        self.reply(200, {'choices': [{'message': {'content': content}}]})


def main():
    # Keep offline fixtures on this machine even when the shell configures a VPN proxy.
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='/tmp/latextrans-ui-v2')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='latextrans-browser-') as tmp:
        folder = Path(tmp)
        model_server = ThreadingHTTPServer(('127.0.0.1', 0), ModelAPI)
        threading.Thread(target=model_server.serve_forever, daemon=True).start()
        config = folder / 'config.toml'
        config.write_text(toml.dumps({
            'source_language': 'en', 'target_language': 'ch', 'category': {},
            'tex_sources_dir': str(folder / 'source'), 'output_dir': str(folder / 'output'),
            'llm_config': {'model': 'test-flash', 'api_key': 'browser-private-secret',
                'base_url': f'http://127.0.0.1:{model_server.server_port}/v1/chat/completions', 'max_concurrent_requests': 1},
        }))
        app = Workbench(config)
        server = WorkbenchServer(('127.0.0.1', 0), app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        address = f'http://127.0.0.1:{server.server_port}'
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1920, 'height': 950})
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                try:
                    page.goto(address)
                    page.wait_for_load_state('networkidle')
                    expect(page.get_by_role('heading', name='论文翻译', exact=True)).to_be_visible()
                    box = page.locator('#start-button').bounding_box()
                    assert box['y'] + box['height'] < 950, 'Primary action is below the fold'
                    page.screenshot(path=str(output / 'desktop.png'), full_page=True)
                    page.locator('#start-button').click()
                    expect(page.locator('#input-error')).to_be_visible()
                    # The configuration is a working dialog, not an always-visible sidebar.
                    page.get_by_role('button', name='设置', exact=True).click()
                    expect(page.get_by_role('dialog')).to_be_visible()
                    page.locator('[data-models="primary"]').click()
                    expect(page.locator('#primary-feedback')).to_contain_text('已获取 32 个模型')
                    expect(page.locator('#primary-model-options [role="option"]')).to_have_count(32)
                    page.locator('#setting-model').fill('gpt-6-luna')
                    expect(page.locator('#primary-model-options [role="option"]')).to_have_count(1)
                    page.locator('[data-model-toggle="primary"]').click()
                    page.locator('[data-model-toggle="primary"]').click()
                    expect(page.locator('#primary-model-options [role="option"]')).to_have_count(32)
                    page.screenshot(path=str(output / 'model-list-desktop.png'), full_page=True)
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.locator('#setting-model').click()
                    expect(page.locator('#primary-model-menu')).to_be_visible()
                    bounds = page.locator('#primary-model-menu').bounding_box()
                    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390, 'model popup exceeds mobile width'
                    assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 844, 'model popup exceeds mobile height'
                    page.screenshot(path=str(output / 'model-list-mobile.png'), full_page=True)
                    page.locator('#primary-model-options').get_by_role('option', name='test-model-27', exact=True).click()
                    expect(page.locator('#setting-model')).to_have_value('test-model-27')
                    page.set_viewport_size({'width': 1920, 'height': 950})
                    page.locator('#setting-model').click()
                    page.locator('#primary-model-options').get_by_role('option', name='gpt-5.6-luna', exact=True).click()
                    expect(page.locator('#setting-model')).to_have_value('gpt-5.6-luna')
                    expect(page.locator('#primary-model-menu')).not_to_be_visible()
                    page.locator('#setting-model').click()
                    expect(page.locator('#primary-model-options [role="option"]')).to_have_count(32)
                    page.locator('#setting-model').fill('flash-fast')
                    expect(page.locator('#primary-model-options [role="option"]')).to_have_count(1)
                    page.locator('#setting-model').press('ArrowDown')
                    page.locator('#setting-model').press('Enter')
                    expect(page.locator('#setting-model')).to_have_value('test-flash-fast')
                    page.locator('#setting-model').fill('custom/private-model')
                    expect(page.locator('#primary-model-options')).to_contain_text('没有匹配')
                    page.locator('#setting-model').press('Escape')
                    expect(page.get_by_role('dialog')).to_be_visible()
                    expect(page.locator('#setting-model')).to_have_value('custom/private-model')
                    page.locator('#setting-model').fill('test-flash')
                    page.locator('#setting-model').press('Escape')
                    page.locator('.settings-details').first.locator('summary').click()
                    page.locator('[data-models="repair"]').click()
                    expect(page.locator('#repair-model-options [role="option"]')).to_have_count(32)
                    page.locator('#repair-model-options').get_by_role('option', name='gpt-6-luna', exact=True).click()
                    expect(page.locator('#setting-repair-model')).to_have_value('gpt-6-luna')
                    page.locator('.settings-details').first.locator('summary').click()
                    page.locator('.settings-details').nth(1).locator('summary').click()
                    chosen = str(folder / 'chosen output')
                    with patch('src.gui.server.shutil.which', return_value='picker'), patch('src.gui.server.pick_save_directory', return_value=chosen):
                        page.locator('#pick-output-directory').click()
                        expect(page.locator('#setting-output')).to_have_value(chosen)
                    with patch('src.gui.server.shutil.which', return_value='picker'), patch('src.gui.server.pick_save_directory', return_value=None):
                        page.locator('#pick-output-directory').click()
                        expect(page.locator('#directory-feedback')).to_contain_text('已取消')
                        expect(page.locator('#setting-output')).to_have_value(chosen)
                    page.locator('.settings-details').nth(1).locator('summary').click()
                    page.locator('[data-probe="primary"]').first.click()
                    expect(page.locator('#primary-feedback')).to_contain_text('连接成功', timeout=10000)
                    expect(page.locator('#primary-feedback')).to_contain_text('test-flash')
                    page.locator('[data-probe-kind="translation"]').click()
                    expect(page.locator('#primary-feedback')).to_contain_text('短文翻译完成', timeout=10000)
                    page.screenshot(path=str(output / 'settings.png'), full_page=True)
                    page.get_by_role('button', name='保存设置', exact=True).click()
                    expect(page.get_by_role('dialog')).not_to_be_visible()
                    assert toml.load(config)['llm_config']['api_key'] == 'browser-private-secret'
                    assert toml.load(config)['output_dir'] == chosen
                    # Upload a real source archive and run the actual translation worker.
                    archive = io.BytesIO()
                    with zipfile.ZipFile(archive, 'w') as target:
                        target.writestr('main.tex', r'''\documentclass{article}
\title{Offline monitor test}
\begin{document}
\maketitle
\section{Introduction}
This paper checks that a request failure is visible immediately in the translation interface.
\end{document}
''')
                    page.locator('#source-files').set_input_files({'name': 'paper.zip', 'mimeType': 'application/zip', 'buffer': archive.getvalue()})
                    expect(page.locator('.uploaded-file')).to_contain_text('paper.zip')
                    expect(page.locator('#start-button')).to_be_enabled()
                    page.locator('#start-button').click()
                    expect(page.locator('.status-heading')).to_contain_text('正在翻译', timeout=15000)
                    expect(page.locator('#monitor-content')).to_contain_text('接口限流', timeout=15000)
                    page.screenshot(path=str(output / 'retry.png'), full_page=True)
                    page.locator('[data-detail="requests"]').click()
                    expect(page.locator('#requests-panel .request-row:not(.header)')).to_be_visible(timeout=15000)
                    page.reload()
                    page.wait_for_load_state('networkidle')
                    expect(page.locator('.status-heading')).to_contain_text('正在翻译')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.evaluate('window.scrollTo(0,0)')
                    page.screenshot(path=str(output / 'mobile-running.png'), full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile horizontal overflow'
                    with page.expect_download() as info:
                        page.locator('#diagnostics-link').click()
                    info.value.save_as(str(output / 'diagnostics.json'))
                    diagnostics = (output / 'diagnostics.json').read_text()
                    assert 'browser-private-secret' not in diagnostics
                    assert 'request_error' in diagnostics
                    page.locator('#stop-button').click()
                    expect(page.locator('.status-heading')).to_contain_text('已停止', timeout=10000)
                    expect(page.locator('#start-button')).to_be_enabled(timeout=10000)
                    # A genuine preparation error must remain visible and retain its traceback.
                    empty = folder / 'empty-paper'
                    empty.mkdir()
                    page.set_viewport_size({'width': 1440, 'height': 1000})
                    page.locator('#tab-local').click()
                    page.locator('#local-input').fill(str(empty))
                    page.locator('#start-button').click()
                    expect(page.locator('.status-heading')).to_contain_text('翻译失败', timeout=15000)
                    expect(page.locator('#issues-panel')).to_contain_text('Traceback', timeout=10000)
                    page.screenshot(path=str(output / 'failed.png'), full_page=True)
                    # Supply a completed fixture to verify the result and file-download UI.
                    pdf_dir = folder / 'output/ch_completed-paper'
                    pdf_dir.mkdir(parents=True)
                    writer = PdfWriter()
                    writer.add_blank_page(300, 400)
                    writer.write(str(pdf_dir / 'ch_completed-paper_mono.pdf'))
                    job = TranslationJob(app.params(), {'paper_list': [], 'project_items': ['completed-paper']})
                    job.snapshot.apply({'type': 'project_start', 'project_name': 'completed-paper', 'index': 1, 'total': 1})
                    job.snapshot.apply({'type': 'stage_start', 'stage': 'compile'})
                    job.snapshot.apply({'type': 'result', 'result': {'output_dir': str(folder / 'output'), 'config': {'target_language': 'ch'},
                        'projects': ['completed-paper'], 'completed_projects': [{'project_dir': 'completed-paper'}], 'failed_projects': []}})
                    app.jobs[job.id] = job
                    page.reload()
                    page.wait_for_load_state('networkidle')
                    page.locator('.rail [data-view="history"]').click()
                    row = page.locator('#history-jobs .history-row').filter(has_text='completed-paper')
                    expect(row).to_be_visible()
                    row.get_by_role('button', name='查看').click()
                    expect(page.locator('#result-files')).to_contain_text('单语版')
                    with page.expect_download() as info:
                        page.get_by_role('link', name='下载 PDF').click()
                    info.value.save_as(str(output / 'test-download.pdf'))
                    assert (output / 'test-download.pdf').read_bytes().startswith(b'%PDF')
                    page.screenshot(path=str(output / 'success.png'), full_page=True)
                    # Connection loss is a visible state and clears after reconnection.
                    page.route('**/api/jobs/**', lambda route: route.abort())
                    expect(page.locator('#page-alert')).to_be_visible(timeout=10000)
                    page.unroute('**/api/jobs/**')
                    expect(page.locator('#page-alert')).not_to_be_visible(timeout=10000)
                    assert not errors, errors
                    print(json.dumps({'passed': ['desktop layout', 'settings dialog', '32-model dropdown', 'search and keyboard selection', 'mobile model dropdown', 'manual model input', 'repair model dropdown', 'native directory selection and cancellation', 'output directory persistence', 'connection test metadata',
                        'saved-key preservation', 'real source upload', 'immediate HTTP 429', 'retry countdown', 'active requests',
                        'refresh reconnect', 'mobile layout', 'diagnostics redaction', 'real process stop', 'failure traceback',
                        'history navigation', 'PDF download', 'server reconnect'], 'screenshots': str(output)}, ensure_ascii=False))
                except BaseException:
                    page.screenshot(path=str(output / 'test-failure.png'), full_page=True)
                    print(page.locator('body').inner_text()[-12000:])
                    raise
                finally:
                    browser.close()
        finally:
            for job in app.jobs.values():
                job.stop()
            server.shutdown()
            server.server_close()
            model_server.shutdown()
            model_server.server_close()


if __name__ == '__main__':
    main()
