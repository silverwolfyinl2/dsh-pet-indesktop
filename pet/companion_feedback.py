"""Local interaction feedback and game-task receipts, without model calls."""
from collections import OrderedDict
import math
import time


class TouchFeedback:
    TEXTS = {'head': '嘿嘿，摸到头了。', 'poke': '在呢，陪着你。',
             'drag': '要搬家啦？', 'drop': '好，松手啦。'}

    def __init__(self):
        self._last = {}

    def react(self, kind, *, now):
        if kind not in self.TEXTS or now - self._last.get(kind, -math.inf) < 3:
            return None
        self._last[kind] = now
        return self.TEXTS[kind]


def show_touch(window, kind, *, now=None):
    now = time.monotonic() if now is None else now
    if not window.cfg.get('companion_touch_feedback', True):
        return False
    if (getattr(window, '_sticky_bubble_active', False)
            or getattr(window, '_alert_current', None) is not None
            or getattr(window, '_alert_queue', None)
            or now < getattr(window, '_companion_task_until', 0)
            or now < getattr(window, '_bubble_busy_until', 0)):
        return False
    if not getattr(window, 'isVisible', lambda: True)():
        return False
    feed = getattr(window, '_companion_touch', None)
    if feed is None:
        feed = window._companion_touch = TouchFeedback()
    text = feed.react(kind, now=now)
    if not text:
        return False
    window.show_bubble(text, duration_ms=1800)
    return True


class ReceiptGate:
    def __init__(self):
        self._runs = OrderedDict()

    def consume(self, row, *, now):
        if not isinstance(row, dict) or row.get('event') != 'companion/task' or row.get('version') != 1:
            return None
        if row.get('agent') != 'game-copilot':
            return None
        try:
            age = now - float(row['ts'])
            seq = int(row['seq'])
            hwnd = int(row['hwnd'])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(age) or not -5 <= age <= 60 or seq < 1 or hwnd < 1:
            return None
        run_id = str(row.get('run_id') or '')[:100]
        if not run_id or not row.get('event_id'):
            return None
        key = (run_id, hwnd)
        previous = self._runs.get(key, (0, False))
        if previous[1] or seq <= previous[0]:
            return None
        stage = row.get('stage')
        if stage not in ('started', 'accepted', 'purchase', 'progress', 'checking', 'finished', 'failed'):
            return None
        name = ' '.join(str(row.get('character') or '当前角色').split())[:30]
        ring = row.get('ring')
        if stage == 'progress' and (type(ring) is not int or not 1 <= ring <= 5):
            return None
        if stage == 'started':
            text = f'{name}的修炼任务开始处理，先确认接取。'
        elif stage == 'accepted':
            text = f'{name}已接取修炼任务，开始跑环。'
        elif stage == 'purchase':
            text = f'{name}正在准备修炼任务要的物品。'
        elif stage == 'progress':
            text = f'{name}已推进到第{ring}环。'
        elif stage == 'checking':
            text = f'{name}这一环有点久，正在检查当前步骤。'
        elif stage == 'failed':
            text = f'{name}的任务已停止，还没确认完成。'
        elif row.get('status') == 'completed' and row.get('verified') is True:
            text = f'{name}的第五环已确认完成！'
        elif row.get('status') == 'partial':
            text = f'{name}已完成这次指定的推进次数。'
        else:
            text = f'{name}这轮尚未确认完成，先停在这里。'
        terminal = stage in ('finished', 'failed')
        self._runs[key] = (seq, terminal)
        self._runs.move_to_end(key)
        while len(self._runs) > 32:
            self._runs.popitem(last=False)
        important = terminal or stage == 'checking'
        return {'text': text, 'important': important,
                'state': 'idle' if terminal else 'working'}


def handle_receipt(manager, row):
    if not manager.cfg.get('companion_task_feedback', True):
        return False
    gate = getattr(manager, '_companion_receipts', None)
    if gate is None:
        gate = manager._companion_receipts = ReceiptGate()
    result = gate.consume(row, now=manager._clock())
    if result is None:
        return False
    cancel = getattr(manager, '_cancel_done_check', None)
    if callable(cancel):
        cancel('game-copilot')
    manager._last_raw['game-copilot'] = result['state']
    window = manager.win
    acts = list(getattr(window, 'cats', {}).get('acts', []))
    if result['state'] == 'working':
        pool = [name for name in acts if any(word in name for word in ('工作', '写', '跑', '思考'))] or acts
    else:
        pool = list(getattr(window, 'idles', []))
    request = getattr(window, 'request_link_anim', None)
    if pool and callable(request) and getattr(window, 'isVisible', lambda: True)():
        request(pool[0])
    duration = 5500 if result['important'] else 3200
    window._companion_task_until = time.monotonic() + duration / 1000
    manager._show_link_bubble(result['text'], important=result['important'], duration_ms=duration)
    return True
