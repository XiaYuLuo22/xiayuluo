"""Local-owner training protection UI; all storage/backup responses are controlled."""
from test_browser import browser_app, page, playwright_api  # noqa: F401


def training_status(**changes):
    return {'root': r'E:\夏鱼洛数据\training', 'ready': True,
            'message': '训练数据与程序分开保存；普通更新不会清理此目录。',
            'last_backup': None, **changes}


def open_training_settings(page, info):
    page.route('**/api/training/status', lambda route: route.fulfill(json=info))
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#training-protection')).to_be_visible()
    playwright_api.expect(page.locator('#training-status')).to_have_text(info['message'])


def test_training_ui_shows_storage_and_successful_backup(page):
    requests = []
    backup = {'path': r'E:\夏鱼洛数据\training\backups\training-20261026.zip', 'files': 12, 'bytes': 123456}

    def backup_route(route):
        requests.append(route.request.post_data_json)
        assert route.request.headers.get('x-assistant-token')
        route.fulfill(json=training_status(last_backup=backup))

    page.route('**/api/training/backup', backup_route)
    open_training_settings(page, training_status())
    playwright_api.expect(page.locator('#training-root')).to_have_text(r'E:\夏鱼洛数据\training')
    playwright_api.expect(page.locator('#training-ready')).to_have_text('就绪')
    playwright_api.expect(page.locator('#training-backup-path')).to_contain_text('暂无成功备份')
    playwright_api.expect(page.locator('#training-protection')).to_contain_text('本次运行成功备份')
    playwright_api.expect(page.locator('#training-protection')).to_contain_text('旧备份仍保存在训练目录的 backups 文件夹')
    page.locator('#backup-training').click()
    playwright_api.expect(page.locator('#training-backup-result')).to_have_text(f"备份成功：{backup['path']}")
    playwright_api.expect(page.locator('#training-backup-path')).to_have_text(backup['path'])
    playwright_api.expect(page.locator('#training-backup-size')).to_have_text('12 个文件 · 123,456 字节')
    assert requests == [{}]
    assert page.locator('#training-protection input, #training-protection textarea').count() == 0
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_training_ui_backup_failure_retains_previous_backup(page):
    previous = {'path': r'E:\training\backups\previous.zip', 'files': 3, 'bytes': 64}
    page.route('**/api/training/backup', lambda route: route.fulfill(status=409,
        json={'detail': '训练数据备份失败；已保留原文件，请检查目录、空间或写入锁。'}))
    open_training_settings(page, training_status(last_backup=previous))
    page.locator('#backup-training').click()
    playwright_api.expect(page.locator('#training-backup-result')).to_contain_text('备份未完成')
    playwright_api.expect(page.locator('#training-backup-result')).to_contain_text('已保留原文件')
    playwright_api.expect(page.locator('#training-backup-path')).to_have_text(previous['path'])
    playwright_api.expect(page.locator('#backup-training')).to_be_enabled()


def test_training_ui_unready_and_failed_status_disable_backup(page):
    info = training_status(ready=False, message='训练目录无法核实；原数据保留。')
    open_training_settings(page, info)
    playwright_api.expect(page.locator('#training-ready')).to_contain_text('需处理')
    playwright_api.expect(page.locator('#backup-training')).to_be_disabled()
    page.route('**/api/training/status', lambda route: route.fulfill(status=503, json={'detail': '暂时无法读取'}))
    page.locator('#refresh-status').click()
    playwright_api.expect(page.locator('#training-status')).to_contain_text('无法读取训练数据保护状态')
    playwright_api.expect(page.locator('#training-ready')).to_have_text('尚未核实')
    playwright_api.expect(page.locator('#backup-training')).to_be_disabled()


def test_training_ui_paths_and_messages_are_plain_text(page):
    payload = '<img src="invalid" onerror="window.trainingExploit=true">'
    open_training_settings(page, training_status(root=payload, message=payload,
        last_backup={'path': payload, 'files': 1, 'bytes': 1}))
    playwright_api.expect(page.locator('#training-root')).to_have_text(payload)
    playwright_api.expect(page.locator('#training-backup-path')).to_have_text(payload)
    assert page.locator('#training-protection img, #training-protection script').count() == 0
    assert page.evaluate('window.trainingExploit === undefined')


def test_training_ui_hidden_from_mobile_path_and_before_owner_auth(page):
    requests = []
    page.route('**/api/training/status', lambda route: (requests.append(route.request.url), route.fulfill(json=training_status())))
    page.evaluate("history.replaceState(null, '', '/mobile/settings')")
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#training-protection')).to_be_hidden()
    assert not requests
    page.evaluate("history.replaceState(null, '', '/'); state.token = ''")
    page.locator('[data-view="settings"]').click()
    playwright_api.expect(page.locator('#training-protection')).to_be_hidden()
    assert not requests
