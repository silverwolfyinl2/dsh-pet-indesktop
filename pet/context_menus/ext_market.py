"""扩展市场入口（2026-10-27 加；同日改为「市场框」为主入口）。

三层分工，别混：
  1) **市场框** `tools/ext_market_box.py`  —— 桌面窗口，边浏览边勾选（主入口）
  2) **网页版** `data/ext_market/index.html` —— 单文件离线全览页（可传阅、可全屏看）
  3) **索引快照** `tools/ext_market_build.py --sync --details` —— 联网抓 JSON

为什么不做成菜单里的一堆列表项：
  市场有 167 个包、29 个官方标签，22 个游戏包还要按「怎么接入游戏」分八层；
  QMenu 装不下，也不该让右键菜单为它变长。菜单只负责"去哪儿"。

为什么市场框最终是**进程内**开窗（2026-10-27 实测后从"单起进程"改回来的）：
  原先设计成独立进程，理由是怕 1180x760 的大窗跟「透明置顶 + 点击穿透」的
  桌宠窗打架。实测两个问题更大：
    1) venv 的 pythonw.exe 是 redirector，spawn 真实解释器后自己退出，
       把子进程一起带走 —— 窗口 5 秒内消失（"一闪就没"）。
    2) 独立进程拿不到桌宠自己的 QSS 作用域，样式容易串。
  桌宠本来就是 PySide6，同进程开一个普通 QWidget 最稳，也绝不会闪控制台：
  样式挂在窗口上（不碰 QApplication），关窗只销毁窗口、不牵连桌宠。
  独立进程仍保留为**兜底**（内联失败时走 _spawn），最后再退到网页版。

自动更新（2026-10-27 加，秋殇问「那就自动每天更新呢」）：
  每次打开市场框都先起一个 `ext_market_build.py --auto`（后台、不等、不弹窗）。
  它自己看 catalog.json 的 mtime：不足 24 小时就一声不哑地退出（零网络）；
  超了才真联网抓一轮。所以：
    · 一天开好几次 —— 只抓一次，不会变频繁联网
    · 每天都开 —— 等于每天都更新
    · 几天不开 —— 不抓，因为没人看（数据旧也不影响任何东西）
  没做成 Windows 计划任务，就是因为代理端口会变（半天内见过 10102→14673），
  而计划任务的运行环境拿不到用户会话的 HTTP(S)_PROXY，很可能因直连失败而白跑。
  由「打开意图」触发反而更可靠：那个时刻一定是用户在线、网络可用的时候。

为什么一律用 pythonw.exe：
  桌宠是常驻的，任何一次派生都不能闪黑框。pythonw + CREATE_NO_WINDOW 双保险，
  这条是 2026-10-27 秋殇明确要求的（"一直在闪窗口"）。
"""
from __future__ import annotations

import logging
import os
import subprocess

from .game_assistant import HOME, _py_exe
from .shared import add_submenu

log = logging.getLogger("pet.ext_market")

MARKET_DIR = os.path.join(HOME, "data", "ext_market")
MARKET_HTML = os.path.join(MARKET_DIR, "index.html")
BUILD_TOOL = os.path.join(HOME, "tools", "ext_market_build.py")
BOX_TOOL = os.path.join(HOME, "tools", "ext_market_box.py")

# 市场框要 PySide6；项目主 .venv 是 3.10 不一定带，桌宠自己的 .venv311 一定带。
_PY_CANDIDATES = (
    os.path.join(HOME, "refs", "dsh-pet-indesktop", ".venv311", "Scripts", "pythonw.exe"),
    os.path.join(HOME, ".venv", "Scripts", "pythonw.exe"),
    os.path.join(HOME, "refs", "dsh-pet-indesktop", ".venv311", "Scripts", "python.exe"),
)


def _box_py() -> str:
    for p in _PY_CANDIDATES:
        if os.path.exists(p):
            return p
    return ""


_BOX_WINDOWS = []          # 保住引用：Qt 窗口没人引用会被立刻回收（表现为"一闪就没"）


def _open_box_inline() -> bool:
    """在桌宠进程内直接开窗 —— 不派生子进程。

    为什么内联（2026-10-27 实测后改的）：
      `pythonw.exe + Start-Process` 起的市场框会在 5 秒内消失 —— venv 的 pythonw
      是个 shim，它 spawn 真实解释器后自己退出，把子进程一起带走了。
      而桌宠本来就是 PySide6，同进程开一个普通 QWidget 最稳，也绝不会闪控制台。

    样式隔离：ext_market_box 的 QSS 挂在**窗口**上，不碰 QApplication，
    所以不会改掉桌宠自己的菜单/气泡样式。
    """
    if not os.path.exists(BOX_TOOL):
        log.warning("市场框脚本不存在：%s", BOX_TOOL)
        return False

    # 已经开着就提到前面，别开第二个
    for w in list(_BOX_WINDOWS):
        try:
            if w.isVisible():
                w.raise_()
                w.activateWindow()
                return True
        except RuntimeError:                                 # 底层 C++ 对象已销毁
            _BOX_WINDOWS.remove(w)

    try:
        import importlib.util

        from PySide6.QtCore import Qt

        spec = importlib.util.spec_from_file_location("ext_market_box", BOX_TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _app, win = mod.build_ui()          # 内部已 show()，不进 exec（桌宠的事件循环在跑）
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        _BOX_WINDOWS.append(win)
        log.info("市场框已内联打开")
        return True
    except Exception as exc:                                          # noqa: BLE001
        log.warning("市场框内联打开失败：%s", exc)
        return False


def _open(path: str) -> bool:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
    except Exception as exc:                                            # noqa: BLE001
        log.warning("打开扩展市场失败：%s", exc)
        return False


def _spawn(py: str, tool: str, args=()) -> bool:
    """安静地起一个子进程（无控制台窗口）。"""
    if not py or not os.path.exists(tool):
        log.warning("扩展市场派生不可用：py=%r tool=%s", py, tool)
        return False
    try:
        kw = {"cwd": HOME, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000                       # CREATE_NO_WINDOW
        subprocess.Popen([py, tool, *args], **kw)
        log.info("扩展市场派生：%s %s", os.path.basename(py), os.path.basename(tool))
        return True
    except Exception as exc:                                            # noqa: BLE001
        log.warning("扩展市场派生失败：%s", exc)
        return False


def _spawn_build(args) -> bool:
    """跑索引构建器。解释器只认项目 .venv（打包后 sys.executable 会是桌宠自己）。"""
    return _spawn(_py_exe(), BUILD_TOOL, args)


def add_ext_market_menu(menu, pet):
    """构建「扩展市场 ▶」子菜单。"""
    sub = add_submenu(menu, "扩展市场", "web")
    sub.setToolTip("Cortico / Coopanion 的扩展索引：按分类与接入方式筛，看清风险再选")

    def _do_box():
        """打开市场框（勾选清单，不安装），顺带做每日自动更新。

        两级：
          · 本地还没有快照 → 先同步抓（否则开出来是空框，体验更差）
          · 有快照 → 后台跑 --auto（超 24 小时才真联网，够新就秒退）
        --auto 不阻塞：框立刻显示现有数据，下次打开就是新的。
        """
        if not os.path.exists(os.path.join(MARKET_DIR, "catalog.json")):
            _spawn_build(["--sync", "--details"])
        else:
            _spawn_build(["--auto"])
        if _open_box_inline():
            return
        # 内联失败（缺 PySide6 / 脚本报错）→ 退独立进程 → 再退网页，别让入口变哑巴
        if _spawn(_box_py(), BOX_TOOL):
            return
        _do_web()

    def _do_web():
        if os.path.exists(MARKET_HTML):
            _open(MARKET_HTML)
        else:
            _spawn_build(["--sync", "--details", "--open"])

    def _do_update():
        _spawn_build(["--sync", "--details"])

    def _do_wishlist():
        fp = os.path.join(MARKET_DIR, "wishlist.json")
        if os.path.exists(fp):
            _open(fp)
        else:
            _open(MARKET_DIR)

    def _add(label, tip, fn, enabled=True):
        act = sub.addAction(label)
        act.setToolTip(tip)
        act.triggered.connect(lambda _c=False: fn())
        if not enabled:
            act.setEnabled(False)
        return act

    _add("打开市场框（勾选清单）",
         "独立窗口：左边按分类/接入方式筛，右边看工具签名，勾上的只记清单，不安装。"
         "打开时会自动检查快照年龄，超过 24 小时在后台静默补一轮",
         _do_box, enabled=bool(_box_py()) and os.path.exists(BOX_TOOL))
    _add("打开网页版（全览）",
         "单文件离线页，可全屏看、可传给别人", _do_web,
         enabled=os.path.exists(MARKET_HTML) or (bool(_py_exe()) and os.path.exists(BUILD_TOOL)))
    _add("看已选清单", "data/ext_market/wishlist.json —— 只是想要的列表", _do_wishlist)
    sub.addSeparator()
    _add("更新索引（联网）",
         "立刻抓最新（不等 24 小时）：jsDelivr / GitHub API / ghproxy 多源回退，含各包工具签名",
         _do_update, enabled=bool(_py_exe()) and os.path.exists(BUILD_TOOL))
    return sub
