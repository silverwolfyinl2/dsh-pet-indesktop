# -*- coding: utf-8 -*-
"""外部气泡放行策略回归测试（GPT 裁决 20261015-330000 的三条约束）。

约束对应：
 ① **重要提醒永不丢弃**：alert_active 时任何分支都不返回 'show'（先 defer、超限才 drop）；
 ② **我方气泡排队有上限**：retries_left 用尽 ⇒ 'drop'（不是无限等）；
 ③ **隐藏时走灵动岛**：不可见 ⇒ 'island'（调用方在岛不可用时记 drop，不静默）。

纯函数，无 Qt 依赖；管理器侧的实际执行由实机 `[BUBBLE]` 日志验证（见 PR 报告）。
"""
from __future__ import annotations

import pytest

from pet.agent_link import external_bubble_policy


class TestPolicy:
    def test_visible_and_free_shows(self):
        assert external_bubble_policy(visible=True, alert_active=False, sticky=False,
                                      busy=False, retries_left=4) == "show"

    def test_hidden_goes_island(self):
        """③ 隐藏 ⇒ 灵动岛（且不受其它门影响）。"""
        assert external_bubble_policy(visible=False, alert_active=True, sticky=True,
                                      busy=True, retries_left=0) == "island"

    @pytest.mark.parametrize("retries_left,expect", [(4, "defer"), (1, "defer"), (0, "drop")])
    def test_alert_never_preempted(self, retries_left, expect):
        """① 提醒队列激活：先排队；只有预算用尽才放弃 ⇒ 永不被我方气泡覆盖。"""
        got = external_bubble_policy(visible=True, alert_active=True, sticky=False,
                                     busy=False, retries_left=retries_left)
        assert got == expect
        assert got != "show"

    @pytest.mark.parametrize("retries_left,expect", [(4, "defer"), (0, "drop")])
    def test_busy_bounded_queue(self, retries_left, expect):
        """② 占位窗口：有界排队，预算用尽即 drop（不无限等）。"""
        assert external_bubble_policy(visible=True, alert_active=False, sticky=False,
                                      busy=True, retries_left=retries_left) == expect

    @pytest.mark.parametrize("retries_left,expect", [(4, "defer"), (0, "drop")])
    def test_sticky_defers_non_important(self, retries_left, expect):
        assert external_bubble_policy(visible=True, alert_active=False, sticky=True,
                                      busy=False, retries_left=retries_left,
                                      important=False) == expect

    def test_sticky_allows_important(self):
        """important=True 时 sticky 不挡（与她既有语义一致）。"""
        assert external_bubble_policy(visible=True, alert_active=False, sticky=True,
                                      busy=False, retries_left=4, important=True) == "show"

    def test_returns_only_known_verdicts(self):
        seen = set()
        for vis in (True, False):
            for alert in (True, False):
                for sticky in (True, False):
                    for busy in (True, False):
                        for n in (0, 1, 4):
                            seen.add(external_bubble_policy(
                                visible=vis, alert_active=alert, sticky=sticky,
                                busy=busy, retries_left=n))
        assert seen <= {"show", "island", "defer", "drop"}
