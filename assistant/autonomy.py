"""Opt-in, RAM-only goal decisions. No tools, screen capture, or execution.

``transitions`` controls lifecycle, not intelligence. A separately supplied
read-only planner may suggest speech or one next step. Observations are inert,
untrusted context and can never replace the goal explicitly supplied by the user.
"""
import asyncio
from collections import deque
from contextlib import suppress
from copy import deepcopy
import math
import re
from threading import RLock
import time
from uuid import uuid4

try:
    from transitions import Machine
except ImportError:  # Keep ordinary chat usable when optional setup is incomplete.
    Machine = None


KINDS = frozenset(('silence', 'speak', 'ask', 'wait', 'propose_step', 'completed', 'blocked'))
EVENT_KINDS = frozenset(('activity', 'task_result', 'user_message', 'timer', 'window_changed', 'system_notice'))
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


def _text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError('目标或事件文字无效，未改变原目标。')
    return value.strip()


class AutonomyController:
    """Bounded event-driven decisions, never a scheduler or action executor."""
    MAX_EVENTS = 32
    MAX_HISTORY = 20

    def __init__(self, planner=None, *, eligible=lambda: True, clock=time.monotonic,
                 min_interval=15, event_ttl=120, planner_timeout=30,
                 max_decisions_per_hour=12, max_proposals=20, max_goal_age=3600):
        if (type(max_decisions_per_hour) is not int or not 1 <= max_decisions_per_hour <= 60
                or type(max_proposals) is not int or not 1 <= max_proposals <= 100
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (min_interval, event_ttl, planner_timeout, max_goal_age))
                or not 1 <= min_interval <= 3600 or not 1 <= event_ttl <= 900
                or not 1 <= planner_timeout <= 120 or not 1 <= max_goal_age <= 86400
                or (planner is not None and not callable(planner))):
            raise ValueError('主动决策参数无效。')
        self.planner, self.eligible, self.clock = planner, eligible, clock
        self.min_interval, self.event_ttl, self.planner_timeout = min_interval, event_ttl, planner_timeout
        self.max_decisions_per_hour, self.max_proposals, self.max_goal_age = max_decisions_per_hour, max_proposals, max_goal_age
        self.enabled = self.closed = False
        self.generation = 0
        self.goal = None
        self.events = deque(maxlen=self.MAX_EVENTS)
        self.history = deque(maxlen=self.MAX_HISTORY)
        self.seen = deque(maxlen=self.MAX_EVENTS * 4)
        self.budget = deque()
        self.last_started = None
        self.task = None
        self.lock = RLock()
        self._phase = 'off'
        self.message = '主动决策默认关闭；不会自行创建目标或执行电脑操作。'
        self.dropped_events = 0
        self.machine = None
        if Machine is not None:
            self.machine = Machine(model=self, model_attribute='_phase', initial='off',
                states=('off', 'idle', 'waiting', 'thinking', 'speaking', 'asking', 'proposed',
                        'awaiting_verification', 'blocked', 'stopped'), auto_transitions=False,
                transitions=[{'trigger': '_enter_' + state, 'source': '*', 'dest': state}
                    for state in ('off', 'idle', 'waiting', 'thinking', 'speaking', 'asking', 'proposed',
                                  'awaiting_verification', 'blocked', 'stopped')])

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('主动决策时钟无效，已停止处理。')
        return value

    def _set_phase(self, phase, message):
        if self.machine is not None:
            getattr(self, '_enter_' + phase)()
        else:
            self._phase = phase
        self.message = message

    def _allowed(self):
        try:
            return bool(self.eligible())
        except Exception:
            return False

    @staticmethod
    def _cancel(task):
        if task and not task.done():
            try:
                def cancel():
                    if not task.done() and not task.cancelling():
                        task.cancel()
                task.get_loop().call_soon_threadsafe(cancel)
            except RuntimeError:
                pass  # A closed loop cannot produce another accepted decision.

    def _invalidate(self):
        self.generation += 1
        self.events.clear()
        self.seen.clear()
        self._cancel(self.task)

    def _prune(self, now):
        while self.events and now - self.events[0]['created_at'] >= self.event_ttl:
            self.events.popleft()
            self.dropped_events += 1
        while self.budget and now - self.budget[0] >= 3600:
            self.budget.popleft()

    def enable(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('主动决策开关必须是布尔值。')
        with self.lock:
            if enabled and (self.closed or self.machine is None or not self._allowed()):
                raise ValueError('主动决策依赖或本机运行条件尚未就绪，未启用。')
            if enabled != self.enabled:
                self._invalidate()
            self.enabled = enabled
            if not enabled:
                if self.goal and self.goal['status'] == 'active':
                    self.goal['status'] = 'stopped'
                self._set_phase('off', '主动决策已关闭；旧事件和在途决策不自动恢复。')
            elif self.goal and self.goal['status'] == 'active':
                self._set_phase('waiting', '等待新的有效事件；不会因开关变化重放旧操作。')
            else:
                self._set_phase('idle', '已开启主动决策，请明确提供目标和完成标准。')
            return self.status()

    def new_goal(self, goal, completion, session_id=None):
        goal, completion = _text(goal, 2000), _text(completion, 1000)
        if session_id is not None and (not isinstance(session_id, str) or not IDENTIFIER.fullmatch(session_id)):
            raise ValueError('目标会话编号无效。')
        with self.lock:
            if not self.enabled or self.closed or not self._allowed():
                raise ValueError('请先在可用的电脑原生 App 中开启主动决策。')
            now = self._now()
            self._invalidate()
            self.history.clear()
            self.goal = {'id': uuid4().hex, 'goal': goal, 'completion': completion, 'session_id': session_id,
                         'source': 'user-explicit', 'status': 'active', 'current_step': '', 'blocked_reason': '',
                         'result': '', 'proposed_steps': 0, 'created_at': now, 'updated_at': now,
                         'completion_verified': False, 'execution_permitted': False}
            self._set_phase('waiting', '目标已保存到本次运行，等待事件；不会自行调用执行工具。')
            return deepcopy(self.goal)

    def observe(self, kind, summary, event_id=None):
        if not isinstance(kind, str) or kind not in EVENT_KINDS:
            raise ValueError('未知主动决策事件类型。')
        summary = _text(summary, 2000)
        event_id = uuid4().hex if event_id is None else event_id
        if not isinstance(event_id, str) or not IDENTIFIER.fullmatch(event_id):
            raise ValueError('主动决策事件编号无效。')
        with self.lock:
            if (not self.enabled or self.closed or not self._allowed() or self.goal is None
                    or self.goal['status'] != 'active'):
                return False
            now = self._now()
            self._prune(now)
            if event_id in self.seen or (self.events and
                    (self.events[-1]['kind'], self.events[-1]['summary']) == (kind, summary)):
                return False
            if len(self.events) == self.MAX_EVENTS:
                self.dropped_events += 1
            self.events.append({'id': event_id, 'kind': kind, 'summary': summary, 'created_at': now,
                                'untrusted': True, 'authorizes_execution': False})
            self.seen.append(event_id)
            return True

    @staticmethod
    def _decision(value):
        if not isinstance(value, dict) or not {'kind', 'message'} <= set(value) or set(value) - {'kind', 'message', 'step'}:
            raise ValueError('主动决策格式无效，未执行任何操作。')
        if not isinstance(value['kind'], str) or value['kind'] not in KINDS:
            raise ValueError('主动决策类型无效。')
        message = _text(value['message'], 2000, empty=value['kind'] in ('silence', 'wait'))
        step = value.get('step')
        if value['kind'] == 'propose_step':
            step = _text(step, 1000)
        elif step not in (None, ''):
            raise ValueError('只有步骤建议可以包含下一步文字，不接受可执行参数。')
        return {'kind': value['kind'], 'message': message, 'step': step or None}

    async def _plan(self, goal, event):
        return await self.planner(goal, event)

    async def next_decision(self):
        with self.lock:
            now = self._now()
            self._prune(now)
            if (not self.enabled or self.closed or not self._allowed() or not self.goal
                    or self.goal['status'] != 'active' or (self.task and not self.task.done())):
                return None
            if now - self.goal['created_at'] >= self.max_goal_age:
                self.goal['status'], self.goal['blocked_reason'] = 'blocked', '目标已超过本次处理时限。'
                self._set_phase('blocked', self.goal['blocked_reason'])
                return None
            if self.planner is None:
                self._set_phase('waiting', '尚未连接只读决策规划器，事件仅在内存排队，不会假装自主执行。')
                return None
            if (not self.events or len(self.budget) >= self.max_decisions_per_hour
                    or (self.last_started is not None and now - self.last_started < self.min_interval)):
                return None
            event = self.events.popleft()
            generation = self.generation
            goal = deepcopy(self.goal)
            self.budget.append(now)
            self.last_started = now
            self._set_phase('thinking', '正在根据明确目标和不可信事件做只读判断，尚未执行操作。')
            task = self.task = asyncio.create_task(self._plan(deepcopy(goal), deepcopy(event)))
            deadline = asyncio.get_running_loop().time() + self.planner_timeout
        try:
            async with asyncio.timeout(self.planner_timeout):
                raw = await task
            # An adapter can accidentally swallow cancellation. Never accept its
            # late answer merely because asyncio.timeout then sees no exception.
            if asyncio.get_running_loop().time() >= deadline:
                raise ValueError('主动决策已超时，迟到结果不会发布。')
            value = self._decision(raw)
            with self.lock:
                if (generation != self.generation or not self.enabled or not self._allowed()
                        or self.goal is None or self.goal['id'] != goal['id']):
                    return None
                now = self._now()
                if now - event['created_at'] >= self.event_ttl:
                    self.dropped_events += 1
                    self._set_phase('waiting', '决策对应事件已过期，结果已丢弃，不自动重放。')
                    return None
                decision = {**value, 'id': uuid4().hex, 'goal_id': goal['id'], 'event_id': event['id'],
                            'created_at': now, 'source': 'read-only-planner', 'execution_permitted': False,
                            'completion_verified': False, 'requires_explicit_execution': True}
                phase = {'silence': 'waiting', 'wait': 'waiting', 'speak': 'speaking', 'ask': 'asking',
                         'propose_step': 'proposed', 'completed': 'awaiting_verification', 'blocked': 'blocked'}[value['kind']]
                if value['kind'] == 'propose_step':
                    if self.goal['proposed_steps'] >= self.max_proposals:
                        raise ValueError('本次目标的步骤建议已达上限，停止而非无限循环。')
                    self.goal['proposed_steps'] += 1
                    self.goal['current_step'] = value['step']
                elif value['kind'] == 'completed':
                    self.goal['status'], self.goal['result'] = 'awaiting_verification', value['message']
                elif value['kind'] == 'blocked':
                    self.goal['status'], self.goal['blocked_reason'] = 'blocked', value['message']
                self.goal['updated_at'] = now
                self.history.append(decision)
                self._set_phase(phase, '本次只读决策已完成；步骤与完成判断不是实际执行或成功证明。')
                return deepcopy(decision)
        except asyncio.CancelledError:
            with self.lock:
                if generation == self.generation:
                    self._set_phase('waiting', '在途主动决策已中止，消费中的事件不会重放。')
            raise
        except Exception:
            with self.lock:
                if generation == self.generation and self.goal is not None:
                    self.goal['status'], self.goal['blocked_reason'] = 'blocked', '规划器失败或结果无效，未执行操作；请核实后提交新目标。'
                    self._set_phase('blocked', self.goal['blocked_reason'])
            return None
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            with self.lock:
                if self.task is task:
                    self.task = None

    async def stop(self, reason='用户停止'):
        reason = _text(reason, 240)
        with self.lock:
            self._invalidate()
            if self.goal:
                self.goal['status'], self.goal['blocked_reason'] = 'stopped', reason
            self._set_phase('stopped' if self.enabled else 'off', reason + '；旧事件和决策不自动恢复。')
            task = self.task
        if task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await task
        return self.status()

    async def interrupt_and_wait(self, reason='本机操作优先'):
        return await self.stop(reason)

    async def close(self):
        with self.lock:
            self.closed = True
        self.enable(False)
        await self.stop('应用退出')

    def status(self):
        with self.lock:
            self._prune(self._now())
            goal = None if self.goal is None else {key: self.goal[key] for key in
                ('id', 'status', 'proposed_steps', 'completion_verified', 'execution_permitted')}
            return {'available': self.machine is not None and not self.closed,
                    'usable': self.machine is not None and self.planner is not None and not self.closed,
                    'enabled': self.enabled, 'phase': self._phase, 'message': self.message,
                    'needs_planner': self.planner is None, 'generation': self.generation, 'goal': goal,
                    'pending_events': len(self.events), 'dropped_events': self.dropped_events,
                    'budget_remaining': max(0, self.max_decisions_per_hour - len(self.budget)),
                    'min_interval_seconds': self.min_interval, 'event_ttl_seconds': self.event_ttl,
                    'scope': 'explicit-goal-read-only-decisions', 'executes_actions': False,
                    'persistent': False, 'replays_events': False}

    def export(self):
        with self.lock:
            return {'schema': 1, 'status': self.status(), 'goal': deepcopy(self.goal),
                    'events': deepcopy(list(self.events)), 'decisions': deepcopy(list(self.history))}
