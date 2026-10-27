"""游戏助理菜单（本机追加 · 2026-10-01）—— 原生可勾选动作，勾态读写我们的 switches.json。

设计与上游一致（照 pet/context_menus/shared.py 里"主动识屏"子菜单的写法）：
  · sub.addAction(text) + setCheckable(True) + setChecked(...) + toggled ✓
  · 勾态唯一真源 = <game-copilot>/data/switches.json（绝对路径，可用环境变量覆盖）
工具侧读同一个文件 → 勾上真开、点掉真停。

2026-10-02 加固（修掉三条真问题）：
  1. **原子写**：tmp + fsync + os.replace —— 以前直接覆盖写，主机侧工具正好在读的时候会
     读到半截 JSON，被当成"开关是关的"；
  2. **失败回弹**：写失败（目录不可写/被占用）时把勾选状态拨回去（blockSignals 防递归）
     —— 以前照样翻，等于 UI 说谎；
  3. **默认值单一口径**：DEFAULTS 必须与 tools/command.py 的 DEFAULTS 一致，读不到文件时
     展示的口径和工具侧完全相同（以前菜单默认 advice/voice=True、工具侧 default=False，
     会出现"菜单显示开着、工具判定关"的错位）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

from .shared import add_submenu

log = logging.getLogger("pet.game_assistant")

HOME = os.environ.get("GAMECOPILOT_HOME") or os.path.join("D:" + chr(92), "AI", "AIWORK", "game-copilot")
SWITCHES = os.path.join(HOME, "data", "switches.json")

# ★ 唯一口径：键 → 默认值（tools/command.py 的 DEFAULTS 必须与这里一致）
DEFAULTS = {"ops": False, "slow": False, "advice": True, "laptop": False, "record": False,
            "voice": True,
            "learn_mouse": False}    # 学习模式（鼠标）：游戏在跑时记下每次点击那一帧

# 联动（秋殇 2026-10-08）：「AI 操作」与「AI 操作笔记本」**打开时**顺带把「慢通道思考」也打开 ✓
# （关掉这两项**不动**慢通道 —— 它可以单独开着只做思考/看图）
LINK_TO_SLOW = ("ops", "laptop")

def _py_exe() -> str:
    """跑本项目的 .py 工具时该用哪个解释器。

    ★ 2026-10-05 踩过的坑：PyInstaller 打包后 `sys.executable` **就是桌宠自己** ✗ ——
      拿它去跑 `tools/xxx.py` 等于又起一只桌宠（秋殇实测：勾一次开关多一个桌宠 ✓）。
    规则：项目 .venv 优先；frozen 且找不到 venv ⇒ **返回空串，让调用方跳过**
    （宁可这一轮不同步，也绝不拉出新桌宠 ✗）。
    """
    for name in ("python.exe", "pythonw.exe"):
        cand = os.path.join(HOME, ".venv", "Scripts", name)
        if os.path.exists(cand):
            return cand
    if getattr(sys, "frozen", False):
        return ""
    return getattr(sys, "executable", "") or ""


# 开关分两段（2026-10-08 秋殇要的"分区"）：**指挥类**（动不动手）与**看与说 / 记录类**
COMMAND_ITEMS = (
    ("ops", "AI 操作", "在后台按键与点击（AALC 同款后台投递）"),
    ("slow", "慢通道思考", "让慢通道（云端那套）看画面、定策略、指挥快通道；"
                           "打开上面「AI 操作」时会自动带上它"),
    ("laptop", "AI 操作笔记本", "把动作发给笔记本执行（打开时也会带上慢通道思考）"),
)
VIEW_ITEMS = (
    ("advice", "AI 建议", "看画面与裁判层后给建议"),
    ("record", "AI 记录（我点哪儿）", "记录我在游戏里主要点击哪里（只记坐标，不记画面）"),
    ("voice", "语音播报", "把建议念出来"),
    ("learn_mouse", "学习模式（鼠标）", "游戏在跑时，记下我每次点击的那一瞬间画面"),
)
ITEMS = COMMAND_ITEMS + VIEW_ITEMS

# 慢通道腿（真源 data\brain_leg.json）：bridge=走 DSH 凭证桥 / direct=DeepSeek 直连 / local=本机 ollama
LEG_JSON = os.path.join(HOME, "data", "brain_leg.json")
LEG_ITEMS = (("bridge", "bridge（DSH 桥 · 有视觉）"),
             ("direct", "direct（DeepSeek 直连 · 有视觉）"),
             ("local", "local（本机 ollama · 不带图）"))


def read_leg() -> str:
    try:
        with open(LEG_JSON, encoding="utf-8") as fh:
            return str(json.load(fh).get("leg") or "bridge")
    except Exception:                                            # noqa: BLE001
        return "bridge"


def write_leg(name: str) -> bool:
    """写默认腿（原子：tmp + replace）。失败只记日志、返回 False（调用方把勾拨回去）。"""
    tmp = "%s.tmp.%d" % (LEG_JSON, os.getpid())
    try:
        os.makedirs(os.path.dirname(LEG_JSON), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"leg": str(name)}, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, LEG_JSON)
        log.info("慢通道腿切到：%s", name)
        return True
    except OSError as exc:
        log.warning("写腿失败（%s）：%s", LEG_JSON, exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def read_switches():
    """读开关：文件缺/坏都不算错，落回 DEFAULTS（与工具侧同口径）。"""
    data = dict(DEFAULTS)
    try:
        with open(SWITCHES, encoding="utf-8") as fh:
            got = json.load(fh)
        if isinstance(got, dict):
            data.update({k: bool(v) for k, v in got.items()})
    except FileNotFoundError:
        pass
    except Exception:                                            # noqa: BLE001
        log.warning("switches.json 读不了（按默认值显示）：%s", SWITCHES)
    return data


def _sync_laptop_gate(on) -> None:
    """把「AI 操作笔记本」开关同步到那台的抓屏闸门（关了那台就不抓屏）。

    2026-10-04 秋殇定：主机这个开关就是笔记本抓屏的总闸 —— 关了就不截图抓取
    （那台的桌面记录器会喂云端视觉，花钱 ✗）。**只同步，不动任何勾选**：
    失败只记日志（笔记本没开机时同步不了很正常）。
    """
    try:
        tool = os.path.join(HOME, "tools", "laptop_gate_sync.py")
        if not os.path.exists(tool):
            return
        exe = _py_exe()
        if not exe:
            log.info("抓屏闸门同步跳过：找不到可用的 python（frozen 环境下不能用桌宠自己的 exe ✗）")
            return
        kw = {"cwd": HOME, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000                      # CREATE_NO_WINDOW
        subprocess.Popen([exe, tool, "--quiet"], **kw)
        log.info("抓屏闸门同步已发起：laptop=%s", on)
    except Exception as exc:                                          # noqa: BLE001
        log.warning("抓屏闸门同步失败（不影响开关本身）：%s", exc)


def _sync_all_switches() -> None:
    """勾选后立刻同步一轮（主机 ↔ 笔记本）。失败只记日志，绝不拦住勾选。"""
    try:
        import subprocess
        import sys as _sys
        tool = os.path.join(HOME, "tools", "switches_sync.py")
        if not os.path.exists(tool):
            return
        exe = _py_exe()
        if not exe:
            log.info("开关同步跳过：找不到可用的 python（frozen 下 sys.executable 是桌宠 exe ✗）")
            return
        subprocess.Popen([exe, tool, "--once"], cwd=HOME, close_fds=True)
    except Exception as exc:                                            # noqa: BLE001
        log.warning("开关同步失败（不影响勾选）：%s", exc)


def write_switch(key, on) -> bool:
    """原子写一个开关；成功 True / 失败 False（调用方负责回弹勾选状态）。

    2026-10-08 加联动：打开 `ops`（AI 操作）或 `laptop`（AI 操作笔记本）时，**顺带把 `slow`
    （慢通道思考）写开** —— 秋殇要的语义："打开 AI 操作就开启慢通道思考"。只联"开"的方向 ✓
    （关掉 ops/laptop 不动 slow，它可以单独开着只做思考/看图）。
    """
    data = read_switches()
    data[key] = bool(on)
    if on and key in LINK_TO_SLOW and not data.get("slow"):
        data["slow"] = True
        log.info("联动：%s=on ⇒ slow(慢通道思考)=on", key)
    tmp = "%s.tmp.%d" % (SWITCHES, os.getpid())      # 固定 .tmp 会与同步进程互锁（WinError 32）✗
    try:
        os.makedirs(os.path.dirname(SWITCHES), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        last = None
        for _i in range(4):                     # 瞬时被占用（对方正在读）就重试，别把勾选弹回去 ✓
            try:
                os.replace(tmp, SWITCHES)       # 原子替换：读方永远看到完整文件
                last = None
                break
            except PermissionError as _exc:
                last = _exc
                import time as _t
                _t.sleep(0.15 * (_i + 1))
        if last is not None:
            raise last
        if key == "laptop":
            _sync_laptop_gate(data.get("laptop"))
        _sync_all_switches()                    # 勾完立刻两机同步一轮（秋殇 2026-10-04）
        return True
    except OSError as exc:
        log.warning("switches.json 写失败（%s）：%s", exc, SWITCHES)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def switch_on(key, default=None):
    if default is None:
        default = DEFAULTS.get(key, False)
    return bool(read_switches().get(key, default))


CHARS_CACHE = os.path.join(HOME, "data", "htz_chars.json")
STOP_FILE = os.path.join(HOME, "data", "agent_bus", "stop")


def _chars_from_cache(path: str) -> list:
    """读角色名单缓存（由 tools\\htz_chars.py --refresh 写；数据源=htz_daily.list_characters）。

    菜单是**同步**构建的，绝不能在这儿跑子进程（会卡住右键菜单）⇒ 只读缓存；
    没有/坏了就返回空表，菜单里如实显示「还没读到角色名」。
    """
    try:
        import json as _json
        with open(path, encoding="utf-8") as fh:
            data = _json.load(fh)
        out = []
        for i, c in enumerate(list(data.get("chars") or []), start=1):
            c = c or {}
            no = int(c.get("no") or i)
            nm = str(c.get("name") or "").strip()
            out.append({"no": no, "name": nm or ("第 %d 个" % no)})
        return out
    except Exception:                                                    # noqa: BLE001
        return []


def _spawn_quiet(args, label: str) -> None:
    """安静地拉起 `tools\\pet_actions.py <args>`（无控制台窗口，输出追加 data\\pet_actions.log）。

    解释器一律 `_py_exe()`（打包后 `sys.executable` 是桌宠自己 ✗，拿它跑 py 等于又起一只桌宠）；
    拿不到就不动 —— 宁可这一轮不干，也绝不拉出新桌宠。
    """
    py = _py_exe()
    tool = os.path.join(HOME, "tools", "pet_actions.py")
    if not py or not os.path.exists(tool):
        log.warning("动作「%s」不可用：py=%r tool=%s", label, py, tool)
        return
    try:
        logf = os.path.join(HOME, "data", "pet_actions.log")
        with open(logf, "a", encoding="utf-8") as fh:
            subprocess.Popen([py, tool, *args], cwd=HOME, stdout=fh,
                             stderr=subprocess.STDOUT, creationflags=0x00000008)
    except Exception as exc:                                     # noqa: BLE001
        log.warning("动作「%s」拉起失败：%s", label, exc)


def _add_stop(parent):
    """「停止快通道」—— 可勾选；真源 = `data\\agent_bus\\stop` 文件在不在。

    打 √ = 写 stop（快通道下一轮开头停），取消 √ = 删掉。
    勾态在**开菜单时**读一次（只读存在性，同步安全、不起子进程）。

    2026-10-27：从 `_add_commands` 内部提出来成为模块级 —— 原来注释写着"急停在最前"，
    实际它排在 7 个开关后面（注释与实现不符），提到子菜单最前就得能在外面调。
    """
    py = _py_exe()
    tool = os.path.join(HOME, "tools", "pet_actions.py")
    act = parent.addAction("停止快通道")
    act.setCheckable(True)
    try:
        act.setChecked(os.path.exists(STOP_FILE))          # 先定态、后 connect，避免构建期误触发
    except OSError:
        pass
    act.setToolTip("打 √ = 快通道下一轮开头就停；取消 √ = 解除急停")
    if not py or not os.path.exists(tool):
        act.setEnabled(False)
        act.setToolTip("找不到项目 .venv / pet_actions.py，已禁用")
        return act

    def _on_toggle(on, a=act):
        _spawn_quiet(["stop"] if on else ["clear-stop"],
                     "停止快通道" if on else "解除急停")
        a.setToolTip("已打 √：急停生效（下一轮开头停）；再点一下即解除" if on
                     else "已解除：快通道可以跑")

    act.toggled.connect(_on_toggle)
    return act


def _add_commands(sub, pet) -> None:
    """命令项（不带勾）：跑修炼任务 / 急停 / 看状态 / 问慢通道（2026-10-07 秋殇要的）。

    与开关项的区别：开关项写 switches.json；命令项**拉起项目里的 tools/pet_actions.py**，
    由它去干活、并把结果推到明面气泡。解释器一律用 `_py_exe()`（打包后 sys.executable 是
    桌宠自己 ✗），拿不到就**禁用该项**，绝不起第二只桌宠。
    """
    py = _py_exe()
    tool = os.path.join(HOME, "tools", "pet_actions.py")

    def _spawn(args, label):
        _spawn_quiet(args, label)

    def _add_on(parent, label, args, tip):
        act = parent.addAction(label)
        act.setToolTip(tip)
        act.triggered.connect(lambda _c=False, a=args, l=label: _spawn(a, l))
        if not py or not os.path.exists(tool):
            act.setEnabled(False)
            act.setToolTip("找不到项目 .venv / pet_actions.py，已禁用")
        return act

    def _add(label, args, tip):
        return _add_on(sub, label, args, tip)

    # 2026-10-27 整理（秋殇：重复的删、该收的收）：
    #   · 「停止快通道」不再在这里建 —— 提到 add_game_assistant_menu 的最前面（急停要好找）
    #   · 删掉原「交给慢通道（跑全）」：它与「幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色（跑全）」
    #     是同一个 args（loop --pick all）、同一条账本、同一套闸门，两处并存只让人选两次
    # 2026-10-11 秋殇定调：合并成一项 + **右箭头子菜单（号 + 角色名）**，一律走慢通道受理
    # （指令账本 + 三道闸门 + 唯一终态 + 审计 + 气泡回传）。
    # 日常"修炼任务每天跑全"⇒ 点第 0 项；1..N 是"只跑某一个"的口子（账本会把选中的号记下来）。
    # 真跑（不经账本）仍可用：一键脚本\00-游戏助手 → [1]。
    # 2026-10-11 秋殇定调：外面套一层「幻唐志 ▶」，里面再是「跑修炼任务 ▶（号+名字）」，
    # 名字以 data\\htz_party.json 为准（不 OCR）；一律走慢通道账本；真跑另用一键脚本 00 → [1]。
    _htz = sub.addMenu("幻唐志")
    _htz.setToolTip("幻唐志相关：跑修炼任务 / 实跑前预检（走慢通道账本·当前模拟档，不碰游戏）")
    _run = _htz.addMenu("跑修炼任务")
    _run.setToolTip("每天跑全；号=右下角队伍条左→右第 N 格（名单见 data\\htz_party.json）")
    _add_on(_run, "0 全部角色（跑全）", ["loop", "--pick", "all"],
            "全角色串行（经慢通道受理；当前模拟档不碰游戏）")
    _chars = _chars_from_cache(CHARS_CACHE)
    if _chars:
        for _c in _chars:
            _sect = _c.get("sect") or "?"
            _add_on(_run, "%d %s" % (_c["no"], _c["name"]),
                    ["loop", "--pick", str(_c["no"])],
                    "只跑第 %d 个：%s（门派 %s）；账本会记下这一号" % (_c["no"], _c["name"], _sect))
    else:
        _none = _run.addAction("（还没读到角色名）")
        _none.setEnabled(False)
        _none.setToolTip("跑一次：.venv\\Scripts\\python tools\\htz_chars.py --refresh")
    _add_on(_htz, "幻唐志预检（实跑前先跑）", ["precheck"],
            "跑 tools\\htz_preflight.py；结果写 data\\preflight_last.log")
    _add("看状态（推气泡）", ["status"], "目标/开跑/急停/最近分数/慢脑用哪条腿")
    # 「问慢通道…」「看慢通道巡检报告」不在这里 —— 它们属于慢通道本身，见 _add_slow_tools()


def _add_slow_tools(parent) -> None:
    """慢通道自己的两个动作：问一句 / 看巡检报告。

    2026-10-27 从 `_add_commands` 挪进来 —— 它们原来散在命令区末尾，跟「幻唐志预检」
    「看状态」混在一起，而它们其实是慢通道的入口与体检，应该跟「慢通道思考」「慢通道腿」同处。
    """
    py = _py_exe()
    tool = os.path.join(HOME, "tools", "pet_actions.py")

    ask = parent.addAction("问慢通道…")
    ask.setToolTip("弹个小框问一句，回答直接弹在气泡上（不经过 DSH）")

    def _on_ask():
        try:
            from PySide6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getText(None, "问慢通道", "要问什么？（例：现在卡在哪）")
        except Exception:                                            # noqa: BLE001
            return
        if ok and str(text).strip():
            _spawn_quiet(["ask", str(text).strip()], "问慢通道")

    ask.triggered.connect(lambda _c=False: _on_ask())
    if not py or not os.path.exists(tool):
        ask.setEnabled(False)
        ask.setToolTip("找不到项目 .venv / pet_actions.py，已禁用")

    # 2026-10-08 加：只看不改 —— 打开 DSH 的慢通道巡检报告（tools\slow_channel_audit.py 的产物）
    rep = parent.addAction("看慢通道巡检报告")
    rep.setToolTip("只读：慢通道的腿/错误率/眼的命中率/它签发的改动，报告在 data\\slow_channel_audit.md")
    audit_md = os.path.join(HOME, "data", "slow_channel_audit.md")
    if not os.path.exists(audit_md):
        rep.setToolTip(rep.toolTip() + "（还没生成过 —— 跑一次 tools\\slow_channel_audit.py）")

    def _open_report():
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            if not os.path.exists(audit_md):           # 没有就先让 py 生成一份（只读工具，安全）
                _spawn_quiet(["audit"], "看慢通道巡检报告")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(audit_md))
        except Exception as exc:                                         # noqa: BLE001
            log.warning("打开巡检报告失败：%s", exc)

    rep.triggered.connect(lambda _c=False: _open_report())


# ── 开关分组（2026-10-27 整理）──────────────────────────────────────────────
# 一级只留 8 项。7 个开关按「要不要动手」分三组进子菜单：
#   动手       —— 会真的去按键/点击（ops / laptop）
#   看与说     —— 只看、只说，不动手（advice / voice）
#   记录与学习 —— 攒素材，为以后学（record / learn_mouse）
# 「slow（慢通道思考）」不在这三组 —— 它是慢通道本身的开关，跟它的腿、问、体检放一起。
SWITCH_GROUPS = (
    ("动手", "interaction", ("ops", "laptop")),
    ("看与说", "chat", ("advice", "voice")),
    ("记录与学习", "screen", ("record", "learn_mouse")),
)
SWITCH_LABELS = {key: (label, tip) for key, label, tip in ITEMS}


def add_game_assistant_menu(menu, pet):
    """构建「游戏助理」子菜单。

    2026-10-27 整理（秋殇：把分类里的重复和不要的收拾一下）：
      · 一级从 14 项收到 8 项：急停 / 三组开关 / 慢通道 / 幻唐志 / 看状态 / 维护
      · 「慢通道腿」「问慢通道…」「看慢通道巡检报告」收进「慢通道 ▶」
        （原来一个夹在开关与命令之间、两个散在命令区末尾）
      · 删掉「交给慢通道（跑全）」：与「幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色（跑全）」
        同一个 args / 同一条账本 / 同一套闸门，两处并存只让人选两次
      · 「重载开关」「打开开关状态文件夹」收进「维护 ▶」—— 运维项不跟日常开关抢位置
      · 「停止快通道」提到子菜单最前（原注释写着急停最前，实际排在 7 个开关后面）
    """
    sub = add_submenu(menu, "游戏助理", "automation")
    acts: dict = {}

    def _make_handler(action, key):
        def _on_toggled(on):
            ok = write_switch(key, on)
            # ★ 诊断（2026-10-05）：勾选这条路上原来"成功不写日志"⇒ 出了事查不出 ✗
            log.info("菜单勾选：%s -> %s（写入 %s）", key, bool(on), "成功" if ok else "失败")
            if ok:
                # 联动后**把菜单上那一格也拨对**（否则 UI 与真源不一致 ✗）
                if on and key in LINK_TO_SLOW:
                    slow_act = acts.get("slow")
                    if slow_act is not None and not slow_act.isChecked():
                        slow_act.blockSignals(True)
                        try:
                            slow_act.setChecked(True)
                        finally:
                            slow_act.blockSignals(False)
                        log.info("联动：菜单「慢通道思考」已同步为勾上")
                return
            # 写失败 → 把勾拨回去：宁可显示"没点上"，也不要 UI 说谎
            action.blockSignals(True)
            try:
                action.setChecked(not bool(on))
            finally:
                action.blockSignals(False)
            action.setToolTip("写不进 %s（勾选已回退）" % SWITCHES)
        return _on_toggled

    def _switch(parent, key, extra_tip=""):
        """在指定父菜单下建一个可勾选开关（原生右侧蓝勾），勾态写 switches.json。"""
        label, tip = SWITCH_LABELS[key]
        if extra_tip:
            tip = "%s%s" % (tip, extra_tip)
        act = parent.addAction(label)
        act.setCheckable(True)
        act.setChecked(switch_on(key))
        act.setToolTip(tip)
        acts[key] = act
        act.toggled.connect(_make_handler(act, key))
        return act

    # ① 急停 —— 最前（紧急时好找）
    _add_stop(sub)
    sub.addSeparator()

    # ② 三组开关
    for label, icon, keys in SWITCH_GROUPS:
        group = add_submenu(sub, label, icon)
        for key in keys:
            _switch(group, key)

    # ③ 慢通道：开关 + 腿 + 问 + 体检
    slow_menu = add_submenu(sub, "慢通道", "automation")
    slow_menu.setToolTip("慢通道思考：开关、走哪条腿、问它一句、看它的体检报告")
    _switch(slow_menu, "slow", extra_tip="（真源：%s）" % SWITCHES)
    slow_menu.addSeparator()

    # ── 慢通道腿（三选一；真源 data\brain_leg.json） ─────────────────────────────
    leg_menu = add_submenu(slow_menu, "慢通道腿", "automation")
    leg_menu.setToolTip("慢通道思考走哪条腿：bridge / direct 能看图，local 只做文本兜底")
    cur = read_leg()
    leg_acts = {}
    for name, label in LEG_ITEMS:
        a = leg_menu.addAction(label)
        a.setCheckable(True)
        a.setChecked(name == cur)

        def _on_leg(on, _name=name, _ref=a):
            if not on:                            # 点掉当前项 = 不换（保持三选一）
                return
            if not write_leg(_name):
                _ref.blockSignals(True)
                _ref.setChecked(False)
                _ref.blockSignals(False)
                _ref.setToolTip("写不进 %s（已回退）" % LEG_JSON)
                return
            for other_name, other in leg_acts.items():
                if other_name != _name and other.isChecked():
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
            _spawn_quiet(["leg", _name], "慢通道腿")

        leg_acts[name] = a
        a.toggled.connect(_on_leg)
    leg_menu.setEnabled(bool(_py_exe()) and os.path.exists(os.path.join(HOME, "tools", "pet_actions.py")))

    slow_menu.addSeparator()
    _add_slow_tools(slow_menu)

    # ④ 幻唐志 / 看状态
    sub.addSeparator()
    _add_commands(sub, pet)

    # ⑤ 维护 —— 运维项集中放，不跟日常开关抢位置
    sub.addSeparator()
    safe = add_submenu(sub, "维护", "settings")

    def _on_reload():
        now = read_switches()
        for k, a in acts.items():
            want = bool(now.get(k, DEFAULTS.get(k, False)))
            if a.isChecked() != want:
                a.blockSignals(True)
                try:
                    a.setChecked(want)
                finally:
                    a.blockSignals(False)
        log.info("菜单重载开关：%s", {k: bool(now.get(k)) for k in acts})

    reload_act = safe.addAction("重载开关（从磁盘）")
    reload_act.setToolTip("命令行/同步进程改过 switches.json 时，把菜单勾态刷成真源的样子")
    reload_act.triggered.connect(lambda _c=False: _on_reload())

    folder = safe.addAction("打开开关状态文件夹")
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        folder.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(SWITCHES))))
    except Exception:                                            # noqa: BLE001
        folder.setEnabled(False)

    return sub
