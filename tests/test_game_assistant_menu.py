# -*- coding: utf-8 -*-
"""「游戏助理」菜单（本机追加模块）的回归测试。

钉住四件事（对应 2026-10-02 修掉的三条真问题 + 布局归属）：
  1. 子菜单能建出来：5 个可勾选项 + 一个「打开开关状态文件夹」；
  2. 勾选写盘是**原子**的，且不丢别的键；
  3. **写失败要回弹**（不能 UI 说谎）；
  4. 读不到文件时与 DEFAULTS 同口径（与 tools/command.py 对齐）；
  5. 我们的 action 必须能被菜单引擎解析出来（默认模板保持上游原样，
     我们那条由她 config 的 context_menu_layout 提供 —— 这条防止"模板恢复原样后菜单里没了"）。
"""
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging

import pytest
from PySide6.QtWidgets import QApplication, QMenu

from pet import menu_layout as menu_layout_mod
from pet.context_menus import game_assistant as GA
from pet.context_menus.registry import MENU_ACTIONS


@pytest.fixture()
def switches(tmp_path, monkeypatch):
    """把开关文件指到临时目录（不动真实的 switches.json）。"""
    path = tmp_path / "data" / "switches.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(GA, "SWITCHES", str(path))
    return path


def _app():
    return QApplication.instance() or QApplication([])


def test_menu_toggles_and_groups(switches, tmp_path, monkeypatch):
    """2026-10-08 分区版：**指挥类**（ops/slow/laptop）在前，**看与说/记录类**在后。

    2026-10-11 追加：急停改版成可勾选「停止快通道」，因此它也出现在勾选项里。
    这里只断言**语义不变量**（开关区前 7 项顺序 + 急停项的勾态与 stop 文件一致），
    不写全表字面量——字面量会随 switches.json 实况漂移，那是夹具的职责。
    """
    monkeypatch.setattr(GA, "STOP_FILE", str(tmp_path / "no_such_stop"))   # 替身：不碰真实急停
    _app()
    menu = QMenu()
    sub = GA.add_game_assistant_menu(menu, None)
    acts = [a for a in sub.actions() if a.isCheckable()]
    texts = [a.text() for a in acts]
    # 2026-10-08 契约：开关区前 7 项顺序（「慢通道思考」紧跟「AI 操作」）
    assert texts[:7] == ["AI 操作", "慢通道思考", "AI 操作笔记本", "AI 建议",
                         "AI 记录（我点哪儿）", "语音播报", "学习模式（鼠标）"], texts
    # 2026-10-11 契约：急停改可勾选，故也在勾选项里；替身路径没有 stop 文件 ⇒ 不打 √
    assert "停止快通道" in texts, "急停改版后应出现在勾选项里：%s" % texts
    assert acts[texts.index("停止快通道")].isChecked() is False, "没有 stop 文件时不该打 √"
    assert any("文件夹" in a.text() for a in sub.actions())
    assert any("巡检报告" in a.text() for a in sub.actions()), "该有「看慢通道巡检报告」这个只读入口"
    assert any("重载开关" in a.text() for a in sub.actions()), "该有「重载开关（从磁盘）」"


def _stop_act(sub):
    for a in sub.actions():
        if a.text() == "停止快通道":
            return a
    return None


def test_estop_toggle_first_and_marked(switches, tmp_path, monkeypatch):
    """急停改版（2026-10-11 秋殇定）：可勾选「停止快通道」，且仍是命令区第一项。

    语义：**有 √ = 会停，取消 √ = 解除**。醒目方式从 ⛔ 前缀改为勾选框。
    """
    monkeypatch.setattr(GA, "STOP_FILE", str(tmp_path / "no_such_stop"))
    _app()
    menu = QMenu()
    sub = GA.add_game_assistant_menu(menu, None)
    texts = [a.text() for a in sub.actions() if a.text()]
    cmds = [t for t in texts if t in ("停止快通道", "跑修炼任务（所有角色）", "看状态（推气泡）")]
    assert cmds and cmds[0] == "停止快通道", "「停止快通道」要是命令区第一项，实得：%s" % cmds
    act = _stop_act(sub)
    assert act is not None, "菜单里该有「停止快通道」"
    assert act.isCheckable(), "急停已改成可勾选（醒目方式=勾选框）"
    assert act.isChecked() is False, "没有 stop 文件时不该打 √"


def test_estop_toggle_reads_stop_file(switches, tmp_path, monkeypatch):
    """有 stop 文件 ⇒ 菜单开出来就是 √（= 会停）；这正是「取消 √ = 解除」的另一半。"""
    stop = tmp_path / "stop"
    monkeypatch.setattr(GA, "STOP_FILE", str(stop))
    _app()
    m1 = QMenu()                                  # 引用必须留住：临时 QMenu 会被 GC
    sub1 = GA.add_game_assistant_menu(m1, None)
    a1 = _stop_act(sub1)
    assert a1 is not None and a1.isChecked() is False, "文件还不存在 ⇒ 不该打 √"
    stop.write_text("x", encoding="utf-8")
    m2 = QMenu()
    sub2 = GA.add_game_assistant_menu(m2, None)
    a2 = _stop_act(sub2)
    assert a2 is not None and a2.isChecked() is True, "存在 stop 文件 ⇒ 必须打 √（有 √ = 会停）"



def test_leg_submenu_three_options(switches, tmp_path, monkeypatch):
    """慢通道腿：三选一（bridge/direct/local），勾态读 data\\brain_leg.json。"""
    _app()
    leg_json = tmp_path / "brain_leg.json"
    leg_json.parent.mkdir(parents=True, exist_ok=True)
    leg_json.write_text('{"leg": "direct"}', encoding="utf-8")
    monkeypatch.setattr(GA, "LEG_JSON", str(leg_json))
    menu = QMenu()
    sub = GA.add_game_assistant_menu(menu, None)
    leg = next(a.menu() for a in sub.actions() if a.text() == "慢通道腿")
    acts = [a for a in leg.actions() if a.isCheckable()]
    assert len(acts) == 3, [a.text() for a in acts]
    assert [a.isChecked() for a in acts] == [False, True, False], "勾态要跟 brain_leg.json 一致"
    acts[0].setChecked(True)                       # 切到 bridge
    assert json.loads(leg_json.read_text(encoding="utf-8"))["leg"] == "bridge"
    assert not acts[1].isChecked(), "三选一：切过去要把别的点掉"


def test_slow_sits_right_after_ops(switches):
    """位置契约（秋殇 2026-10-08）：「慢通道思考」就在「AI 操作」下面那一行。"""
    _app()
    menu = QMenu()                       # 必须绑定住：临时 QMenu 会被 GC ⇒ C++ 对象被删 ✗
    sub = GA.add_game_assistant_menu(menu, None)
    texts = [a.text() for a in sub.actions() if a.isCheckable()]
    assert texts.index("慢通道思考") == texts.index("AI 操作") + 1, texts


def test_ops_on_links_slow_on(switches):
    """联动：打开「AI 操作」⇒ 慢通道思考一起开（文件与菜单格子都要对）。"""
    _app()
    menu = QMenu()                       # 必须绑定住：临时 QMenu 会被 GC ⇒ C++ 对象被删 ✗
    sub = GA.add_game_assistant_menu(menu, None)
    ops = next(a for a in sub.actions() if a.text() == "AI 操作")
    ops.setChecked(True)
    got = json.loads(switches.read_text(encoding="utf-8"))
    assert got["ops"] is True and got["slow"] is True, got
    slow_act = next(a for a in sub.actions() if a.text() == "慢通道思考")
    assert slow_act.isChecked(), "菜单上那一格也要同步拨上（不能 UI 与真源不一致 ✗）"


def test_laptop_on_links_slow_on(switches, monkeypatch):
    """联动：打开「AI 操作笔记本」也把慢通道思考带上。"""
    _app()
    monkeypatch.setattr(GA, "_sync_laptop_gate", lambda on: None)   # 别真去连笔记本
    menu = QMenu()                       # 必须绑定住：临时 QMenu 会被 GC ⇒ C++ 对象被删 ✗
    sub = GA.add_game_assistant_menu(menu, None)
    lap = next(a for a in sub.actions() if a.text() == "AI 操作笔记本")
    lap.setChecked(True)
    got = json.loads(switches.read_text(encoding="utf-8"))
    assert got["laptop"] is True and got["slow"] is True, got


def test_ops_off_does_not_touch_slow(switches):
    """只联"开"的方向：关掉 AI 操作**不动**慢通道（它可以单独开着只做思考/看图）。"""
    _app()
    switches.write_text(json.dumps({"ops": True, "slow": True}), encoding="utf-8")
    menu = QMenu()                       # 必须绑定住：临时 QMenu 会被 GC ⇒ C++ 对象被删 ✗
    sub = GA.add_game_assistant_menu(menu, None)
    ops = next(a for a in sub.actions() if a.text() == "AI 操作")
    ops.setChecked(False)
    got = json.loads(switches.read_text(encoding="utf-8"))
    assert got["ops"] is False and got["slow"] is True, got


def test_toggle_writes_atomically_and_keeps_other_keys(switches):
    _app()
    switches.write_text(json.dumps({"custom": 1, "advice": False}, ensure_ascii=False), encoding="utf-8")
    menu = QMenu()
    sub = GA.add_game_assistant_menu(menu, None)
    ops = next(a for a in sub.actions() if a.text() == "AI 操作")
    ops.setChecked(True)
    got = json.loads(switches.read_text(encoding="utf-8"))
    assert got["ops"] is True, got
    assert got["custom"] == 1, "不能丢别人的键"
    assert got["advice"] is False, "不能覆盖没动的键"
    assert not switches.with_suffix(".json.tmp").exists(), "临时文件必须被 replace 掉"


def test_toggle_reverts_when_write_fails(switches, monkeypatch):
    """写失败 → 勾选状态必须回弹（否则 UI 说谎）。"""
    _app()
    monkeypatch.setattr(GA, "write_switch", lambda key, on: False)
    menu = QMenu()
    sub = GA.add_game_assistant_menu(menu, None)
    voice = next(a for a in sub.actions() if a.text() == "语音播报")
    before = voice.isChecked()
    voice.setChecked(not before)          # 用户点了一下
    assert voice.isChecked() == before, "写失败必须回弹到原状态"
    assert "回退" in voice.toolTip()


def test_defaults_match_tools_side(switches):
    _app()
    assert GA.DEFAULTS == {"ops": False, "slow": False, "advice": True, "laptop": False,
                           "record": False, "voice": True, "learn_mouse": False}
    got = GA.read_switches()              # 文件不存在 → 全默认
    assert got == GA.DEFAULTS, got
    # 与工具侧同口径（tools/command.py 的 LABELS 也要认这个键 —— 否则命令行看不到"慢通道思考"）
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "tools" / "command.py"
    if p.exists():
        spec = importlib.util.spec_from_file_location("_gc_command_labels", p)
        src = p.read_text(encoding="utf-8")
        assert '"slow": "慢通道思考"' in src, "tools/command.py 的 LABELS 里缺 slow ✗"


def test_loop_entry_found_by_its_name(switches, monkeypatch):
    """2026-10-14 秋殇反馈：菜单里找不到「交给慢通道」这个词。

    契约（两层）：
      1. 「游戏助理」命令区**第一层**必须有一个名字里含「交给慢通道」的入口
         （原路径藏在「幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色」两层里，找不到）；
      2. 它与那条老入口必须是**同一个动作**（同一 args），不是复制出来的第二套实现。
    """
    _app()
    seen = []
    monkeypatch.setattr(GA, "_spawn_quiet",
                        lambda args, label: seen.append((list(args), label)))
    menu = QMenu()                       # 必须绑定住：临时 QMenu 会被 GC ⇒ C++ 对象被删 ✗
    sub = GA.add_game_assistant_menu(menu, None)
    texts = [a.text() for a in sub.actions()]
    hit = next((a for a in sub.actions() if "交给慢通道" in a.text()), None)
    assert hit is not None, "第一层没有「交给慢通道」入口：%s" % texts
    hit.trigger()
    assert seen == [(["loop", "--pick", "all"], hit.text())], seen

    # 老路径同样指向这条命令（同一个 args ⇒ 同一条账本、同一套闸门）
    nested = next(a for a in sub.actions() if a.text() == "幻唐志")
    run = next(a for a in nested.menu().actions() if a.text() == "跑修炼任务")
    zero = next(a for a in run.menu().actions() if a.text().startswith("0 "))
    seen.clear()
    zero.trigger()
    assert [args for args, _label in seen] == [["loop", "--pick", "all"]], seen


def test_action_is_resolvable_by_menu_engine(tmp_path):
    """我们的 action 必须能被注册表与布局引擎认出来（模板保持上游原样的前提下）。"""
    _app()
    assert "game_assistant" in MENU_ACTIONS.ids
    layout = {"schema_version": 1, "layout_id": "user",
              "nodes": [{"type": "action", "id": "game_assistant", "visible": True}]}
    res = menu_layout_mod.resolve_menu_layout(
        layout, registered_actions=MENU_ACTIONS.ids, available_actions=MENU_ACTIONS.ids)
    ids = [n.get("id") for n in res.nodes]
    assert "game_assistant" in ids, f"布局引擎没认出来：{ids}"
    # 用户布局缺的默认动作会被"补"进来（default-action-added:* 属正常）；
    # 只有 unknown-action / duplicate-action 这类才算坏。
    bad = [d for d in res.diagnostics if d.startswith(("unknown-action", "duplicate-action"))]
    assert not bad, bad
