from test_browser import browser_app, page, playwright_api  # noqa: F401


def prepare(page):
    page.evaluate("async () => { await post('/api/automation', {mode: 'auto'}); await refreshStatus(); }")
    page.route('**/api/updates', lambda route: route.fulfill(json={'available': True,
        'version': '9.0.0', 'current': '0.1.14', 'notes': 'fixture', 'install_supported': True}))
    page.locator('[data-view="settings"]').click()


def test_update_download_progress_is_visible_but_not_installation_success(page):
    prepare(page)
    requests = []
    page.route('**/api/updates/install', lambda route: requests.append(route))
    page.route('**/api/updates/progress', lambda route: route.fulfill(json={
        'phase': 'downloading', 'message': '正在下载签名更新包。', 'received_bytes': 75, 'total_bytes': 100}))
    page.locator('#download-update').click()
    playwright_api.expect(page.locator('#update-status')).to_have_text('正在下载签名更新包。 75%')
    assert len(requests) == 1 and requests[0].request.post_data_json == {'confirm': True}
    requests[0].fulfill(json={'restarting': True})
    playwright_api.expect(page.locator('#update-status')).to_contain_text('新版启动成功后才能确认更新完成')
    assert page.evaluate('updateBusy === false')


def test_late_progress_cannot_overwrite_failed_install_message(page):
    prepare(page)
    installation, progress = [], []
    page.route('**/api/updates/install', lambda route: installation.append(route))
    page.route('**/api/updates/progress', lambda route: progress.append(route))
    page.locator('#download-update').click()
    page.wait_for_function('updateBusy === true')
    # Waiting for the first scheduled poll gives us a deliberately stale reply.
    page.wait_for_timeout(1200)
    assert installation and progress
    installation[0].fulfill(status=409, json={'detail': '安装包校验失败，当前版本保留。'})
    playwright_api.expect(page.locator('#update-status')).to_have_text('安装包校验失败，当前版本保留。')
    progress[0].fulfill(json={'phase': 'ready', 'message': '不应覆盖错误的旧进度', 'received_bytes': 100, 'total_bytes': 100})
    page.wait_for_timeout(100)
    playwright_api.expect(page.locator('#update-status')).to_have_text('安装包校验失败，当前版本保留。')
