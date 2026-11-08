# -*- coding: utf-8 -*-
"""T-20261108-309：慢通道动作 → Live2D 表演的落地判据。

三块被测面：

1. **纯函数映射**（``performance.resolve_scene_action``）：素材名/关键词 → 情绪 →
   (表情, 动作)。零 Qt 依赖，本文件的绝大多数用例都在这一层。
2. **sprite 投递**（``Live2DSprite.play_scene_action``）：用假窗口断言「送了什么 JS」，
   不启 QtWebEngine（真相位取证在探针里做）；**冻帧唤醒边界**：待机 clip 轮转不许
   敲醒冻帧（实机回归，见 ``bind_clip`` 的 docstring 与 T-305 取证）。
3. **壳层分流**（``OverlayShell.request_link_anim``）：未绑定方法 + 替身属性，断言
   Live2D 目标不进 webm 的 ``switch_clip``、webm 目标仍走老路。
4. **overlay 返回值链路**（T-20261108-309）：``SceneBubbleMixin`` 在生产里拿到的
   ``self.win`` 是 ``MultiWindowProxy``，其 ``True/False/None`` 三态决定日志怎么写；
   这里用真 proxy + 壳替身把三态与聚合规则钉住（真 Live2D 的页面账本取证在探针里）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet import performance as P  # noqa: E402


# ---------------------------------------------------------------- 1 · 纯函数映射
def test_scene_mood_exact_material_name() -> None:
    """素材名走 emotionActions 快照：舞狮头属 excited。"""
    assert P.scene_mood("舞狮头") == "excited"


def test_scene_mood_keyword_fallback() -> None:
    """快照没覆盖的素材走品类词兜底。"""
    assert P.scene_mood("放风筝") == "happy"      # 「放」
    assert P.scene_mood("左转奔跑") == "calm"     # 「奔跑」
    assert P.scene_mood("原地小憩沉眠") == "tired"  # 快照精确命中


def test_scene_mood_unknown_returns_none() -> None:
    assert P.scene_mood("完全不存在的动作名") is None
    assert P.scene_mood("") is None


def test_resolve_scene_action_material_name() -> None:
    """素材名 → (表情, 动作) 真名。"""
    assert P.resolve_scene_action("舞狮头") == ("爱心眼", "举手切默认手")
    assert P.resolve_scene_action("玩游戏气急败坏") == ("屑屑眼", "竖手指切出来动画")


def test_resolve_scene_action_live2d_motion_name_wins() -> None:
    """慢通道**直接点 Live2D 动作名/别名**时优先精确命中，且不换表情。"""
    assert P.resolve_scene_action("竖手指") == (None, "竖手指切出来动画")
    assert P.resolve_scene_action("竖手指切出来动画") == (None, "竖手指切出来动画")
    assert P.resolve_scene_action("举手") == (None, "举手切默认手")


def test_resolve_scene_action_unknown_returns_none() -> None:
    """不认识就不猜 —— 调用方据此记 no-op（不静默伪装成功）。"""
    assert P.resolve_scene_action("瞎编的动作") is None
    assert P.resolve_scene_action("") is None


def test_mood_table_names_are_real_model_names() -> None:
    """情绪表里的每个名字都必须真实存在（单一真源的防错配断言）。"""
    for mood, (expression, motion) in P.MOOD_PERFORMANCE.items():
        assert mood in P.SCENE_MOODS
        if expression is not None:
            assert expression in P.EXPRESSION_NAMES, (mood, expression)
        if motion is not None:
            assert motion in P.MOTION_NAMES, (mood, motion)


def test_scene_mood_groups_only_use_known_sentiments() -> None:
    for mood, _names in P._SCENE_MOOD_GROUPS + P._EXTRA_MOOD_GROUPS:
        assert mood in P.MOOD_PERFORMANCE, mood


ACTION_POOL = Path(r"D:\AI\slowdesk\persona\action_pool.json")


@pytest.mark.skipif(not ACTION_POOL.exists(), reason="慢通道动作池（本机 slowdesk）不存在")
def test_every_slow_channel_action_resolves() -> None:
    """慢通道 100 个动作名**一个都不许兜底成 None** —— 否则 Live2D 上会静默不动。"""
    names = json.loads(ACTION_POOL.read_text(encoding="utf-8"))["all"]
    missing = [name for name in names if P.resolve_scene_action(name) is None]
    assert not missing, f"未映射的慢通道动作：{missing}"


def test_every_slow_channel_action_has_visible_layer() -> None:
    """每个动作至少有一层可见表演（表情或动作），``(None, None)`` 等于没效果。"""
    names = json.loads(ACTION_POOL.read_text(encoding="utf-8"))["all"]
    blank = [
        name for name in names
        if (plan := P.resolve_scene_action(name)) is not None
        and plan == (None, None)
    ]
    assert not blank, f"无可见层的动作：{blank}"


# ---------------------------------------------------------------- 2 · sprite 投递
class _FakeSignal:
    def connect(self, slot):
        self.slot = slot


class _FakeWindow:
    """假 Live2D 窗口：只记 JS，不碰 QtWebEngine。"""

    def __init__(self, ready: bool = True) -> None:
        self.is_ready = ready
        self.scripts: list[str] = []
        self.touches = 0
        self.ready_changed = _FakeSignal()

    def run_js(self, script, callback=None):
        self.scripts.append(script)
        if callback is not None:
            callback("{}")

    def touch(self) -> None:
        self.touches += 1


class _FakeLibrary:
    def names(self):
        return []


def _make_sprite(window: _FakeWindow, library=None):
    from pet.live2d_sprite import Live2DSprite

    return Live2DSprite(library if library is not None else _FakeLibrary(), window=window)  # type: ignore[arg-type]


def test_live2d_sprite_sends_expression_and_motion() -> None:
    window = _FakeWindow()
    sprite = _make_sprite(window)
    assert sprite.play_scene_action("舞狮头") is True
    joined = "\n".join(window.scripts)
    assert "setExpression" in joined and "爱心眼" in joined
    assert "playMotionByName" in joined and "举手切默认手" in joined


def test_live2d_sprite_unknown_action_sends_nothing() -> None:
    window = _FakeWindow()
    sprite = _make_sprite(window)
    assert sprite.play_scene_action("瞎编的动作") is False
    assert window.scripts == []


def test_live2d_sprite_not_ready_returns_false() -> None:
    window = _FakeWindow(ready=False)
    sprite = _make_sprite(window)
    assert sprite.play_scene_action("舞狮头") is False
    assert window.scripts == []


def test_live2d_sprite_expression_only_plan() -> None:
    """neutral 这类没有动作的情绪：只切表情，不许报「播了动作」。"""
    window = _FakeWindow()
    sprite = _make_sprite(window)
    assert sprite.play_scene_action("左转奔跑") is True   # calm → (默认体型, None)
    joined = "\n".join(window.scripts)
    assert "setExpression" in joined
    assert "playMotionByName" not in joined


# ------------------------------------------------- 2.1 · 冻帧唤醒边界（T-305 实机回归）
class _CategorizedLibrary:
    """带分类的假素材库：走属性分支（不触 catalog 扫描），分类即本用例的真源。"""

    idles = ["待机呼吸1", "待机呼吸2"]
    turns = ["转向东张西望"]
    moves = ["左转奔跑"]
    clicks = ["点击回应"]
    acts = ["挥手"]
    drag = None
    names = ["待机呼吸1", "待机呼吸2", "转向东张西望", "左转奔跑", "点击回应", "挥手"]
    character_id = ""


def test_live2d_idle_clip_roll_does_not_wake_frozen_page() -> None:
    """待机 clip 轮转不是 Live2D 的可见活动：不许重置页面冻帧计时。

    实机取证（``.tmp/t309/touch-log-evidence-1948.txt``）：行为机待机轮转每
    6.0–6.25 s 过一次 ``bind_clip``，旧实现无条件 ``touch()`` ⇒ 页面 ``__lastAct``
    每 6 s 被重置，而冻帧阈值正是 6000 ms、Qt 心跳只有 2 s 一次 ⇒ 真桌宠静置
    120 s 从未冻住（实测 1.93 核）。Live2D 页面不播 webm，待机换绑零可见产出。
    """
    window = _FakeWindow()
    sprite = _make_sprite(window, _CategorizedLibrary())
    assert sprite.bind_clip("待机呼吸1") is True
    assert sprite.bind_clip("待机呼吸2") is True
    assert window.touches == 0, "待机 clip 轮转不许敲醒冻帧"


def test_live2d_real_wake_channels_still_wake_frozen_page() -> None:
    """反向锁定：换绑不唤醒 ≠ 把唤醒通道一起删掉。

    真实唤醒只有两条（T-305 判据「有动作 / 说话 / 交互时立即恢复」的落地口）：
    ① ``_run_page``（表情/动作/口型，见下一条表演层用例）；
    ② 窗口位移走 ``Live2DWindow.set_geometry_global`` → ``Live2DWindow.touch``。
    这里钉住 sprite 侧的显式通道：``Live2DSprite.touch()`` 必须继续转发给窗。
    """
    window = _FakeWindow()
    sprite = _make_sprite(window)
    sprite.touch()
    assert window.touches == 1, "显式唤醒通道（表演层/位移）不许被一起删掉"


def test_live2d_clip_rebind_does_not_wake_the_page() -> None:
    """行为机每 ~6 s 轮换待机素材：换绑/续圈在 Live2D 上是 no-op，不许叫醒页面。

    2026-11-08 实测：真桌宠待机 120 s 烧 1.93 核（渲染器 1.34）。根因是行为机
    每 6.0–6.25 s 换一次 idle clip → ``bind_clip`` 里的 ``touch`` 把页面待机计时
    按 6 s 重置，而冻结判定 = 6 s 阈值 + 2 s 心跳（实测 7.35 s 才判）⇒ **永远判不到
    静止**。冻帧是 T-305 的既有杠杆，webm 播控不得再冒充「有活干」（触摸栈实测：
    ``_tick_sprite → _enter_idle → _bind_with_gen → bind_clip → touch``）。
    """
    window = _FakeWindow()
    sprite = _make_sprite(window)
    for name in ("idle", "待机呼吸1", "点击回应", "挥手"):
        assert sprite.bind_clip(name) is True
    assert sprite.restart_clip() is True
    assert window.touches == 0, "webm 播控 no-op 不许唤醒 Live2D 页面（会压掉待机冻帧）"


def test_live2d_performance_still_wakes_the_page() -> None:
    """真正的可见表演（表情/动作/口型/位移）必须仍能叫醒冻住的页面。"""
    window = _FakeWindow()
    sprite = _make_sprite(window)
    assert sprite.play_scene_action("舞狮头") is True
    assert window.touches >= 1, "表演层是叫醒通道，删掉换绑唤醒不能把它一起删了"


# ---------------------------------------------------------------- 3 · 壳层分流
def _fake_shell(target=None, busy: bool = False):
    """``OverlayShell`` 未绑定方法 + 替身属性（不构造 QObject）。"""
    from pet.overlay_shell import OverlayShell

    shell = OverlayShell.__new__(OverlayShell)
    shell.__dict__["_pending_link_anim"] = None
    shell.__dict__["_link_anim_current"] = None
    calls: list[str] = []
    shell.__dict__["mark_activity"] = lambda: calls.append("activity")
    shell.__dict__["alert_target_sprite"] = lambda: target
    shell.__dict__["_link_anim_busy"] = lambda: busy
    shell.__dict__["_play_pending_link_anim"] = lambda: calls.append("webm")
    return shell, calls


class _Live2DTarget:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.names: list[str] = []

    def play_scene_action(self, name: str) -> bool:
        self.names.append(name)
        return self.ok


def test_shell_routes_live2d_target_to_performer() -> None:
    target = _Live2DTarget(ok=True)
    shell, calls = _fake_shell(target)
    assert shell.request_link_anim("舞狮头") is True
    assert target.names == ["舞狮头"]
    assert "webm" not in calls


def test_shell_live2d_miss_is_explicit_noop() -> None:
    """映射不出来 ⇒ 返回 False（既不播也不假装投递），且**不进 webm 链**。"""
    target = _Live2DTarget(ok=False)
    shell, calls = _fake_shell(target)
    assert shell.request_link_anim("瞎编的动作") is False
    assert "webm" not in calls


def test_shell_webm_target_keeps_old_path() -> None:
    """没有 play_scene_action 的 sprite（webm 路径）仍走原链路，返回 None。"""
    shell, calls = _fake_shell(object())
    assert shell.request_link_anim("待机呼吸休闲") is None
    assert "webm" in calls
    assert shell._pending_link_anim == "待机呼吸休闲"


# ---------------------------------------------------------------- 4 · 场景桥日志
def test_scene_bridge_logs_live2d_sent(caplog) -> None:
    """``_on_scene_action`` 在 Live2D 投递成功时记 via=live2d-sent、played=False。"""
    from pet.dsh_scene_bridge import SceneBubbleMixin

    class _Win:
        anim = "?"

        def request_link_anim(self, name):
            return True

    class _Bridge(SceneBubbleMixin):
        pass

    bridge = _Bridge.__new__(_Bridge)
    bridge.__dict__["win"] = _Win()

    with caplog.at_level(logging.INFO, logger="pet.dsh_scene_bridge"):
        bridge._on_scene_action("dsh", "舞狮头")
    line = [r.message for r in caplog.records if "[SCENE]" in r.message][-1]
    assert "via=live2d-sent" in line
    assert "played=False" in line
    assert "sent=True" in line


def test_scene_bridge_logs_live2d_miss_as_not_sent(caplog) -> None:
    """Live2D 下映射不出来 ⇒ sent=False（旧代码会记 sent=True，屏幕其实没动）。"""
    from pet.dsh_scene_bridge import SceneBubbleMixin

    class _Win:
        anim = "?"

        def request_link_anim(self, name):
            return False

    class _Bridge(SceneBubbleMixin):
        pass

    bridge = _Bridge.__new__(_Bridge)
    bridge.__dict__["win"] = _Win()

    with caplog.at_level(logging.INFO, logger="pet.dsh_scene_bridge"):
        bridge._on_scene_action("dsh", "瞎编的动作")
    line = [r.message for r in caplog.records if "[SCENE]" in r.message][-1]
    assert "via=live2d-miss" in line
    assert "sent=False" in line


# ---------------------------------------------------------------- 5 · overlay 返回值链路
class _OverlayChainShell:
    """overlay 扇出目标的最小面（生产是 ``OverlayShell``）：可见性 + 返回值契约。"""

    def __init__(self, result=True, visible: bool = True) -> None:
        self.result = result
        self.visible = visible
        self.names: list[str] = []

    def isVisible(self) -> bool:  # noqa: N802 - 与 OverlayShell 同命名
        return self.visible

    def request_link_anim(self, name):
        self.names.append(name)
        return self.result


def _proxy_for(shell):
    """生产同构：``MultiWindowProxy`` 的宿主是 AppShell（只有 config/instances/壳）。"""
    from types import SimpleNamespace

    from pet.multi_window_shared import MultiWindowProxy

    host = SimpleNamespace(config=SimpleNamespace(), instances=[], _overlay_shell=shell)
    return host, MultiWindowProxy(host)


def test_overlay_win_is_production_proxy_and_targets_shell() -> None:
    """overlay 下 ``self.win`` 必须是 ``MultiWindowProxy``，扇出目标是壳（不是 None/PetWindow）。"""
    from pet.multi_window_shared import presentation_targets

    shell = _OverlayChainShell(result=True)
    host, win = _proxy_for(shell)
    assert type(win).__name__ == "MultiWindowProxy"
    assert presentation_targets(host) == [shell]
    # 代理没有池内版本 ⇒ 桥的 ``play_scene_action`` 分支必然落到 ``request_link_anim``
    assert not hasattr(win, "play_scene_action")
    assert win.request_link_anim("舞狮头") is True
    assert shell.names == ["舞狮头"]


def test_proxy_request_link_anim_aggregates_three_states() -> None:
    """True（Live2D 已投递）/ False（明确 no-op）/ None（webm 投递口径）三态如实聚合。"""
    for result, expected in ((True, True), (False, False), (None, None)):
        _host, win = _proxy_for(_OverlayChainShell(result=result))
        assert win.request_link_anim("舞狮头") is expected
    # 无可见窗 = 没有投递目标：不许谎报 True（沿用上游"无返回值"口径）
    hidden = _OverlayChainShell(result=True, visible=False)
    _host, win = _proxy_for(hidden)
    assert win.request_link_anim("舞狮头") is None
    assert hidden.names == []


def test_scene_bridge_through_proxy_logs_three_states(caplog) -> None:
    """桥经生产代理看到的返回值 → 日志三分支（三态一个都不许混写）。"""
    from pet.dsh_scene_bridge import SceneBubbleMixin

    def run(result) -> str:
        _host, win = _proxy_for(_OverlayChainShell(result=result))
        bridge = SceneBubbleMixin()
        bridge.__dict__["win"] = win
        with caplog.at_level(logging.INFO, logger="pet.dsh_scene_bridge"):
            bridge._on_scene_action("dsh", "舞狮头")
        return [r.message for r in caplog.records if "[SCENE]" in r.message][-1]

    true_line = run(True)
    assert "via=live2d-sent" in true_line and "sent=True" in true_line
    assert "played=False" in true_line
    miss_line = run(False)
    assert "via=live2d-miss" in miss_line and "sent=False" in miss_line
    queued_line = run(None)
    assert "via=upstream-queued" in queued_line and "sent=True" in queued_line


# ---------------------------------------------------------------- 6 · 角色层开关语义
def test_live2d_env_switch_semantics() -> None:
    """``PET_LIVE2D`` 的唯一读点：未设/空串=交给配置；``0``=硬关；其它非空=开。"""
    from pet.overlay_shell import live2d_enabled_by_env

    assert live2d_enabled_by_env({}) is None
    assert live2d_enabled_by_env({"PET_LIVE2D": ""}) is None
    assert live2d_enabled_by_env({"PET_LIVE2D": "0"}) is False
    assert live2d_enabled_by_env({"PET_LIVE2D": "1"}) is True


def _patch_fake_live2d(monkeypatch, touched: list) -> None:
    """把 Live2D 层换成记账哨兵：任何「进入 Live2D 分支」都会写 ``touched``。

    ``from . import live2d_sprite`` 优先取包属性（前面的用例已导入真模块把它挂到
    ``pet`` 上），所以 ``sys.modules`` 与包属性两处都要换。
    """
    from types import ModuleType, SimpleNamespace
    import sys

    import pet as pet_pkg

    def _instance():
        touched.append("instance")
        return SimpleNamespace(acquire=lambda scale: None)

    fake = ModuleType("pet.live2d_sprite")
    fake.Live2DRenderer = SimpleNamespace(instance=_instance)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pet.live2d_sprite", fake)
    monkeypatch.setattr(pet_pkg, "live2d_sprite", fake, raising=False)


def _make_default_sprite_with_env(monkeypatch, env: str | None, config: dict):
    """在假 self 上调未绑定 ``_make_default_sprite``（不构造 QObject / 不碰 Qt 窗）。"""
    from pet import overlay_shell as OS

    marker = object()
    monkeypatch.setattr(OS, "PetSprite", lambda *a, **k: marker)
    if env is None:
        monkeypatch.delenv("PET_LIVE2D", raising=False)
    else:
        monkeypatch.setenv("PET_LIVE2D", env)
    shell = OS.OverlayShell.__new__(OS.OverlayShell)
    shell.__dict__["_config"] = config
    return OS.OverlayShell._make_default_sprite(shell, object(), None, 1.0), marker


def test_make_default_sprite_env_zero_never_enters_live2d(monkeypatch) -> None:
    """报告 §七.7 假开关：``PET_LIVE2D=0`` 必须在工厂层短路，一 px 都不碰 Live2D。

    旧实现读 ``os.environ.get("PET_LIVE2D")`` 的真值 ⇒ ``"0"`` 也算开：本用例在旧
    实现下 ``touched == ["instance"]`` 必红；修好后 ``touched == []``。
    """
    touched: list[str] = []
    _patch_fake_live2d(monkeypatch, touched)
    sprite, marker = _make_default_sprite_with_env(monkeypatch, "0", {})
    assert sprite is marker
    assert touched == [], "PET_LIVE2D=0 是关，工厂层不许碰 Live2D"


def test_make_default_sprite_env_unset_follows_config(monkeypatch) -> None:
    """未设环境变量时配置键说了算：``live2d_sprite=False`` 仍能关（旧行为不变）。"""
    touched: list[str] = []
    _patch_fake_live2d(monkeypatch, touched)
    sprite, marker = _make_default_sprite_with_env(
        monkeypatch, None, {"live2d_sprite": False})
    assert sprite is marker
    assert touched == []


if __name__ == "__main__":  # pragma: no cover - 方便手工跑
    sys.exit(pytest.main([__file__, "-q"]))
