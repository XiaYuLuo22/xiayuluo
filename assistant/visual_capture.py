"""Explicit, RAM-only foreground capture for the local shadow visual loop.

MSS resources are lazy and thread-affine. Privacy-title filtering is best effort,
not proof that every visible pixel is safe. Nothing here executes mouse actions.
"""
import hashlib
import importlib.util
import json
import os
import sys
from threading import get_ident

from .proactive import ObservationSkipped, WindowsScreenObserver


MAX_PIXELS = 16_000_000


class MSSFrameSource:
    def __init__(self, enabled=False, *, platform=None, window_info=None, mss_factory=None):
        if type(enabled) is not bool:
            raise ValueError('视觉采集开关必须是布尔值。')
        self.enabled = enabled
        self.platform = platform or sys.platform
        self.window_info = window_info or WindowsScreenObserver._window_info
        self.factory = mss_factory
        self.source = None
        self.owner_thread = None

    def available(self):
        if self.platform != 'win32':
            return False, '本地视觉采集仅在 Windows 原生 App 可用。'
        if not self.enabled:
            return False, '请先在本机启用 DESKTOP_ENABLED 并重启。'
        try:
            if (importlib.util.find_spec('PIL') is None or importlib.util.find_spec('cv2') is None) or (
                    self.factory is None and importlib.util.find_spec('mss') is None):
                return False, '缺少本地视觉采集组件，请安装支持视觉循环的版本。'
        except (ImportError, ValueError):
            return False, '本地视觉采集组件无法安全加载。'
        return True, ''

    def _thread(self):
        if self.owner_thread is not None and self.owner_thread != get_ident():
            raise ValueError('视觉采集资源必须在同一专用线程中使用和关闭。')

    @staticmethod
    def _info(info):
        if (not isinstance(info, dict) or set(info) != {'title', 'pid', 'window', 'rect'}
                or not isinstance(info['title'], str) or len(info['title']) > 1024
                or type(info['pid']) is not int or info['pid'] < 1
                or type(info['window']) is not int or info['window'] < 1
                or not isinstance(info['rect'], (tuple, list)) or len(info['rect']) != 4
                or any(type(value) is not int or abs(value) > 1_000_000 for value in info['rect'])):
            raise ObservationSkipped('无法安全核实前台窗口范围。')
        if info['pid'] == os.getpid() or WindowsScreenObserver.PRIVATE_TITLE.search(info['title']):
            raise ObservationSkipped('当前窗口不参与本地视觉采集。')
        # Do not retain a mutable dictionary owned by a window-info adapter.
        return {**info, 'rect': tuple(info['rect'])}

    def _open(self):
        self._thread()
        if self.source is None:
            factory = self.factory
            if factory is None:
                import mss
                factory = mss.mss
            self.source = factory()
            self.owner_thread = get_ident()
        return self.source

    def capture(self):
        ready, reason = self.available()
        if not ready:
            raise ValueError(reason)
        self._thread()
        info = self._info(self.window_info())
        source = self._open()
        monitors = source.monitors
        if not isinstance(monitors, (tuple, list)) or len(monitors) < 2:
            raise ObservationSkipped('主屏幕信息不可用。')
        # Windows primary display has desktop origin (0, 0). MSS lists display
        # enumeration order, which is not guaranteed to put primary at index 1.
        primary = [monitor for monitor in monitors[1:] if isinstance(monitor, dict)
                   and monitor.get('left') == 0 and monitor.get('top') == 0]
        if len(primary) != 1:
            raise ObservationSkipped('无法唯一核实主屏幕，已停止采集。')
        monitor = primary[0]
        if (not isinstance(monitor, dict) or any(type(monitor.get(key)) is not int
                for key in ('left', 'top', 'width', 'height'))
                or not 1 <= monitor['width'] <= 32_000 or not 1 <= monitor['height'] <= 32_000
                or abs(monitor['left']) > 1_000_000 or abs(monitor['top']) > 1_000_000):
            raise ObservationSkipped('无法安全核实主屏幕尺寸。')
        left, top, right, bottom = info['rect']
        left, top = max(left, monitor['left']), max(top, monitor['top'])
        right = min(right, monitor['left'] + monitor['width'])
        bottom = min(bottom, monitor['top'] + monitor['height'])
        width, height = right - left, bottom - top
        if width < 80 or height < 80 or width * height > MAX_PIXELS:
            raise ObservationSkipped('前台窗口不在允许的主屏幕采集范围。')
        shot = source.grab({'left': left, 'top': top, 'width': width, 'height': height})
        if tuple(shot.size) != (width, height):
            raise ObservationSkipped('采集画面与窗口尺寸不匹配，已丢弃。')
        raw = shot.bgra
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != width * height * 4:
            raise ObservationSkipped('采集画面字节无效，已丢弃。')
        from PIL import Image
        image = Image.frombytes('RGB', (width, height), bytes(raw), 'raw', 'BGRX')
        current = self._info(self.window_info())
        if current != info:
            raise ObservationSkipped('前台窗口或范围已变化，本帧已丢弃。')
        scope = [info['pid'], info['window'], list(info['rect']),
                 [monitor[key] for key in ('left', 'top', 'width', 'height')]]
        scope_id = hashlib.sha256(json.dumps(scope, separators=(',', ':')).encode()).hexdigest()
        return {'image': image, 'width': width, 'height': height, 'scope_id': scope_id}

    def close(self):
        self._thread()
        if self.source is not None:
            self.source.close()
        self.source = None
        self.owner_thread = None


def compare_frames(before, after):
    """A bounded pixel-change fraction, never a semantic success detector."""
    from PIL import Image
    if (not isinstance(before, Image.Image) or not isinstance(after, Image.Image)
            or before.size != after.size or before.mode != 'RGB' or after.mode != 'RGB'
            or before.width < 1 or before.height < 1 or before.width * before.height > MAX_PIXELS):
        raise ValueError('本地画面对比要求同尺寸、大小受限的 RGB 图像。')
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise ValueError('缺少 OpenCV 本地图像复核组件。') from None
    # Avoid allocating full-frame NumPy buffers merely to compare tiny previews.
    first = cv2.cvtColor(np.asarray(before.resize((64, 48), Image.Resampling.BILINEAR)), cv2.COLOR_RGB2GRAY)
    second = cv2.cvtColor(np.asarray(after.resize((64, 48), Image.Resampling.BILINEAR)), cv2.COLOR_RGB2GRAY)
    return float(cv2.absdiff(first, second).mean() / 255.0)
