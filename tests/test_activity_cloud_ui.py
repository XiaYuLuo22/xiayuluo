"""Owner-facing cloud and activity status uses plain text and honest readiness."""
from test_browser import browser_app, page, playwright_api  # noqa: F401


def test_activity_summary_is_plain_text_not_executable_or_saved(page):
    text = '<img src=x onerror="window.activityExecuted=true">'
    page.route('**/api/proactive/context', lambda route: route.fulfill(json={
        'items': [{'category': 'coding', 'activity': text, 'age_seconds': 12}],
        'read_only': True, 'ephemeral': True}))
    page.locator('[data-view="settings"]').click()
    page.locator('#refresh-activity-context').click()
    playwright_api.expect(page.locator('#activity-context')).to_contain_text(text)
    assert page.locator('#activity-context img, #activity-context script').count() == 0
    assert page.evaluate('window.activityExecuted === undefined')


def test_cloud_card_does_not_claim_no_server_is_connected(page):
    page.route('**/api/cloud-sync', lambda route: route.fulfill(json={
        'configured': False, 'phase': 'off', 'last_sync': None,
        'message': '未配置独立云服务；不会上传私人数据。'}))
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#cloud-sync-card')).to_be_visible()
    playwright_api.expect(page.locator('#cloud-sync-status')).to_contain_text('未配置独立云服务')
    playwright_api.expect(page.locator('#cloud-sync-now')).to_be_disabled()
    assert page.locator('#cloud-sync-card input').count() == 0


def test_cloud_sync_is_explicit_empty_body_and_hidden_from_phone_path(page):
    calls = []
    def cloud(route):
        if route.request.method == 'POST':
            calls.append(route.request.post_data_json)
        route.fulfill(json={'configured': True, 'phase': 'ready', 'last_sync': None, 'message': '文本同步测试完成'})
    page.route('**/api/cloud-sync', cloud)
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#cloud-sync-now')).to_be_enabled()
    assert not calls
    page.locator('#cloud-sync-now').click()
    playwright_api.expect(page.locator('#cloud-sync-status')).to_have_text('文本同步测试完成')
    assert calls == [{}]
    page.evaluate("history.replaceState(null, '', '/mobile/settings')")
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#cloud-sync-card')).to_be_hidden()
