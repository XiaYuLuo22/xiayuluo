import asyncio
from threading import Event, get_ident
import time

from PIL import Image
import pytest

from assistant.proactive import ObservationSkipped
from assistant.visual_loop import VisualLoop


class Source:
    def __init__(self):
        self.calls = self.closes = 0
        self.scope = 'fixture-window'
        self.threads = []
        self.block = None
        self.started = Event()
        self.private = False

    def available(self):
        return True, ''

    def capture(self):
        self.started.set()
        self.threads.append(get_ident())
        self.calls += 1
        if self.block is not None:
            self.block.wait(2)
        if self.private:
            raise ObservationSkipped('fixture privacy')
        return {'image': Image.new('RGB', (80, 80), (self.calls % 255, 0, 0)),
                'width': 80, 'height': 80, 'scope_id': self.scope}

    def close(self):
        self.threads.append(get_ident())
        self.closes += 1


def prediction(image, goal):
    return {'proposed_action': 'click', 'normalized_xy': [.5, .5],
            'confidence': .8, 'execution_permitted': False}


async def wait_for(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(.005)


async def test_default_off_status_has_no_capture_threads_or_model_calls():
    source = Source()
    loop = VisualLoop(source)
    result = loop.status()
    assert not result['running'] and not result['policy_ready'] and not result['real_model_ready']
    assert result['execution_permitted'] is False and result['executed_actions'] == 0
    with pytest.raises(ValueError):
        await loop.start()
    assert source.calls == source.closes == 0
    await loop.close()


@pytest.mark.parametrize('options', [{'capture_hz': 0}, {'capture_hz': 31}, {'capture_hz': True},
                                    {'inference_hz': float('nan')}, {'duration_seconds': 121},
                                    {'start_delay_seconds': -1}, {'start_delay_seconds': 6},
                                    {'start_delay_seconds': True},
                                    {'goal': 'x' * 129}, {'goal': 'private\nother'}])
async def test_invalid_run_never_captures(options):
    source = Source()
    loop = VisualLoop(source, eligible=lambda: True)
    with pytest.raises(ValueError):
        await loop.start(**options)
    assert not loop.running and source.calls == 0


async def test_no_policy_measures_real_sampling_without_fake_inference_or_execution():
    pytest.importorskip('cv2')
    source = Source()
    loop = VisualLoop(source, eligible=lambda: True)
    await loop.start('只测量', capture_hz=30, inference_hz=10, duration_seconds=2)
    await wait_for(lambda: loop.captured_frames >= 3 and loop.processed_frames >= 1)
    await loop.stop()
    result = loop.status()
    assert result['phase'] == 'off' and result['captured_frames'] >= 2
    assert result['capture_fps'] > 0 and result['processed_frames'] >= 1
    assert result['inference_fps'] == 0 and result['p95_inference_ms'] is None
    assert not result['policy_ready'] and result['executed_actions'] == 0
    assert not loop.running and loop._latest is None and source.closes == 1
    assert len(set(source.threads)) == 1
    await loop.close()


async def test_latest_slot_drops_old_frames_and_slow_predictions_never_create_actions():
    pytest.importorskip('cv2')
    source = Source()
    def slow(image, goal):
        time.sleep(.08)
        return prediction(image, goal)
    loop = VisualLoop(source, eligible=lambda: True, predictor=slow)
    await loop.start('影子目标', capture_hz=30, inference_hz=20, duration_seconds=2)
    await wait_for(lambda: loop.last_prediction is not None)
    current = loop.status()
    assert current['last_prediction']['execution_permitted'] is False
    assert current['last_prediction']['generation'] == loop.generation
    await wait_for(lambda: loop.dropped_frames >= 1 and loop.inferred_frames >= 2)
    await loop.stop()
    result = loop.status()
    assert result['captured_frames'] > result['inferred_frames'] >= 1
    assert result['dropped_frames'] >= 1 and result['p95_inference_ms'] >= 60
    assert result['executed_actions'] == 0 and result['last_prediction'] is None
    await loop.close()


async def test_stale_prediction_is_discarded_not_published():
    source = Source()
    def slow(image, goal):
        time.sleep(.06)
        return prediction(image, goal)
    loop = VisualLoop(source, eligible=lambda: True, predictor=slow)
    loop.max_frame_age = .01
    await loop.start('fixture', capture_hz=20, inference_hz=20, duration_seconds=2)
    await wait_for(lambda: loop.stale_frames + loop.stale_predictions >= 1)
    await loop.stop()
    assert loop.status()['stale_frames'] + loop.status()['stale_predictions'] >= 1
    assert loop.last_prediction is None and loop.status()['executed_actions'] == 0


async def test_stop_waits_for_owned_capture_and_clears_frames_before_restart():
    source = Source()
    source.block = Event()
    loop = VisualLoop(source, eligible=lambda: True)
    await loop.start('first')
    await wait_for(source.started.is_set)
    stopping = asyncio.create_task(loop.stop())
    await asyncio.sleep(.02)
    assert not stopping.done() and loop.status()['executed_actions'] == 0
    source.block.set()
    await stopping
    assert not loop.running and source.closes == 1 and loop._latest is None
    generation = loop.generation
    await loop.start('second', duration_seconds=.1)
    assert loop.generation > generation
    await loop.stop()
    assert loop._latest is None
    await loop.close()


async def test_immediate_stop_and_closed_loop_never_leak_cancelled_error():
    source = Source()
    loop = VisualLoop(source, eligible=lambda: True)
    await loop.start()
    await loop.stop()
    assert not loop.running and source.calls == 0
    await loop.close()
    with pytest.raises(ValueError):
        await loop.start()


async def test_preparation_delay_has_no_capture_or_worker_and_stop_never_revives_it():
    source = Source()
    loop = VisualLoop(source, eligible=lambda: True)
    state = await loop.start('fixture', start_delay_seconds=.2, duration_seconds=.1)
    assert state['phase'] == 'starting' and '准备期不截图' in state['message']
    assert state['capture_fps'] == 0 and state['elapsed_seconds'] == 0
    await asyncio.sleep(.02)
    assert source.calls == source.closes == 0
    assert loop.started is None and loop.status()['preparing_remaining_seconds'] > 0
    await loop.stop()
    await asyncio.sleep(.22)
    assert source.calls == source.closes == 0 and loop._latest is None
    assert not loop.running and loop.status()['elapsed_seconds'] == 0


async def test_preparation_time_is_not_counted_as_sampling_duration():
    source = Source()
    loop = VisualLoop(source, eligible=lambda: True)
    await loop.start('fixture', start_delay_seconds=.1, duration_seconds=.1)
    await loop.task
    assert loop.started - loop.prepared_at >= .08
    assert loop.status()['elapsed_seconds'] == round(loop.ended - loop.started, 2)
    assert (loop.ended - loop.prepared_at) - (loop.ended - loop.started) >= .08
    assert source.calls > 0


@pytest.mark.parametrize('change', ['scope', 'permission', 'privacy'])
async def test_changed_scope_permission_or_privacy_stops_without_resuming(change):
    source, allowed = Source(), [True]
    loop = VisualLoop(source, eligible=lambda: allowed[0])
    await loop.start('fixture', duration_seconds=2)
    await wait_for(lambda: loop.captured_frames > 0)
    if change == 'scope': source.scope = 'other-window'
    if change == 'permission': allowed[0] = False
    if change == 'privacy': source.private = True
    await loop.task
    assert loop.phase == 'interrupted' and loop._latest is None
    assert source.closes == 1 and loop.status()['executed_actions'] == 0
    calls = source.calls
    await asyncio.sleep(.04)
    assert source.calls == calls


async def test_initial_self_or_private_window_waits_without_fabricated_frames():
    source = Source()
    source.private = True
    loop = VisualLoop(source, eligible=lambda: True)
    await loop.start('fixture', duration_seconds=2)
    await wait_for(lambda: loop.privacy_skips > 0)
    assert loop.phase == 'waiting' and loop.captured_frames == 0
    source.private = False
    await wait_for(lambda: loop.captured_frames > 0)
    await loop.stop()
    assert loop._latest is None


@pytest.mark.parametrize('result', [
    {'proposed_action': 'click', 'normalized_xy': [.5, .5], 'confidence': .8, 'execution_permitted': True},
    {'proposed_action': 'click', 'normalized_xy': [1.1, .5], 'confidence': .8, 'execution_permitted': False},
    {'proposed_action': 'noop', 'normalized_xy': [.5, .5], 'confidence': .8, 'execution_permitted': False},
])
async def test_invalid_or_execution_claiming_prediction_stops_shadow_run(result):
    source = Source()
    loop = VisualLoop(source, eligible=lambda: True, predictor=lambda *_: result)
    await loop.start('fixture', duration_seconds=2)
    await loop.task
    assert loop.phase == 'error' and loop.last_prediction is None
    assert loop.status()['executed_actions'] == 0
