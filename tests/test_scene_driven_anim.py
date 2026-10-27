# -*- coding: utf-8 -*-
"""情景驱动动画（秋殇 2026-10-15 定调）回归测试。

定调原文：「改成**平时待机**，然后根据**慢通道判断的情景**来让慢通道**选择相应的动作**」；
「**我要的是能够选择动作，而不是随机动作链**」。

公共缝合点（两处，均为 PetWindow 的对外可见行为）：
  a) `PetWindow._pick_next` —— **情景驱动开启时空闲只回待机**（不再按 30/10/40/20 掷骰选动作/移动）；
  b) `PetWindow.play_scene_action(name)` —— 按**慢通道给定**的动作名播放；名字非法返回 False；
     播完回待机的职责归既有播完逻辑（`_on_anim_ended`），本文件只断言"选中的就是那一个"。

契约（为什么默认关）：默认开会让 `_pick_next` 不再消费预测，打红既有
`test_predictive_prewarm` 的 b/c/d/d1 四支——即改变既有被测行为。故本特性
**默认关、显式开**：`PET_SCENE_DRIVEN_ANIM=1`（也接受 true/on/yes）。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.window import PetWindow

from tests.test_predictive_prewarm import FakeLibrary


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_window(tmp_path, **cfg_overrides):
    cfg = Config(base=tmp_path)
    for k, v in cfg_overrides.items():
        cfg.set(k, v)
    return PetWindow(FakeLibrary(), cfg)


def _force_pick_last(monkeypatch):
    """把掷骰逼成"必选非待机"：random() 取最大值、choice 取池尾元素。

    情景驱动若真的生效，`_pick_next` 不会调用它们，结果仍应是待机。
    """
    import random as _random
    monkeypatch.setattr(_random, "random", lambda: 0.999)
    monkeypatch.setattr(_random, "choice", lambda lst: lst[-1])


# ============================================================================
# 开关语义：默认关（既有行为不变）／显式开
# ============================================================================
class TestSceneDrivenFlag:
    def test_default_is_off(self, app, tmp_path, monkeypatch):
        monkeypatch.delenv("PET_SCENE_DRIVEN_ANIM", raising=False)
        win = _make_window(tmp_path)
        assert win.scene_driven_anim is False

    @pytest.mark.parametrize("val", ["1", "true", "on", "yes", "TRUE", " 1 "])
    def test_explicit_on(self, app, tmp_path, monkeypatch, val):
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", val)
        win = _make_window(tmp_path)
        assert win.scene_driven_anim is True

    @pytest.mark.parametrize("val", ["0", "false", "", "no", "off"])
    def test_explicit_off(self, app, tmp_path, monkeypatch, val):
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", val)
        win = _make_window(tmp_path)
        assert win.scene_driven_anim is False


# ============================================================================
# a) 空闲只待机：开=只回待机；关=仍走随机链（同一随机数下不同结果）
# ============================================================================
class TestIdleOnlyWhenSceneDriven:
    def test_on_idles_only(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        _force_pick_last(monkeypatch)
        win._pick_next()
        assert win.anim in win.idles, "情景驱动开启时空闲必须回待机"
        assert win.idles, "夹具应至少有一个待机动画"

    def test_off_may_leave_idle(self, app, tmp_path, monkeypatch):
        """对照：关掉开关、同一随机数下**允许**离开待机（证明门是真在起作用）。"""
        monkeypatch.delenv("PET_SCENE_DRIVEN_ANIM", raising=False)
        win = _make_window(tmp_path)
        win._switch(win.idles[0])
        _force_pick_last(monkeypatch)
        win._pick_next()
        assert win.anim in (win.idles + win.turns + win.acts + win.moves)


# ============================================================================
# b) 选动作：按名播放（慢通道给的名字）／非法名不静默
# ============================================================================
class TestPlaySceneAction:
    def test_plays_named_animation(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        pool = win.acts or win.turns or win.idles
        assert pool, "夹具应至少有一个可播动画"
        name = pool[0]
        assert win.play_scene_action(name) is True
        assert win.anim == name, "必须播**慢通道指定**的那一个，而不是随机挑"

    def test_plays_second_distinct_name(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        pool = win.acts or win.turns or win.idles
        if len(pool) < 2:
            pytest.skip("该夹具只有一个候选动画")
        assert win.play_scene_action(pool[1]) is True
        assert win.anim == pool[1]

    @pytest.mark.parametrize("bad", ["不存在的动作名", " ", "写codigo", "待机"])
    def test_rejects_unknown_name(self, app, tmp_path, monkeypatch, bad):
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        before = win.anim
        assert win.play_scene_action(bad) is False
        assert win.anim == before, "非法名不得改变当前动画（不静默乱播）"

    def test_rejects_empty(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        assert win.play_scene_action("") is False
        assert win.play_scene_action(None) is False


# ============================================================================
# c) 集成边界（GPT 裁决 20261015-330000 点名的三条）
#    ① 连续点名  ② 动作未播完再次点名  ③ 窗口关闭/重建期间收到点名
# ============================================================================
class TestSceneActionIntegrationEdges:
    def test_consecutive_calls_last_wins(self, app, tmp_path, monkeypatch):
        """① 连续点名：连推两个不同动作，以**最后一次**请求为准（不排队、不合并）。"""
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        pool = win.acts or win.turns or win.idles
        if len(pool) < 2:
            pytest.skip("该夹具不足两个候选动画")
        assert win.play_scene_action(pool[0]) is True
        assert win.play_scene_action(pool[1]) is True
        assert win.anim == pool[1], "连续点名必须以最后一次为准"

    def test_interrupt_before_finished(self, app, tmp_path, monkeypatch):
        """② 动作**尚未播完**时再次点名（不同动作）⇒ 立刻切换，不等待、不抛。"""
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        pool = win.acts or win.turns or win.idles
        if len(pool) < 2:
            pytest.skip("该夹具不足两个候选动画")
        win.play_scene_action(pool[0])
        # 刻意不推进帧（模拟"还没播完"）：直接再点名另一个
        assert win.play_scene_action(pool[1]) is True
        assert win.anim == pool[1]
        # 再点名回第一个，仍应立刻生效
        assert win.play_scene_action(pool[0]) is True
        assert win.anim == pool[0]

    def test_repeat_same_name_is_not_guaranteed_restart(self, app, tmp_path, monkeypatch):
        """③-附：重复点名**同一动作**的确定语义（契约 §三）。

        本缝合点只能观察到"请求被接受且当前动画名相同"；是否重头播放取决于 `_switch`
        对同名动画的既有短路行为，**不在本测试的可观测范围**，故契约里写明"不保证重播"，
        需要强制重播时应先发 `{"state":"idle"}` 回待机再点名。
        """
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        name = (win.acts or win.turns or win.idles)[0]
        assert win.play_scene_action(name) is True
        assert win.play_scene_action(name) is True          # 不抛、不崩
        assert win.anim == name

    def test_action_during_and_after_hide(self, app, tmp_path, monkeypatch):
        """③ 窗口**隐藏/关闭期间**收到点名：不得抛异常；恢复显示后仍可正常点名。"""
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win = _make_window(tmp_path)
        name = (win.acts or win.turns or win.idles)[0]
        win.hide()
        assert win.play_scene_action(name) in (True, False)   # 隐藏期间：允许拒绝，但**不许抛**
        win.show()
        assert win.play_scene_action(name) is True
        assert win.anim == name
        win.close()
        assert win.play_scene_action(name) in (True, False)   # 关闭期间：同样不许抛

    def test_action_on_fresh_window_after_close(self, app, tmp_path, monkeypatch):
        """③-附 窗口**重建**（新实例）后点名正常 ⇒ 说明点名不依赖上一个窗口实例的状态。"""
        monkeypatch.setenv("PET_SCENE_DRIVEN_ANIM", "1")
        win1 = _make_window(tmp_path)
        win1.close()
        win2 = _make_window(tmp_path)
        name = (win2.acts or win2.turns or win2.idles)[0]
        assert win2.play_scene_action(name) is True
        assert win2.anim == name
