import os
from threading import Thread

from PIL import Image
import pytest

from assistant.proactive import ObservationSkipped
from assistant.visual_capture import MSSFrameSource, compare_frames


def info(**changes):
    return {'title': 'test fixture editor', 'pid': os.getpid() + 1, 'window': 42,
            'rect': (20, 30, 220, 150), **changes}


class Shot:
    def __init__(self, width, height):
        self.size = (width, height)
        self.bgra = bytearray([3, 2, 1, 255] * (width * height))


class Capture:
    def __init__(self):
        self.monitors = [{}, {'left': 0, 'top': 0, 'width': 300, 'height': 200}]
        self.calls = []
        self.closed = False
        self.shot = None

    def grab(self, region):
        self.calls.append(region)
        self.shot = Shot(region['width'], region['height'])
        return self.shot

    def close(self):
        self.closed = True


def source(window_info=lambda: info(), **kwargs):
    capture = Capture()
    return MSSFrameSource(True, platform='win32', window_info=window_info,
                          mss_factory=lambda: capture, **kwargs), capture


def test_off_non_windows_and_status_never_open_capture():
    calls = []
    disabled = MSSFrameSource(mss_factory=lambda: calls.append('opened'))
    assert not disabled.available()[0]
    with pytest.raises(ValueError):
        disabled.capture()
    non_windows = MSSFrameSource(True, platform='linux', mss_factory=lambda: calls.append('opened'))
    assert not non_windows.available()[0]
    with pytest.raises(ValueError):
        non_windows.capture()
    enabled = MSSFrameSource(True, platform='win32', mss_factory=lambda: calls.append('opened'))
    assert enabled.available() == (True, '')
    enabled.close()
    assert calls == []


def test_missing_components_are_honest_and_do_not_open(monkeypatch):
    monkeypatch.setattr('assistant.visual_capture.importlib.util.find_spec', lambda name: None)
    capture = MSSFrameSource(True, platform='win32')
    assert not capture.available()[0]
    with pytest.raises(ValueError, match='组件'):
        capture.capture()
    assert capture.source is None


def test_capture_clips_foreground_and_copies_bgra_without_titles():
    capture, driver = source(lambda: info(rect=(-20, 30, 220, 250)))
    result = capture.capture()
    assert driver.calls == [{'left': 0, 'top': 30, 'width': 220, 'height': 170}]
    assert result['image'].mode == 'RGB' and result['image'].getpixel((0, 0)) == (1, 2, 3)
    assert (result['width'], result['height']) == (220, 170)
    assert set(result) == {'image', 'width', 'height', 'scope_id'}
    assert len(result['scope_id']) == 64 and 'editor' not in result['scope_id']
    driver.shot.bgra[:] = b'\0' * len(driver.shot.bgra)
    assert result['image'].getpixel((0, 0)) == (1, 2, 3)
    capture.close()
    assert driver.closed


@pytest.mark.parametrize('title', ['登录', 'Password manager', 'InPrivate', '银行付款'])
def test_private_windows_are_skipped_before_open(title):
    capture, driver = source(lambda: info(title=title))
    with pytest.raises(ObservationSkipped):
        capture.capture()
    assert capture.source is None and driver.calls == []


def test_self_and_unavailable_secure_desktop_are_skipped():
    capture, driver = source(lambda: info(pid=os.getpid()))
    with pytest.raises(ObservationSkipped):
        capture.capture()
    assert not driver.calls
    def locked():
        raise ObservationSkipped('locked fixture')
    capture, driver = source(locked)
    with pytest.raises(ObservationSkipped):
        capture.capture()
    assert capture.source is None


@pytest.mark.parametrize('change', [{'window': 43}, {'rect': (21, 30, 220, 150)},
                                    {'title': 'other fixture'}, {'title': '密码'}])
def test_focus_or_geometry_changes_during_capture_discard_frame(change):
    values = iter([info(), info(**change)])
    capture, _ = source(lambda: next(values))
    with pytest.raises(ObservationSkipped):
        capture.capture()
    capture.close()


@pytest.mark.parametrize('rect', [(400, 0, 600, 120), (0, 0, 79, 120),
                                  (0, 0, 200, 79), (20, 30, 10, 20)])
def test_small_outside_or_reversed_regions_are_not_grabbed(rect):
    capture, driver = source(lambda: info(rect=rect))
    with pytest.raises(ObservationSkipped):
        capture.capture()
    assert not driver.calls
    capture.close()


def test_geometry_bounds_pixel_budget_and_returned_size_are_checked():
    capture, driver = source(lambda: info(rect=(0, 0, 6000, 6000)))
    driver.monitors[1].update(width=6000, height=6000)
    with pytest.raises(ObservationSkipped):
        capture.capture()
    assert not driver.calls
    capture.close()
    capture, driver = source()
    driver.grab = lambda _: Shot(1, 1)
    with pytest.raises(ObservationSkipped, match='尺寸'):
        capture.capture()
    capture.close()


def test_window_and_geometry_have_distinct_scope_but_title_is_not_identity():
    values = [info(), info(), info(title='renamed fixture'), info(title='renamed fixture'),
              info(window=44), info(window=44), info(rect=(10, 30, 220, 150)), info(rect=(10, 30, 220, 150))]
    iterator = iter(values)
    capture, _ = source(lambda: next(iterator))
    scopes = [capture.capture()['scope_id'] for _ in range(4)]
    assert scopes[0] == scopes[1]
    assert len(set(scopes)) == 3
    capture.close()


def test_primary_monitor_is_selected_by_windows_origin_not_enumeration_order():
    capture, driver = source(lambda: info(rect=(-20, 30, 220, 250)))
    driver.monitors = [{}, {'left': -300, 'top': 0, 'width': 300, 'height': 200},
                       {'left': 0, 'top': 0, 'width': 300, 'height': 200}]
    capture.capture()
    assert driver.calls == [{'left': 0, 'top': 30, 'width': 220, 'height': 170}]
    capture.close()


def test_resources_are_thread_affine_and_can_be_reopened_after_close():
    capture, _ = source()
    capture.capture()
    errors = []
    def elsewhere():
        for operation in (capture.capture, capture.close):
            try:
                operation()
            except ValueError as error:
                errors.append(str(error))
    thread = Thread(target=elsewhere)
    thread.start(); thread.join()
    assert len(errors) == 2
    capture.close()
    capture.capture()
    capture.close()


def test_pixel_change_measure_is_bounded_not_success():
    pytest.importorskip('cv2')
    black, white = Image.new('RGB', (80, 80)), Image.new('RGB', (80, 80), 'white')
    assert compare_frames(black, black) == 0.0
    assert compare_frames(black, white) == 1.0
    with pytest.raises(ValueError):
        compare_frames(black, white.resize((81, 80)))
    with pytest.raises(ValueError):
        compare_frames(black.convert('L'), white)
