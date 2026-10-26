"""Small, volatile, untrusted activity summaries; never raw screens or window titles."""
from collections import deque
import re
from threading import RLock
import time


CATEGORIES = ('unknown', 'coding', 'reading', 'writing', 'browsing', 'communication', 'media', 'other')
# This is only a secondary, conservative filter, not a promise that a model can
# recognise every piece of personal information. Original images are never kept.
PRIVATE_TEXT = re.compile(r'https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}|'
                          r'[a-z]:[\\/]|(?:^|\s)/[\w.-]+/|\\\\|'
                          r'(?:sk-|ghp_|github_pat_)[a-z0-9_-]+|[a-f0-9]{32,}|'
                          r'\b\d{7,}\b|password\s*[:=]|密码\s*[:：=]', re.I)


class ActivityContext:
    """Bounded RAM-only context. Every entry remains explicitly untrusted data."""
    MAX_ITEMS = 12
    TTL = 900

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.items = deque(maxlen=self.MAX_ITEMS)
        self.lock = RLock()

    def clear(self):
        with self.lock:
            self.items.clear()

    def _prune(self, now):
        while self.items and now - self.items[0][0] >= self.TTL:
            self.items.popleft()

    def record(self, category, activity):
        if category not in CATEGORIES or not isinstance(activity, str):
            return False
        activity = ' '.join(activity.split())
        if not activity or len(activity) > 240 or PRIVATE_TEXT.search(activity):
            return False
        with self.lock:
            now = self.clock()
            self._prune(now)
            if self.items and self.items[-1][1:] == (category, activity):
                self.items[-1] = (now, category, activity)
            else:
                self.items.append((now, category, activity))
        return True

    def context(self):
        with self.lock:
            now = self.clock()
            self._prune(now)
            return [{'category': category, 'activity': activity,
                     'age_seconds': max(0, int(now - created)),
                     'source': 'visible-foreground-window', 'untrusted': True,
                     'confidence': 'limited-observation'}
                    for created, category, activity in self.items]
