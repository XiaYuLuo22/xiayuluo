"""Opt-in local shadow sampling: bounded latest-frame handoff, never input control.

The separate capture and analysis workers own no mouse/keyboard executor. A
prediction is only a diagnostic, even when an offline checkpoint is loaded.
"""
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math
import time
from uuid import uuid4


@dataclass(frozen=True)
class VisualFrame:
    id: str
    generation: int
    captured_at: float
    image: object
    width: int
    height: int
    scope_id: str


class VisualLoopInterrupted(ValueError):
    pass


def _number(value, minimum, maximum, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(label)
    return float(value)


def _p95(values):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * .95) - 1)] * 1000, 2)


class VisualLoop:
    """One bounded run, explicit start, RAM-only frames and no network model calls.

    ``source.capture()`` returns an owned PIL RGB image plus width, height and
    scope_id. ``predictor.predict(image, goal)`` (or callable) is synchronous and
    runs only in its analysis worker. Its outputs must be offline-only proposals.
    The owner injects ``eligible`` to revoke the loop during local work, updates,
    native-window closure or loss of the separate mouse-control grant.
    """
    def __init__(self, source=None, *, eligible=lambda: False, predictor=None, clock=time.monotonic):
        if source is None:
            from .visual_capture import MSSFrameSource
            source = MSSFrameSource()
        self.source, self.eligible, self.predictor, self.clock = source, eligible, predictor, clock
        self.lifecycle = asyncio.Lock()
        self.task = None
        self.running = self.closed = False
        self.generation = 0
        self.phase = 'off'
        self.message = '本地视觉循环默认关闭；只做影子观测，不执行鼠标。'
        self.started = self.ended = self.latest_captured_at = None
        self.prepared_at = None
        self.start_delay_seconds = 0.
        self.goal = ''
        self.capture_hz, self.inference_hz = 20., 10.
        self.duration_seconds, self.max_frame_age = 30., .5
        self._latest = None
        self._scope = None
        self.last_prediction = None
        self.change_score = None
        self._reset_metrics()

    def _reset_metrics(self):
        self.captured_frames = self.processed_frames = self.inferred_frames = 0
        self.dropped_frames = self.stale_frames = self.stale_predictions = self.privacy_skips = 0
        self.capture_times, self.inference_times, self.analysis_times = (deque(maxlen=256) for _ in range(3))

    def _allowed(self, generation):
        if not self.running or self.closed or generation != self.generation:
            raise VisualLoopInterrupted('视觉循环已停止，旧画面与预测已撤销。')
        try:
            eligible = self.eligible()
        except Exception:
            eligible = False
        if eligible is not True:
            raise VisualLoopInterrupted('本机任务、窗口或授权已变化，视觉循环已让出资源。')

    def status(self):
        now = self.clock()
        end = now if self.running or self.ended is None else self.ended
        duration = max(0., end - self.started) if self.started is not None else 0.
        available, reason = self.source.available()
        return {'phase': self.phase, 'running': self.running, 'message': self.message,
                'available': bool(available), 'reason': reason,
                'policy_configured': self.predictor is not None,
                'policy_ready': self.predictor is not None and getattr(self.predictor, 'ready', True) is True,
                'real_model_ready': False,
                'execution_permitted': False, 'executed_actions': 0, 'scope': 'local-shadow-only',
                'generation': self.generation, 'target_capture_hz': self.capture_hz,
                'target_inference_hz': self.inference_hz, 'duration_seconds': self.duration_seconds,
                'start_delay_seconds': self.start_delay_seconds,
                'preparing_remaining_seconds': (round(max(0., self.prepared_at + self.start_delay_seconds - now), 1)
                    if self.running and self.phase == 'starting' and self.prepared_at is not None else 0.),
                'elapsed_seconds': round(duration, 2),
                'capture_fps': round(self.captured_frames / duration, 2) if duration else 0.,
                'inference_fps': round(self.inferred_frames / duration, 2) if duration else 0.,
                'processed_fps': round(self.processed_frames / duration, 2) if duration else 0.,
                'captured_frames': self.captured_frames, 'processed_frames': self.processed_frames,
                'inferred_frames': self.inferred_frames, 'dropped_frames': self.dropped_frames,
                'stale_frames': self.stale_frames, 'stale_predictions': self.stale_predictions,
                'privacy_skips': self.privacy_skips,
                'p95_capture_ms': _p95(self.capture_times), 'p95_inference_ms': _p95(self.inference_times),
                'p95_analysis_ms': _p95(self.analysis_times),
                'latest_frame_age_ms': (round(max(0., now - self.latest_captured_at) * 1000, 2)
                                        if self.running and self.latest_captured_at is not None else None),
                'change_score': self.change_score, 'last_prediction': self.last_prediction}

    async def start(self, goal='', *, capture_hz=20, inference_hz=10, duration_seconds=30,
                    start_delay_seconds=0):
        capture_hz = _number(capture_hz, 1, 30, '采样频率必须在1–30Hz之间。')
        inference_hz = _number(inference_hz, 1, 30, '本地分析频率必须在1–30Hz之间。')
        duration_seconds = _number(duration_seconds, .1, 120, '单次观测时段必须在0.1–120秒之间。')
        start_delay_seconds = _number(start_delay_seconds, 0, 5, '准备时间必须在0–5秒之间。')
        if not isinstance(goal, str) or len(goal.strip()) > 128 or any(ord(char) < 32 for char in goal):
            raise ValueError('请提供不超过128字的单行观测目标。')
        async with self.lifecycle:
            if self.closed or self.task and not self.task.done():
                raise ValueError('视觉循环已关闭或已有运行，请先停止。')
            available, reason = self.source.available()
            if not available:
                raise ValueError(reason)
            try:
                allowed = self.eligible()
            except Exception:
                allowed = False
            if allowed is not True:
                raise ValueError('请在空闲的电脑原生App中开启鼠标控制后开始本机影子测试。')
            self.generation += 1
            self.goal, self.capture_hz, self.inference_hz = goal.strip(), capture_hz, inference_hz
            self.duration_seconds = duration_seconds
            self.start_delay_seconds = start_delay_seconds
            self._reset_metrics()
            self._latest = self._scope = self.last_prediction = self.change_score = None
            self.latest_captured_at = self.ended = None
            self.prepared_at, self.started, self.running = self.clock(), None, True
            self.phase = 'starting' if start_delay_seconds else ('shadow' if self.predictor is not None else 'observing')
            self.message = (f'准备开始：请在{start_delay_seconds:g}秒内切换到目标窗口；准备期不截图。'
                            if start_delay_seconds else '正在启动本地影子观测；没有鼠标执行权限。')
            self.task = asyncio.create_task(self._run(self.generation))
            return self.status()

    @staticmethod
    async def _offload(executor, callback, *arguments):
        # Cancellation revokes result use immediately, but still waits for the
        # one owned synchronous call before closing its thread-affine resource.
        future = asyncio.get_running_loop().run_in_executor(executor, callback, *arguments)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(future)
            except Exception:
                pass
            raise

    async def _capture(self, generation, executor):
        from .proactive import ObservationSkipped
        deadline = self.clock()
        while True:
            self._allowed(generation)
            started = self.clock()
            try:
                value = await self._offload(executor, self.source.capture)
            except ObservationSkipped:
                self._allowed(generation)
                self.privacy_skips += 1
                self._latest = None
                if self._scope is not None:
                    raise VisualLoopInterrupted('当前窗口不再适合观察，已停止并清除画面。') from None
                self.phase, self.message = 'waiting', '等待非助手、非隐私的前台窗口；未采集有效画面。'
            else:
                self._allowed(generation)
                finished = self.clock()
                image, width, height, scope = value['image'], value['width'], value['height'], value['scope_id']
                if (type(width) is not int or type(height) is not int or not 16 <= width <= 16384
                        or not 16 <= height <= 16384 or width * height > 16_000_000
                        or getattr(image, 'size', None) != (width, height) or getattr(image, 'mode', None) != 'RGB'
                        or not isinstance(scope, str) or not 1 <= len(scope) <= 128):
                    raise ValueError('Invalid local frame.')
                if self._scope is not None and self._scope != scope:
                    raise VisualLoopInterrupted('前台窗口或坐标范围已变化，旧观测目标已停止。')
                self._scope = scope
                if self._latest is not None:
                    self.dropped_frames += 1
                self.captured_frames += 1
                self.capture_times.append(max(0., finished - started))
                self.latest_captured_at = started
                self._latest = VisualFrame(uuid4().hex, generation, started, image, width, height, scope)
                self.phase = 'shadow' if self.predictor is not None else 'observing'
                self.message = ('本地影子预测中；无鼠标执行权限。' if self.predictor is not None
                                else '本地观测中；未加载动作策略，没有执行任何鼠标操作。')
            deadline = max(deadline + 1 / self.capture_hz, self.clock())
            await asyncio.sleep(max(0., deadline - self.clock()))

    @staticmethod
    def _prediction(value):
        if (not isinstance(value, dict) or value.get('execution_permitted') is not False
                or value.get('proposed_action') not in ('click', 'move', 'noop')):
            raise ValueError('Invalid shadow prediction.')
        confidence = _number(value.get('confidence'), 0, 1, 'Invalid confidence.')
        coordinates = value.get('normalized_xy')
        if value['proposed_action'] == 'noop':
            if coordinates is not None:
                raise ValueError('Invalid noop coordinates.')
        else:
            if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2:
                raise ValueError('Invalid coordinates.')
            coordinates = [_number(item, 0, 1, 'Invalid coordinates.') for item in coordinates]
        return {'proposed_action': value['proposed_action'], 'normalized_xy': coordinates,
                'confidence': confidence, 'execution_permitted': False}

    async def _analyse(self, generation, executor):
        from .visual_capture import compare_frames
        previous = None
        deadline = self.clock()
        try:
            while True:
                self._allowed(generation)
                frame, self._latest = self._latest, None
                if frame is not None:
                    if frame.generation != generation or self.clock() - frame.captured_at > self.max_frame_age:
                        self.stale_frames += 1
                    else:
                        started = self.clock()
                        score = None if previous is None else await self._offload(executor, compare_frames, previous, frame.image)
                        self._allowed(generation)
                        self.processed_frames += 1
                        self.analysis_times.append(max(0., self.clock() - started))
                        self.change_score, previous = score, frame.image
                        if self.predictor is not None:
                            predict = getattr(self.predictor, 'predict', self.predictor)
                            started = self.clock()
                            result = await self._offload(executor, predict, frame.image, self.goal)
                            self._allowed(generation)
                            self.inferred_frames += 1
                            self.inference_times.append(max(0., self.clock() - started))
                            result = self._prediction(result)
                            if self.clock() - frame.captured_at > self.max_frame_age:
                                self.stale_predictions += 1
                            else:
                                self.last_prediction = {**result, 'frame_id': frame.id, 'generation': generation}
                deadline = max(deadline + 1 / self.inference_hz, self.clock())
                await asyncio.sleep(max(0., deadline - self.clock()))
        finally:
            previous = None

    async def _run(self, generation):
        capture_executor = analysis_executor = None
        children = []
        try:
            if self.start_delay_seconds:
                await asyncio.sleep(self.start_delay_seconds)
            self._allowed(generation)
            self.started = self.clock()
            self.phase = 'shadow' if self.predictor is not None else 'observing'
            capture_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='xiayuluo-capture')
            analysis_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='xiayuluo-local-policy')
            children = [asyncio.create_task(self._capture(generation, capture_executor)),
                        asyncio.create_task(self._analyse(generation, analysis_executor))]
            done, _ = await asyncio.wait(children, timeout=self.duration_seconds, return_when=asyncio.FIRST_COMPLETED)
            for child in done:
                child.result()
            self.phase, self.message = 'completed', '本次观测时段结束；未执行鼠标，不代表任务已完成。'
        except asyncio.CancelledError:
            self.phase, self.message = 'off', '视觉循环已停止；旧画面和预测已清除，没有鼠标动作。'
        except VisualLoopInterrupted as error:
            self.phase, self.message = 'interrupted', str(error)
        except Exception:
            self.phase, self.message = 'error', '本地采集或策略分析失败，已停止；未执行鼠标，原模型保留。'
        finally:
            self.running = False
            for child in children:
                if not child.done() and not child.cancelling():
                    child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            self._latest = self._scope = self.last_prediction = None
            try:
                if capture_executor is not None:
                    await self._offload(capture_executor, self.source.close)
                close = getattr(self.predictor, 'close', None)
                if close is not None and analysis_executor is not None:
                    await self._offload(analysis_executor, close)
            except Exception:
                self.phase, self.message = 'error', '本地视觉资源未能正常关闭；请关闭App后核实。'
            finally:
                if capture_executor is not None:
                    capture_executor.shutdown(wait=True, cancel_futures=True)
                if analysis_executor is not None:
                    analysis_executor.shutdown(wait=True, cancel_futures=True)
                self.ended = self.clock()

    async def stop(self):
        async with self.lifecycle:
            self.generation += 1
            self.running = False
            self._latest = self.last_prediction = None
            if self.task is not None:
                self.phase, self.message = 'stopping', '正在停止本次采集与推理，等待自有资源清理。'
                if not self.task.done() and not self.task.cancelling():
                    self.task.cancel()
                try:
                    await asyncio.shield(self.task)
                except asyncio.CancelledError:
                    # A stop immediately after start may cancel before _run has
                    # entered its own try/finally; that is a completed stop.
                    if not self.task.done():
                        await asyncio.shield(self.task)
                        raise
                    if not self.task.cancelled():
                        raise
                self.task = None
            if not self.closed and self.phase != 'error':
                self.phase, self.message = 'off', '视觉循环已关闭；不会继续截图或执行鼠标。'
            return self.status()

    async def interrupt_and_wait(self):
        return await self.stop()

    async def close(self):
        await self.stop()
        self.closed = True
