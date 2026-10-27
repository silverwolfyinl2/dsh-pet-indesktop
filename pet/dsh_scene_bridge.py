"""桌宠「场景气泡 / 场景动作」桥 —— 从 agent_link 抽出的独立模块（2026-10-27）。

## 为什么抽出来

这一段（3 个纯函数 + 6 个类方法，约 190 行）原本长在 `pet/agent_link.py` 的
`AgentLinkManager` 里。上游 v4.4.0 把 `agent_link.py` **整个改成了包**
（`pet/agent_link/__init__.py`，+2551/−1251），升级时这 190 行要在大改的文件里
重新贴一遍——那是整个升级冲突里最贵的一块。

抽出来后：
- 上游怎么改 `agent_link` 的内部实现、气泡改道灵动岛逻辑怎么变，都不影响本文件
- 升级时 `agent_link/__init__.py` 只需要保留一行继承（`AgentLinkManager(SceneBubbleMixin, ...)`）

## 不自造语义

方法体是**原样搬运**，一行没改。`self` 语义不变（`SceneBubbleMixin` 被
`AgentLinkManager` 继承，本文件的方法拿到的是同一个实例）。
唯一变化是日志 logger 名（`pet.agent_link` → `pet.dsh_scene_bridge`），
`[BUBBLE]` / `[SCENE]` 日志前缀保持不变——按前缀扫描的脚本不受影响。

## 2026-10-27 现状

这三个函数与六个方法承担：
- 气泡时长按字数缩放（6s + 220ms/字，夹 [6s,20s]，`PET_EXTERNAL_BUBBLE_MS` 可覆盖）
- 外部气泡默认关（`PET_EXTERNAL_BUBBLE=1` 开），运行期可用标记文件临时关
- 放行策略：重要提醒永远优先；被挡时有界排队，绝不静默丢
- 慢通道点名动作（`play_scene_action`），非法名字安全 no-op 并记日志
"""
from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import QTimer

log = logging.getLogger("pet.dsh_scene_bridge")


def external_bubble_duration_ms(text: str) -> int:
    """外部台词气泡的停留时长（2026-10-27 秋殇要求：气泡太短，语音念完就没了）。

    原实现写死 4500ms —— 二十几字的台词，语音还没念完气泡先消失。
    现在按字数缩放：底 6s + 每字 220ms，夹在 [6s, 20s]（长句读得完、短句也不闪没）。
    `PET_EXTERNAL_BUBBLE_MS` 设了就固定用它（便于实测与回归，不用改代码）。
    """
    override = str(os.environ.get("PET_EXTERNAL_BUBBLE_MS", "") or "").strip()
    if override.isdigit() and int(override) > 0:
        return int(override)
    n = len(str(text or ""))
    return int(max(6000, min(20000, 6000 + 220 * n)))


def _external_bubble_enabled() -> bool:
    """★ 2026-10-15（裁决 A）：外部气泡**默认关**，只在我方启动器里显式开（PET_EXTERNAL_BUBBLE=1）。

    关闭时不仅要"不显示"，还要让**已排队的重试自行作废**（避免重开后显示过期文本）。
    """
    import os as _os
    import tempfile as _tempfile
    from pathlib import Path as _Path
    if _os.environ.get("PET_EXTERNAL_BUBBLE", "0").strip().lower() not in ("1", "true", "on", "yes"):
        return False
    # 运行期关闭门（裁决要求"运行中关闭可验证"）：存在该标记文件 ⇒ 立即视为关闭，**无需重启**
    flag = _os.environ.get("PET_EXTERNAL_BUBBLE_FLAG") or str(_Path(_tempfile.gettempdir()) / "dsh-pet-external-bubble.off")
    return not _Path(flag).exists()


def external_bubble_policy(*, visible: bool, alert_active: bool, sticky: bool,
                           busy: bool, retries_left: int, important: bool = False) -> str:
    """★ 2026-10-15：外部（我方）气泡放行策略 —— 纯函数，便于单测与复核。

    返回：
      'island' —— 窗口隐藏：改道灵动岛反馈面（可验；岛不可用时调用方记 drop）
      'defer'  —— 此刻被重要提醒/sticky/占位窗口挡住：**排队等**（有界），**绝不抢占**
      'drop'   —— 等过 retries_left 次仍被挡：放弃并**记原因**（不静默）
      'show'   —— 可以直接显示
    不变量：`alert_active`（审批/问题/失败/卡住等提醒队列）**永远优先**，本函数任何分支都不会覆盖它。
    """
    if not visible:
        return "island"
    if alert_active:
        return "defer" if retries_left > 0 else "drop"
    if sticky and not important:
        return "defer" if retries_left > 0 else "drop"
    if busy:
        return "defer" if retries_left > 0 else "drop"
    return "show"


class SceneBubbleMixin:
    """场景气泡 / 场景动作的宿主方法集（由 `AgentLinkManager` 继承）。

    依赖宿主提供：`win`（PetWindow 或替身）、以及 Qt 对象语义（`QTimer(host)` 需要宿主是 QObject）。
    """

    _EXTERNAL_BUBBLE_TRIES = 4          # 有界排队：最多等 4 次
    _EXTERNAL_BUBBLE_DELAY_MS = 2500    # 每次等待间隔（与她既有重要气泡重试同量级）

    def _on_scene_bubble(self, agent_key: str, text: str) -> None:
        """★ 2026-10-15：显示**我方文本**的气泡 —— 不抢占重要提醒、排队有上限、隐藏走灵动岛。

        每个分支都记 `[BUBBLE]` 日志（shown / island / deferred / dropped+原因）⇒ 可在实机上机器判定。
        """
        if not getattr(self.win, "show_bubble", None):
            log.info("[BUBBLE] dropped reason=no-show_bubble")
            return
        if not _external_bubble_enabled():
            self._scene_bubble_cancel("disabled")      # 真停表（不只是"到点不执行"）
            log.info("[BUBBLE] dropped reason=disabled")
            return
        self._scene_bubble_try(agent_key, text, self._EXTERNAL_BUBBLE_TRIES)

    def _scene_bubble_fire(self) -> None:
        """定时器到点：取出等待项继续（等待项为空说明已被取消 ⇒ 什么都不做）。"""
        pend = getattr(self, "_scene_bubble_pending", None)
        self._scene_bubble_pending = None
        if not pend:
            return
        agent_key, text, retries_left = pend
        if retries_left <= 0:
            log.info("[BUBBLE] dropped reason=queue-exhausted agent=%s", agent_key)
            return
        self._scene_bubble_try(agent_key, text, retries_left)

    def _scene_bubble_cancel(self, reason: str) -> None:
        """**真取消**：停表 + 清等待项（区别于"到点才发现被关"）。"""
        timer = getattr(self, "_scene_bubble_timer", None)
        had = bool(getattr(self, "_scene_bubble_pending", None)) or bool(timer is not None and timer.isActive())
        if timer is not None and timer.isActive():
            timer.stop()
        self._scene_bubble_pending = None
        if had:
            log.info("[BUBBLE] queue-cancelled reason=%s", reason)

    def _scene_bubble_try(self, agent_key: str, text: str, retries_left: int) -> None:
        if not _external_bubble_enabled():
            # 兜底：若仍被调用（例如到点瞬间开关被翻），同样真取消，不留过期文案
            self._scene_bubble_cancel("disabled-at-fire")
            log.info("[BUBBLE] dropped reason=disabled")
            return
        win = getattr(self, "win", None)
        visible = True
        is_visible = getattr(win, "isVisible", None)
        if callable(is_visible):
            try:
                visible = bool(is_visible())
            except Exception:                                                              # noqa: BLE001
                visible = True
        alert_active = bool(getattr(win, "_alert_current", None) or
                            getattr(win, "_alert_queue", None))
        sticky = bool(getattr(win, "_sticky_bubble_active", False))
        busy = time.monotonic() < getattr(win, "_bubble_busy_until", 0.0)
        verdict = external_bubble_policy(visible=visible, alert_active=alert_active,
                                         sticky=sticky, busy=busy, retries_left=retries_left)
        if verdict == "show":
            try:
                # 上游 v4.4.0 起 show_bubble 返回 bool（是否真上屏）；旧版返回 None。
                # 2026-10-27 升级实测：不接返回值时日志写 shown 而屏幕其实没有——
                # 与 E7（僵尸设置进程压气泡）同一类假象，必须按返回值判定。
                ok = win.show_bubble(text, duration_ms=external_bubble_duration_ms(text))
                shown = True if ok is None else bool(ok)      # None = 旧版签名，按旧语义认
                if shown:
                    log.info("[BUBBLE] shown agent=%s len=%d host=%s ret=%r",
                             agent_key, len(text), type(win).__name__, ok)
                else:
                    log.info("[BUBBLE] dropped reason=not-on-screen agent=%s len=%d "
                             "host=%s ret=%r visible=%s alert=%s suppressed=%s sticky=%s",
                             agent_key, len(text), type(win).__name__, ok, visible, alert_active,
                             bool(getattr(win, "_bubble_suppressed", False)), sticky)
            except Exception:                                                              # noqa: BLE001
                log.info("[BUBBLE] dropped reason=show_bubble-error", exc_info=True)
            return
        if verdict == "island":
            ok = False
            try:
                from . import window_alerts as _window_alerts
                ok = bool(_window_alerts.redirect_hidden_bubble(win, text, duration_ms=external_bubble_duration_ms(text)))
            except Exception:                                                              # noqa: BLE001
                ok = False
            log.info("[BUBBLE] island agent=%s ok=%s%s", agent_key, ok,
                     "" if ok else "（岛不可用 ⇒ 丢弃，不静默）")
            return
        if verdict == "defer":
            log.info("[BUBBLE] deferred agent=%s retries_left=%d", agent_key, retries_left)
            # 用**自有** QTimer（不是 singleShot）：这样才能在关闭时**真停表**
            self._scene_bubble_pending = (agent_key, text, retries_left - 1)
            timer = getattr(self, "_scene_bubble_timer", None)
            if timer is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self._scene_bubble_fire)
                self._scene_bubble_timer = timer
            timer.start(self._EXTERNAL_BUBBLE_DELAY_MS)
            return
        log.info("[BUBBLE] dropped reason=%s agent=%s retries_left=0",
                 "alert-active" if alert_active else ("sticky" if sticky else "busy"), agent_key)

    def _on_scene_action(self, agent_key: str, name: str) -> None:
        """★ 2026-10-15：执行**慢通道点名**的动作（监视器线程发信号 → 这里在主线程调窗口）。

        监视器（worker 线程）没有 `self.win` 引用，动作必须由持有窗口的这里执行。

        ★ 2026-10-27 升级后补第二条路：上游 v4.4.0 换成 overlay 架构，
        `self.win` 常是 `MultiWindowProxy` → 落到 overlay 宿主时
        `play_scene_action` 的 `self.idles/acts/...` 池子是空的（实测
        `[SCENE] 已播=False anim=?`），但上游自己提供了 `request_link_anim(name)`
        —— Agent 联动的一次性动作入口，overlay 侧已实现。所以：
        先走我们的池内校验版；它说不播（False）就回落上游接口。
        """
        ok = False
        try:
            fn = getattr(self.win, "play_scene_action", None)
            if callable(fn):
                ok = bool(fn(name))
        except Exception:                                                                  # noqa: BLE001
            log.debug("play_scene_action 失败：%r", name, exc_info=True)
        if not ok:
            try:
                fn2 = getattr(self.win, "request_link_anim", None)
                if callable(fn2):
                    fn2(name)                      # 上游接口无返回值：投递成功即算
                    ok = True
                    log.info("[SCENE] 回落上游 request_link_anim：%r", name)
            except Exception:                                                             # noqa: BLE001
                log.debug("request_link_anim 失败：%r", name, exc_info=True)
                ok = False
        log.info("[SCENE] agent=%s action=%r 已播=%s anim=%s",
                 agent_key, name, ok, getattr(self.win, "anim", "?"))
