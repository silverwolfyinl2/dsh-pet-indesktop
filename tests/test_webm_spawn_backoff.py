# -*- coding: utf-8 -*-
"""webm 解码失败**日志节流**（2026-10-02 本机加固）的回归测试。

背景（本机实测 pet-6744.log，2026-10-02 20:57:29→20:57:39）：会话结束/关机窗口里
消费端以 ~24Hz 反复起 reader，每次 spawn 都失败，10 秒刷 **230 条 ERROR + traceback**；
素材本身没问题（自带 ffmpeg 能读：vp9+alpha、10.04s）。修法 = 连续失败计数 + 日志节流
（第 1 次与每 5 次出 ERROR，其余 DEBUG）+ 会话结束窗口内降级 INFO。

这里钉住：
  1. 计数与退避时长的语义（单调递增、封顶、成功清零）；
  2. 连续失败时 ERROR 条数被节流（不是一次一条）；
  3. **会话结束闸门期间不再出 ERROR**（全部降级 INFO）—— 这就是那 230 条的直接防回归；
  4. 闸门期间 reader 早退、不 spawn。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet import webm_clip as W


@pytest.fixture(autouse=True)
def _clean_state():
    W._reset_spawn_backoff_for_tests()
    W._reset_session_ending_for_tests()
    yield
    W._reset_spawn_backoff_for_tests()
    W._reset_session_ending_for_tests()


def _fail_reader(clip, generation: int) -> None:
    clip._generation = generation
    clip._reader(threading.Event(), generation=generation)


def test_fail_counter_and_delay_semantics():
    p = "dummy-backoff.webm"
    assert W.spawn_backoff_note(p, False) == 1
    assert W.spawn_backoff_note(p, False) == 2
    assert W.spawn_backoff_remaining(p) > 0
    assert W.spawn_backoff_note(p, True) == 0, "成功一次必须清零"
    assert W.spawn_backoff_remaining(p) == 0
    delays = [W.spawn_backoff_delay(n) for n in range(1, 8)]
    assert delays == sorted(delays) and delays[-1] == W._SPAWN_BACKOFF_MAX_S


def test_error_logs_are_throttled(monkeypatch, caplog):
    """连续 9 次失败：ERROR 只该出现在第 1、5 次（共 2 条），不是 9 条。"""
    QApplication.instance() or QApplication([])
    clip = W.WebMClip("dummy-throttle.webm")

    def _boom(*_a, **_k):
        raise IOError("Could not load meta information")

    monkeypatch.setattr(W.imageio_ffmpeg, "read_frames", _boom)
    with caplog.at_level(logging.DEBUG, logger="pet.webm_clip"):
        for i in range(1, 10):
            _fail_reader(clip, i)
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errs) == 2, f"ERROR 应被节流成 2 条，实际 {len(errs)}：{[r.getMessage() for r in errs]}"
    clip.cleanup()
    QApplication.instance().processEvents()


def test_no_error_logs_while_session_ending(monkeypatch, caplog):
    """★ 会话结束窗口：无论失败多少次都不该出 ERROR（那正是 230 条的成因）。"""
    QApplication.instance() or QApplication([])
    clip = W.WebMClip("dummy-sessionending.webm")

    def _boom(*_a, **_k):
        raise IOError("Could not load meta information")

    monkeypatch.setattr(W.imageio_ffmpeg, "read_frames", _boom)
    W.set_session_ending(True)
    with caplog.at_level(logging.DEBUG, logger="pet.webm_clip"):
        for i in range(1, 13):
            _fail_reader(clip, i)
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errs, f"会话结束窗口不该有 ERROR：{[r.getMessage() for r in errs][:3]}"
    clip.cleanup()
    QApplication.instance().processEvents()


def test_reader_returns_early_while_session_ending():
    """闸门置位后 reader 不 spawn（闸门语义，issue #111）。"""
    QApplication.instance() or QApplication([])
    sample = Path("assets/characters/shenshen/videos/random/吃Token.webm")
    assert sample.exists(), f"测试素材不在：{sample}"
    W.set_session_ending(True)
    clip = W.WebMClip(sample)
    stop_evt = threading.Event()
    try:
        clip._reader_local(stop_evt, clip._generation)
        assert clip._reader_proc is None
    finally:
        clip.cleanup()
