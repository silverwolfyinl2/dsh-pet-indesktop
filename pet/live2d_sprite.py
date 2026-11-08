# -*- coding: utf-8 -*-
"""Live2D 角色层（P1 最小可用）：独立无边框透明窗口 + sprite 协议代理。

设计依据：``docs/桌宠-Live2D与陪伴模式-设计定稿-20261107.md`` D1 / D2 / D8。
D2 拍死方案 A：**Live2D 跑在自己的无边框透明窗口里，引擎只推位置**，不嵌进
合成窗（Chromium 子控件黑底风险）。因此本模块只有三件事：

1. ``Live2DWindow`` —— 一张独立的 ``QWebEngineView`` 窗，加载
   ``refs/live2d/web/index.html``（运行页，T-304 已在此页藏掉 Part46 并加了
   ``__pauseRender``）。窗口**整窗鼠标穿透**（``WA_TransparentForMouseEvents``）：
   点击/拖拽不跨窗口转发，直接落到底下的合成窗，由 ``Live2DSprite.alpha_at``
   的遮罩给出命中——这是把 D2 认账的「跨窗口转发」代价压到零的做法。
2. ``Live2DSprite`` —— 喂给引擎的**代理对象**：``_sprite_factory`` 的协议全满足，
   但**自己什么也不画**（``paint`` 是 no-op），对内持有一个 ``PetSprite`` 只当
   **几何内核**（pos/rect/body_rect/钳制/碰撞口径全用它，保证与 webm 那条路径
   一 px 不差），对外把全局矩形推给 Live2D 窗。
3. ``Live2DRenderer`` —— 进程内单例：本地 http 服务（``file://`` 下 fetch 模型
   json 会被 CORS 拦）+ 窗口收口 + 停表。

三条铁律（T-301/T-302 实测，别踩二次）：

- 更新路径只用 ``internalModel.update(0, vnow)``，**不许走 coreModel 那一层的
  update()**——后者整帧重排会把角色挤回 10,869 px 的旧形态（T-301）。本模块不自己写
  更新循环，画面推进交给运行页的 PIXI ticker（它内部就是 ``internalModel.update``）。
- **不可见 / 全屏时停渲染**：杠杆是运行页的 ``__pauseRender(true)``
  （= ``app.ticker.stop()``）。判定源复用引擎现成的
  ``pet.platform_win._fg_fullscreen_probe()``，不新造全屏检测。
- ``pet_sprite.py`` / ``overlay_window.py`` / ``sprite_behavior.py`` /
  ``window_alerts.py`` / ``vision.py`` 五个文件零改动：本模块只**读**它们。

T-305（尺寸联动 / 待机冻帧 / 窗口上限）改动只发生在三处：

1. **尺寸不再写死**。窗口 = ``CANVAS_W*scale × (CANVAS_H+PAD)*scale``（与
   ``pet/window.py:_apply_scale`` 逐字同口径），``scale`` 由调用方传（
   ``acquire(scale=...)``）。此前 ``acquire()`` 不传参、窗口恒 520×760，与宠物的
   ``scale``/``body_rect`` 完全不挂钩——这是本卡修的第一个硬缺陷。
2. **待机冻帧**。判定在页面（``web/index.html:__idleCheck``，它才知道 ticker 与
   动作状态），驱动在 Qt（本文件的 2 s 心跳调 ``__idleCheck``）。为什么不在页面
   里挂 ``setInterval``：后台/被节流的窗口会停 JS 定时器，冻帧就永远不触发；Qt
   心跳走的是主线程 timer，不受页面节流影响。反向的失效面（Qt 侧看不见"正在
   播动作"）由页面自己回报 ``frozen`` 兜住。
3. **窗口数上限** ``MAX_WINDOWS``：单张窗实测 +861 MiB 显存（
   ``R-20261107-304-workbuddy.md`` 判据 14），8 G 卡只够一张。满额后 ``acquire``
   返回 ``None``，调用方退回 webm——**不做「一只 Live2D 一只 webm」的混搭**。
"""

from __future__ import annotations

import functools
import http.server
import json
import logging
import os
import socketserver
import threading
import time
from pathlib import Path
from typing import Optional

# QtWebEngine 的 Chromium 旗标必须在 ``QtWebEngineWidgets`` 被 import 之前落进环境，
# 之后写无效（与 refs/live2d/run_poc.py 同口径）。setdefault：外部已设置时不覆盖。
# ``PET_LIVE2D_NO_SANDBOX=1`` 是**受限 shell** 里的逃生口：工具沙箱会拦 Chromium
# 的沙箱初始化，renderer 以 exit 18 立刻死掉、整页不加载（T-304 实测）。正常桌面
# 不需要、也不该开——所以它只由环境变量显式打开，不写进默认值。
_CHROMIUM_FLAGS = "--disable-gpu-driver-bug-workarounds --ignore-gpu-blocklist"
if os.environ.get("PET_LIVE2D_NO_SANDBOX"):
    _CHROMIUM_FLAGS = "--no-sandbox " + _CHROMIUM_FLAGS
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _CHROMIUM_FLAGS)

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from . import catalog
from .pet_sprite import PetSprite

logger = logging.getLogger(__name__)

#: 运行页所在目录（``refs/live2d``）。``pet/`` 在 ``refs/dsh-pet-indesktop`` 下，
#: 因此上三级 = ``refs``，再进 ``live2d``。可用环境变量 ``PET_LIVE2D_ROOT`` 覆盖
#: （笔记本同步过去的布局不同时用得上）。
def _live2d_root() -> Path:
    env = os.environ.get("PET_LIVE2D_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "live2d"


LIVE2D_ROOT = _live2d_root()
PAGE_PATH = "/web/index.html"

#: 页面未 ready 的最长等待（毫秒）。模型 16 MB + 三个 js，冷启动实测 < 3 s。
READY_TIMEOUT_MS = 20000

# ---------------------------------------------------------------- 尺寸口径（T-305）
#: 基准逻辑画布（与 ``pet/catalog.py:24-31`` 同一组数，不另立一套）。
CANVAS_W = catalog.CANVAS_W          # 640
CANVAS_H = catalog.CANVAS_H          # 360
PAD = catalog.PAD                    # 30（脚底到窗口底线的留白）
FEET_Y = catalog.FEET_Y              # 330
#: ``scale=1.0`` 时 webm 路径的角色像素高（T-302 实测，见
#: ``R-20261107-302-workbuddy.md``：``y=7..353``）。Live2D 按它对齐，两条路径
#: 才有同一把尺子——否则切到 Live2D 角色会突然变大/变小。
TARGET_H = 346
#: 待机判定阈值（毫秒）：这么久没有任何动作/表情/口型/窗口位移，就冻帧。
IDLE_FREEZE_MS = 6000
#: 同时存在的 Live2D 窗上限。单张 +861 MiB（本机 8 G 卡），故默认只给主宠一张。
MAX_WINDOWS = 1


def window_size(scale: float) -> tuple[int, int]:
    """``scale`` → 窗口宽高（像素）。与 ``pet/window.py:_apply_scale`` 同公式：
    宽 ``CANVAS_W*scale``、高 ``(CANVAS_H+PAD)*scale``。scale=1.5 ⇒ 960×585。"""
    scale = float(scale)
    return (max(1, int(round(CANVAS_W * scale))),
            max(1, int(round((CANVAS_H + PAD) * scale))))



# ------------------------------------------------------------------ 本地 http 服务
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """只服务本地模型文件：不落访问日志（pythonw 无控制台，日志也没人看）。"""

    def log_message(self, fmt, *args):  # noqa: A003 - 覆盖标准库签名
        logger.debug("live2d http: " + fmt, *args)


class Live2DServer:
    """127.0.0.1 上的一次性静态服务（进程内单例，端口由系统分配）。

    为什么不用 ``file://``：页面里 ``Live2DModel.from("../DeepSeek/...json")``
    走 fetch，``file://`` 下 CORS 直接拒（run_poc.py 同因）。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._httpd = None
        self._thread = None
        self.port = 0

    def start(self) -> int:
        if self._httpd is not None:
            return self.port
        handler = functools.partial(_QuietHandler, directory=str(self.root))
        self._httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="live2d-http", daemon=True)
        self._thread.start()
        logger.info("live2d: 静态服务 http://127.0.0.1:%d/ -> %s", self.port, self.root)
        return self.port

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001 - 收口期失败不阻断退出
                logger.debug("live2d: 静态服务收口失败", exc_info=True)


# ------------------------------------------------------------------ 独立窗口
class Live2DWindow(QWebEngineView):
    """一张独立的无边框透明 Live2D 窗。

    - 旗标：``Frameless`` + ``StaysOnTop`` + ``Tool``（不进任务栏）+
      ``DoesNotAcceptFocus``（不抢当前应用焦点，游戏陪玩时这点很要命）；
    - ``WA_TranslucentBackground`` + ``page().setBackgroundColor(0,0,0,0)``：
      两处都要，只设一处在 Windows 上会留一层不透明底（D2 否掉嵌窗的黑底风险，
      独立窗同样要按这个组合验，见 T-304 判据 7 实测）；
    - ``WA_TransparentForMouseEvents``：整窗鼠标穿透。命中判定仍由合成窗做
      （``Live2DSprite.alpha_at`` 给遮罩），所以拖拽/点击行为与 webm 路径一致。
    """

    ready_changed = Signal(bool)

    #: 待机心跳间隔（毫秒）。页面自己也能判冻帧（``__idleCheck``），这里**只负责
    #: 按点敲**：被 Chromium 节流的页面定时器靠不住，Qt 主线程 timer 才靠得住。
    IDLE_POLL_MS = 2000
    #: 已经冻住之后的慢心跳。冻住是稳态，不需要 2 s 一问；叫醒走 ``touch()``
    #: （动作/表情/口型/位移都会显式敲），不靠这里轮询。
    IDLE_POLL_FROZEN_MS = 10000

    def __init__(self, *, scale: float = catalog.DEFAULT_SCALE,
                 width: Optional[int] = None, height: Optional[int] = None,
                 root: Optional[Path] = None, parent=None) -> None:
        super().__init__(parent)
        if QApplication.instance() is None:
            raise RuntimeError("Live2DWindow 必须在 QApplication 之后创建")
        self._root = Path(root) if root is not None else LIVE2D_ROOT
        self._server = Live2DServer(self._root)
        self._ready = False
        self._paused = False
        #: 本窗的宠物缩放（窗口尺寸由它推，不再有 520×760 这种写死的默认）
        self._scale = float(scale)
        w, h = window_size(self._scale)
        self._w = int(width) if width else w
        self._h = int(height) if height else h
        self._idle_timer: Optional[QTimer] = None
        self._idle_state: dict = {"frozen": False, "idleMs": 0}
        self._touch_ts = 0.0
        self._poll = QTimer(self)
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._poll_ready)
        self._poll_deadline = 0.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.resize(QSize(self._w, self._h))

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def window_size_px(self) -> tuple[int, int]:
        return (self._w, self._h)


    # ---------------------------------------------------------------- 装载
    def load_page(self) -> None:
        port = self._server.start()
        self.load(QUrl(f"http://127.0.0.1:{port}{PAGE_PATH}"))
        self._poll_deadline = time.monotonic() + READY_TIMEOUT_MS / 1000.0
        self._poll.start()

    @property
    def is_ready(self) -> bool:
        return self._ready

    def _poll_ready(self) -> None:
        if self._ready:
            self._poll.stop()
            return
        if time.monotonic() > self._poll_deadline:
            self._poll.stop()
            logger.warning("live2d: 页面 %d ms 内未 ready（%s）；若在受限 shell 里跑，"
                           "设 PET_LIVE2D_NO_SANDBOX=1 再试（Chromium 沙箱会被拦）",
                           READY_TIMEOUT_MS, self.url().toString())
            return
        self.run_js("window.pocState ? window.pocState() : '{}'", self._on_state)

    def _on_state(self, payload) -> None:
        try:
            state = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            return
        if state.get("error"):
            logger.error("live2d: 页面报错 %s", str(state["error"])[:400])
        if state.get("ready") and not self._ready:
            self._ready = True
            self._poll.stop()
            logger.info("live2d: 运行页 ready（motions=%s，expressions=%s）",
                        state.get("motions"), state.get("expressions"))
            self._start_idle_watch()
            self.ready_changed.emit(True)

    # ---------------------------------------------------------------- 待机冻帧
    def _start_idle_watch(self) -> None:
        if self._idle_timer is None:
            self._idle_timer = QTimer(self)
            self._idle_timer.setInterval(self.IDLE_POLL_MS)
            self._idle_timer.timeout.connect(self._idle_tick)
        self._idle_timer.start()

    def _idle_tick(self) -> None:
        """心跳：问页面「该冻了吗」。页面自己判、自己冻，这里只负责按点敲。"""
        if self._paused or not self._ready:
            return
        self.run_js("window.__idleCheck ? window.__idleCheck() : '{}'", self._on_idle)

    def _on_idle(self, payload) -> None:
        try:
            state = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            return
        if not isinstance(state, dict):
            return
        self._idle_state = state
        # 冻住之后放慢心跳：稳态下 2 s 一问纯属白烧 CPU（实测这 1.6% 全是主进程
        # 的 event-loop + JS 往返），叫醒另有 touch() 这条显式通道。
        want = self.IDLE_POLL_FROZEN_MS if state.get("frozen") else self.IDLE_POLL_MS
        timer = self._idle_timer
        if timer is not None and timer.interval() != want:
            timer.setInterval(want)

    @property
    def idle_state(self) -> dict:
        """页面回报的冻帧状态 ``{"frozen": bool, "idleMs": int, "gate": bool}``。"""
        return dict(self._idle_state)

    def touch(self) -> None:
        """「有活干了」信号：动作/表情/口型/位移都该敲一下，冻住的立即醒。

        1 s 节流：走位时窗口每帧都在动（60 次/秒），不节流就是每帧一次 JS 往返，
        比冻帧省下来的还贵。待机阈值是 6 s，1 s 的粒度完全够。
        """
        now = time.monotonic()
        if now - self._touch_ts < 1.0:
            return
        self._touch_ts = now
        timer = self._idle_timer
        if timer is not None and timer.interval() != self.IDLE_POLL_MS:
            timer.setInterval(self.IDLE_POLL_MS)
        self.run_js("window.__touch ? window.__touch() : '{}'")


    # ---------------------------------------------------------------- 控制面
    def run_js(self, script: str, callback=None) -> None:
        """在页面里跑一段 js。回调可能**永不触发**（页面已死），故一律一次性的。"""
        if self.page() is None:
            return
        self.page().runJavaScript(script, callback or (lambda _r: None))

    def set_paused(self, paused: bool) -> None:
        """停/续渲染：唯一杠杆是运行页的 ``__pauseRender``（= ``app.ticker.stop()``）。"""
        paused = bool(paused)
        if paused == self._paused:
            return
        self._paused = paused
        self.run_js(f"window.__pauseRender && window.__pauseRender({str(paused).lower()})",
                    lambda r: logger.debug("live2d: __pauseRender(%s) -> %s", paused, r))

    @property
    def is_paused(self) -> bool:
        return self._paused

    def render_once(self, callback=None) -> None:
        """停表后手动出一帧（截屏/换姿态用，不改 ticker 状态）。"""
        self.run_js("window.__forceRender ? window.__forceRender() : 'NO_HOOK'", callback)

    def set_geometry_global(self, rect: QRect) -> None:
        """把窗挪到全局矩形 ``rect``（sprite 的**画布**矩形）并按它定尺寸。

        尺寸口径：宽取 ``rect.width()``（= ``CANVAS_W*scale``），高按
        ``(CANVAS_H+PAD)*scale`` 补出底部留白——sprite 的矩形只到画布底，窗要比它
        高一个 PAD，否则角色脚底会贴着屏幕边缘（webm 路径的窗也是这个比例）。
        """
        scale = rect.width() / float(CANVAS_W) if rect.width() > 0 else self._scale
        _w, h = window_size(scale)
        target = QRect(rect.x(), rect.y(), rect.width(), h)
        if target == self.geometry():
            return
        self.setGeometry(target)
        self._w, self._h = target.width(), target.height()
        self.touch()   # 位置动了 = 有活干，冻住的立即醒

    # ---------------------------------------------------------------- 量测
    def grab_image(self) -> QImage:
        """整窗抓图（含 alpha）。WebGL canvas 的 ``toDataURL`` 在
        ``preserveDrawingBuffer=false`` 下返回空图（run_poc 的 ``_poc_a.png``
        就是这么全透明），所以量测**只认 ``grab()``**。"""
        return self.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)

    def alpha_grid(self, cols: int = 32, rows: int = 48, callback=None) -> None:
        """低分辨率 alpha 网格（命中遮罩的原料）。

        页面里把 webgl canvas 缩绘到离屏 2D canvas 再读 ``getImageData``——
        比整幅 ``toDataURL``（几百 KB base64）便宜两个数量级，够做"点到人身上
        没有"这一档判定。
        """
        # 注意：WebGL canvas 的 framebuffer 在合成后即被丢弃（``preserveDrawingBuffer``
        # 默认 false），所以**必须在同一次 JS 里先 render 一次再 drawImage**，否则读到
        # 的全是 0（T-304 第一次实现就是这么拿到 0/1536 个非零格）。
        js = (
            "(() => {"
            f" const n={int(cols)}, m={int(rows)};"
            " try { app.renderer.render(app.stage); } catch(e) {}"
            " const c=document.querySelector('canvas'); if(!c) return '';"
            " const o=document.createElement('canvas'); o.width=n; o.height=m;"
            " const g=o.getContext('2d');"
            " g.clearRect(0,0,n,m); g.drawImage(c,0,0,n,m);"
            " const d=g.getImageData(0,0,n,m).data; const out=new Array(n*m);"
            " for(let i=0;i<n*m;i++) out[i]=d[i*4+3];"
            " return out.join(',');"
            "})()")
        self.run_js(js, callback or (lambda _r: None))

    # ---------------------------------------------------------------- 收口
    def shutdown(self) -> None:
        self._poll.stop()
        if self._idle_timer is not None:
            self._idle_timer.stop()
        try:
            self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.hide()
            self.close()
        except Exception:  # noqa: BLE001
            logger.debug("live2d: 关窗失败", exc_info=True)
        self._server.stop()


# ------------------------------------------------------------------ 协议代理
#: 代理自己的遮罩缓存刷新节流（秒）。P1 是静止待机姿势，1 s 够；P3 上动作/口型
#: 时按动作期间额外刷新即可。
MASK_REFRESH_S = 1.0


class Live2DSprite(PetSprite):
    """喂给 ``_sprite_factory`` 的代理：协议全满足，自己**什么也不画**。

    直接继承 ``PetSprite``（而不是另写一个转发壳）是刻意的：引擎对 sprite 的协议面
    有 88 处调用 + 五个控制器模块的 ``getattr`` 兜底，逐条复制的方案只要漏一条就是
    运行期 AttributeError；继承之后**协议天然零遗漏**，几何语义（``rect`` /
    ``body_rect`` / 贴边钳制 / 碰撞口径 / 探头旋转）与 webm 路径逐字段同源。

    与父类的差异只有三处，都在下面显式覆盖：

    - ``paint()`` 什么都不画（像素在 Live2D 独立窗里，合成窗这层留空）；
    - 播控（``bind_clip`` / ``restart_clip`` / …）是 no-op（不碰 webm 解码）；
    - ``alpha_at()`` 用 Live2D 窗的 alpha 遮罩，不再查帧命中图。
    """

    def __init__(self, library, *, pos=None, facing: str = "left",
                 scale: float = catalog.DEFAULT_SCALE,
                 window: Optional[Live2DWindow] = None) -> None:
        super().__init__(library, pos=pos, facing=facing, scale=scale)
        self._window = window
        self._overlay = None
        self._mask = None            # (cols, rows, bytes)
        self._mask_ts = 0.0
        self._mask_inflight = False
        self._last_geom: Optional[QRect] = None
        self._render_enabled = True
        if self._window is not None:
            self._window.ready_changed.connect(self._on_page_ready)

    # ---------------------------------------------------------------- 绘制/命中
    def paint(self, painter) -> None:
        """**什么都不画**。像素在 Live2D 独立窗里，合成窗这层只留空窗。"""
        return None

    def alpha_at(self, local) -> int:
        """命中细判（``overlay_window.py:825`` 唯一调用点）。

        补偿：Live2D 独立窗是另一块绘制面，合成窗拿不到它的像素，故用**低分辨率
        alpha 遮罩**（32×48，每秒刷一次，见 ``_refresh_mask``）代替逐像素帧命中图。
        遮罩没到位时按 ``body_rect`` 兜底（最坏是透明角落也能点中，可接受）。
        返回 0~255，口径与 ``PetSprite.alpha_at`` 一致。
        """
        mask = self._mask
        if mask is None:
            self._maybe_refresh_mask()
            r = self.rect()
            body = self.body_rect().translated(-r.x(), -r.y())
            return 255 if body.contains(QPoint(int(local.x()), int(local.y()))) else 0
        cols, rows, data = mask
        w = max(1, self.rect().width())
        h = max(1, self.rect().height())
        cx = min(cols - 1, max(0, int(local.x() * cols / w)))
        cy = min(rows - 1, max(0, int(local.y() * rows / h)))
        self._maybe_refresh_mask()
        return data[cy * cols + cx]

    # ---------------------------------------------------------------- 播控（no-op）
    def bind_clip(self, name: str) -> bool:
        """Live2D 不播 webm：吃掉调用、返回"成功"，让行为机照常推进。

        **换绑不是「有活干」，一律不叫醒页面**（2026-11-08 实机回归）：行为机的
        待机轮转每 6.0–6.25 s 就绑一次 clip（本机取证
        ``.tmp/t309/touch-log-evidence-1948.txt``：连续 40 次 ``touch`` 全部来自
        ``_enter_idle → _bind_with_gen → bind_clip``），旧实现无条件 ``touch()``
        ⇒ 页面 ``__lastAct`` 每 6 s 被重置，而冻帧阈值正是 6000 ms ⇒ 真桌宠
        静置 120 s 从未冻住（实测 1.93 核）。Live2D 页面不播 webm，换绑零可见
        产出；真正的唤醒通道只有两条：``_run_page``（表情/动作/口型）与
        ``Live2DWindow.set_geometry_global``（窗口位移）。
        """
        return True

    def restart_clip(self) -> bool:
        """续圈同样是 webm-only 动作：Live2D 上零可见产出，不许唤醒页面。"""
        return True

    def touch(self) -> None:
        """叫醒 Live2D 窗（真正的可见活动：表演层 ``_run_page`` / 窗口位移）。"""
        window = self.__dict__.get("_window")
        if window is not None:
            window.touch()

    def pause_clip(self) -> None:
        return None

    def resume_clip(self) -> None:
        return None

    def set_flight_anim_speed(self, factor: float) -> None:
        return None

    # ---------------------------------------------------------------- 表演层（T-306）
    # 「让 AI 真的操作 Live2D」的三个落地口。名字白名单**不在这里**：单一真源是
    # ``pet/performance.py`` 的 EXPRESSIONS / MOTIONS，本文件只负责把已经校验过的
    # 真名送到运行页。窗口没 ready / 已销毁一律返回 False（表演层据此记 skipped），
    # **不抛异常** —— 说话链路上抛一次就把整句台词丢了。
    def _run_page(self, script: str, callback=None) -> bool:
        window = self.__dict__.get("_window")
        if window is None or not window.is_ready:
            return False
        self.touch()
        window.run_js(script, callback)
        return True

    def set_expression(self, name: str, callback=None) -> bool:
        """切表情（``name`` 必须是 ``performance.EXPRESSIONS`` 里的真名）。"""
        return self._run_page(
            "window.setExpression ? window.setExpression(%s) : 'NO_HOOK'"
            % json.dumps(str(name), ensure_ascii=False), callback)

    def play_gesture(self, name: str, callback=None) -> bool:
        """播动作（``name`` 必须是 ``performance`` 里的动作真名）。

        走运行页的 ``playMotionByName``：模型的 ``Motions`` 条目**没有 Name 字段**
        （只有 ``File``），所以名字只能在页面里按文件名反查，Qt 侧拿不到索引。
        """
        return self._run_page(
            "window.playMotionByName ? window.playMotionByName(%s) : 'NO_HOOK'"
            % json.dumps(str(name), ensure_ascii=False), callback)

    def set_mouth(self, value: float) -> bool:
        """口型张合（0..1，页面侧再钳一次）。"""
        try:
            numeric = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return False
        return self._run_page("window.setMouth ? window.setMouth(%r) : 'NO_HOOK'" % numeric)

    def mouth_state(self, callback=None) -> bool:
        """读回口型统计（当前值 / 峰值 / 谷值 / 调用次数），供探针取证。"""
        return self._run_page("window.getMouth ? window.getMouth() : 'NO_HOOK'", callback)

    # ---------------------------------------------------------------- 慢通道动作（T-20261108-309）
    # 慢通道点名的是 **webm 素材名**（「舞狮头」「写代码」…），模型里没有这些动作。
    # 映射（素材名/关键词 → 情绪 → (表情, 动作)）的单一真源在
    # ``pet/performance.py:resolve_scene_action``，本方法只负责把结果送上运行页。
    def play_scene_action(self, name: str, callback=None) -> bool:
        """慢通道点名动作 → Live2D 表演。返回 False = 不认识或窗口没 ready。

        **返回值只表示"投递了"**（与 ``play_gesture`` 同口径）：JS 是异步的，
        「真的播了」要用 ``scene_action_evidence`` 读页面账本取证。
        ``None, None`` 的计划（如 neutral）不投递 —— 没动作可演时不算成功。
        """
        try:
            from . import performance
            plan = performance.resolve_scene_action(name)
        except Exception:  # noqa: BLE001 - 映射层坏掉不许把宠拖没
            logger.debug("live2d: 慢通道动作解析失败 %r", name, exc_info=True)
            return False
        if not plan or (plan[0] is None and plan[1] is None):
            return False
        expression, motion = plan
        ok = False
        if expression is not None:
            ok = self.set_expression(expression) or ok
        if motion is not None:
            ok = self.play_gesture(motion, callback) or ok
        if ok:
            logger.info("live2d: 慢通道动作 %r -> 表情=%r 动作=%r",
                        str(name or ""), expression, motion)
        return bool(ok)

    def scene_action_evidence(self, callback=None) -> bool:
        """读回页面账本（当前表情 / 最后动作），供探针**取证真播**。

        ``pocState()`` 返回的是 JSON 字符串（页面 ``web/index.html`` 定义），
        解析由调用方做 —— 这里只送一次 JS，不替回执下结论。
        """
        script = (
            "(function(){var s={};try{s=JSON.parse(window.pocState ? window.pocState() : '{}')||{};}catch(e){}"
            "return JSON.stringify({expression: s.currentExpression||null, motion: s.lastMotion||null,"
            " ready: !!s.ready});})()"
        )
        return self._run_page(script, callback)

    # ---------------------------------------------------------------- 跟随
    def bind_overlay(self, overlay) -> None:
        """绑定合成窗（壳层在 ``add_sprite`` 后调一次）：全局原点从它取。"""
        self._overlay = overlay
        self._sync_window()

    def advance(self, dt: float):
        """每 tick 一次（``overlay_window.py:716``）：位移积分仍在父类，这里补窗口跟随。"""
        changed = super().advance(dt)
        self._sync_window()
        return changed

    def set_pos(self, pos) -> None:
        super().set_pos(pos)
        self._sync_window()

    @property
    def visible(self) -> bool:
        return PetSprite.visible.fget(self)

    @visible.setter
    def visible(self, value) -> None:
        PetSprite.visible.fset(self, value)
        self._apply_visibility()

    def _global_rect(self) -> Optional[QRect]:
        # 构造期父类 __init__ 会调 set_pos（→ _sync_window），那时本类的字段还没
        # 落地，故一律走 __dict__ 取值，不用属性访问（AttributeError 会打断构造）。
        if self.__dict__.get("_overlay") is None:
            return None
        try:
            origin = self._overlay.geometry().topLeft()
        except RuntimeError:  # 宿主窗已销毁
            return None
        r = self.rect()
        return QRect(origin.x() + r.x(), origin.y() + r.y(), r.width(), r.height())

    def _sync_window(self) -> None:
        window = self.__dict__.get("_window")
        if window is None:
            return
        rect = self._global_rect()
        if rect is None:
            return
        if rect == self.__dict__.get("_last_geom"):
            self._maybe_refresh_mask()
            return
        self._last_geom = rect
        window.set_geometry_global(rect)
        self._maybe_refresh_mask()

    # ---------------------------------------------------------------- 遮罩
    def _on_page_ready(self, _ok: bool) -> None:
        self._apply_visibility()
        self._refresh_mask()

    def _maybe_refresh_mask(self) -> None:
        window = self.__dict__.get("_window")
        if window is None or not window.is_ready:
            return
        if self._mask_inflight or time.monotonic() - self._mask_ts < MASK_REFRESH_S:
            return
        self._refresh_mask()

    def _refresh_mask(self) -> None:
        window = self.__dict__.get("_window")
        if window is None:
            return
        self._mask_inflight = True
        self._mask_ts = time.monotonic()

        def _store(payload) -> None:
            self._mask_inflight = False
            try:
                vals = [int(v) for v in str(payload).split(",") if v != ""]
            except ValueError:
                return
            if len(vals) != 32 * 48:
                return
            if not any(vals):
                # 全 0 = 读回失败（不是"角色不在窗里"）：不拿它当遮罩，继续走
                # body_rect 兜底，免得一次读空就把点击判定变成"处处点不中"。
                return
            self._mask = (32, 48, bytes(vals))

        window.alpha_grid(32, 48, _store)

    # ---------------------------------------------------------------- 显隐/停表
    def set_render_enabled(self, enabled: bool) -> None:
        """壳层显隐入口：不可见 = 窗口藏 + ticker 停（D8 必做项）。"""
        enabled = bool(enabled)
        if enabled == self._render_enabled:
            return
        self._render_enabled = enabled
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        window = self.__dict__.get("_window")
        if window is None:
            return
        want = bool(self.visible) and bool(self.__dict__.get("_render_enabled", True))
        if want:
            window.show()
            window.set_paused(False)
        else:
            window.hide()
            window.set_paused(True)

    def close(self) -> None:
        window = self.__dict__.get("_window")
        if window is not None:
            window.shutdown()
            self._window = None
        try:
            super().close()
        except Exception:  # noqa: BLE001
            logger.debug("live2d: 父类收口失败", exc_info=True)


# ------------------------------------------------------------------ 进程内收口
class Live2DRenderer:
    """进程内单例：一个 http 服务 + N 张 Live2D 窗。

    P1 只要求"能看见人"，故不做画质调优；N 张窗共用一份静态服务，端口只开一次。
    """

    _inst = None

    @classmethod
    def instance(cls) -> "Live2DRenderer":
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self) -> None:
        # 静态服务由窗口自带（每窗一份，端口各自分配）：这里不另起一份，
        # 免得同一份文件目录开两个 http 服务白占端口与线程。
        self._windows: list[Live2DWindow] = []
        #: 累计开窗次数（含已关的），给探针/日志对账用。
        self.opened_total = 0
        self.refused_total = 0

    def acquire(self, *, scale: float = catalog.DEFAULT_SCALE,
                width: Optional[int] = None,
                height: Optional[int] = None) -> Optional[Live2DWindow]:
        """给一只宠开一张窗（失败/满额返回 None：调用方应退回 webm 路径）。

        ``scale`` 是**必给的语义参数**：窗口尺寸 = ``window_size(scale)``，与宠物的
        几何同一把尺子。此前这里不传参、写死 520×760，集成后窗口与宠物 scale 完全
        脱钩（T-305 勘 1 的硬缺陷）。
        """
        if QApplication.instance() is None:
            logger.warning("live2d: 无 QApplication，放弃开窗")
            return None
        live = [w for w in self._windows if w is not None]
        if len(live) >= MAX_WINDOWS:
            self.refused_total += 1
            logger.info("live2d: 窗口已满（%d/%d），这只退回 webm", len(live), MAX_WINDOWS)
            return None
        try:
            win = Live2DWindow(scale=scale, width=width, height=height)
            win.load_page()
        except Exception:  # noqa: BLE001 - QtWebEngine 起不来时必须能退回 webm
            logger.exception("live2d: 建窗失败，退回 webm 角色层")
            return None
        self._windows.append(win)
        self.opened_total += 1
        logger.info("live2d: 开窗 %sx%s（scale=%.2f，第 %d 张）",
                    win.window_size_px[0], win.window_size_px[1], scale, len(self._windows))
        return win

    def shutdown(self) -> None:
        for win in list(self._windows):
            try:
                win.shutdown()
            except Exception:  # noqa: BLE001
                logger.debug("live2d: 收口窗口失败", exc_info=True)
        self._windows.clear()
