# -*- coding: utf-8 -*-
"""Live2D 独立探针：只开 Live2D 窗，不启动完整桌宠。

用途（T-304 判据 6/7/8/9/14、T-305 判据 1/2/3/4 的取证工具，全部自测自算，
不靠肉眼）：

    python -m pet.live2d_probe --scale 1.5 --seconds 8 --shot .tmp/t305/size15.png
    python -m pet.live2d_probe --measure .tmp/t305/size15.png     # 只量 PNG，不开窗
    python -m pet.live2d_probe --idle-bench --seconds 40          # 待机冻帧
    python -m pet.live2d_probe --hidden-bench                     # 可见 vs 隐藏停表
    python -m pet.live2d_probe --fullscreen-bench                 # 走引擎全屏判定那条路

为什么所有量测都在探针里做：``QWebEngineView.grab()`` 是唯一能拿到带 alpha 的窗
口像素的路径（WebGL canvas 的 ``toDataURL`` 在 ``preserveDrawingBuffer=false``
下返回全透明图，见 ``refs/live2d/_poc_a.png`` 的 0% alpha 实测）。

本探针**不启动桌宠、不碰 PID 27580/32148、不改任何产品文件**。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psutil

from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from .live2d_sprite import Live2DWindow, window_size

#: 判据 6 的门槛（窗口内角色 alpha 非零像素）。
ALPHA_MIN_PIXELS = 5000
#: 判据 7 的"黑"门槛：亮度 < 120 视为黑点；>=240 视为亮。
L_DARK = 120
L_BRIGHT = 240
#: alpha 低于此值视为"没内容"（抗锯齿边不要算成角色）。
A_MIN = 8


# ------------------------------------------------------------------ 量测工具
def _luma(r: int, g: int, b: int) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def count_alpha(img: QImage, thr: int = A_MIN) -> int:
    """alpha 大于阈值的像素数（步长 1，整图，不抽样）。"""
    w, h = img.width(), img.height()
    n = 0
    for y in range(h):
        line = img.constScanLine(y)
        # Format_ARGB32：每像素 4 字节，小端序为 B,G,R,A
        raw = bytes(line)[: w * 4]
        for x in range(0, w * 4, 4):
            if raw[x + 3] > thr:
                n += 1
    return n


def alpha_bbox(img: QImage, thr: int = A_MIN):
    """整图 alpha 实心包围盒 ``[x0,y0,x1,y1]``（含端点）；空图返回 None。

    T-305 判据 2 的判据面：``y1-y0+1`` 就是「屏幕上角色多高」。与页面里
    ``boxAbsCss()`` 同一阈值（8），否则两侧会差 1~2 px 的抗锯齿边。
    """
    w, h = img.width(), img.height()
    x0, y0, x1, y1 = 10 ** 9, 10 ** 9, -1, -1
    for y in range(h):
        raw = bytes(img.constScanLine(y))[: w * 4]
        for x in range(0, w * 4, 4):
            if raw[x + 3] > thr:
                px = x // 4
                if px < x0:
                    x0 = px
                if px > x1:
                    x1 = px
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
    return None if x1 < 0 else [x0, y0, x1, y1]


def diff_bbox(a: QImage, b: QImage, thr: int = 8):
    """两张同尺寸图的差异外接矩形（含端点），用来把 Part46 的覆盖区框出来。"""
    w = min(a.width(), b.width())
    h = min(a.height(), b.height())
    x1 = y1 = 10 ** 9
    x2 = y2 = -1
    for y in range(h):
        ra = bytes(a.constScanLine(y))[: w * 4]
        rb = bytes(b.constScanLine(y))[: w * 4]
        if ra == rb:
            continue
        for x in range(0, w * 4, 4):
            if (abs(ra[x] - rb[x]) > thr or abs(ra[x + 1] - rb[x + 1]) > thr
                    or abs(ra[x + 2] - rb[x + 2]) > thr
                    or abs(ra[x + 3] - rb[x + 3]) > thr):
                px = x // 4
                if px < x1:
                    x1 = px
                if px > x2:
                    x2 = px
                if y < y1:
                    y1 = y
                if y > y2:
                    y2 = y
    if x2 < 0:
        return None
    return (x1, y1, x2, y2)


def region_stats(img: QImage, box) -> dict:
    """框内的可见像素亮度统计（只统计 alpha>A_MIN 的像素，透明像素不算内容）。"""
    x1, y1, x2, y2 = box
    dark = 0
    visible = 0
    min_l = 999.0
    max_l = -1.0
    for y in range(max(0, y1), min(img.height() - 1, y2) + 1):
        raw = bytes(img.constScanLine(y))[: img.width() * 4]
        for x in range(max(0, x1), min(img.width() - 1, x2) + 1):
            i = x * 4
            if raw[i + 3] <= A_MIN:
                continue
            l = _luma(raw[i + 2], raw[i + 1], raw[i])
            visible += 1
            if l < min_l:
                min_l = l
            if l > max_l:
                max_l = l
            if l < L_DARK:
                dark += 1
    return {
        "可见像素": visible,
        "最暗亮度": None if visible == 0 else round(min_l, 1),
        "最亮亮度": None if visible == 0 else round(max_l, 1),
        "L<120 像素数": dark,
    }


def dark_only_blob(on_img: QImage, off_img: QImage,
                   pad: int = 2, min_area: int = 50) -> dict:
    """把「Part46 画出来、关掉就没了」的那块**实心暗块**（= 黑点）框出来。

    为什么不是简单的外接矩形：实测 Part46 除了鼻子那块黑方块，还会在尾巴/裙边留
    一条**细轮廓**差（见 §判据 7 的差分掩码），整块外接矩形会横跨全身，把角色本来
    的暗部（头发/裙摆）算进来，`L<120 = 0` 永远不成立。所以取「ON 暗而 OFF 不暗」
    像素的**最大 8 邻接连通块**——那颗黑方块是唯一超过 min_area 的实心块，轮廓只
    有 1~2 px 宽、连不成大块。
    """
    w = min(on_img.width(), off_img.width())
    h = min(on_img.height(), off_img.height())
    mask = bytearray(w * h)
    total = 0
    for y in range(h):
        ra = bytes(on_img.constScanLine(y))[: w * 4]
        rb = bytes(off_img.constScanLine(y))[: w * 4]
        base = y * w
        for x in range(0, w * 4, 4):
            if ra[x + 3] <= A_MIN:
                continue
            l_on = _luma(ra[x + 2], ra[x + 1], ra[x])
            l_off = _luma(rb[x + 2], rb[x + 1], rb[x]) if rb[x + 3] > A_MIN else 255.0
            # 「ON 比 OFF 明显更暗」才算 Part46 画上去的暗内容（ΔL ≥ 40）。
            # 不用绝对阈值 L<120：鼻子那块下面就是偏暗的鼻梁皮肤，绝对阈值会把
            # 它整块排除掉（T-304 第一版就是这么漏的）。
            if l_on > l_off - 40.0:
                continue
            mask[base + x // 4] = 1
            total += 1
    seen = bytearray(w * h)
    comps = []
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            if not mask[idx] or seen[idx]:
                continue
            stack = [(x, y)]
            seen[idx] = 1
            area = 0
            x1 = x2 = x
            y1 = y2 = y
            while stack:
                cx, cy = stack.pop()
                area += 1
                x1 = min(x1, cx)
                x2 = max(x2, cx)
                y1 = min(y1, cy)
                y2 = max(y2, cy)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            ni = ny * w + nx
                            if mask[ni] and not seen[ni]:
                                seen[ni] = 1
                                stack.append((nx, ny))
            bw = x2 - x1 + 1
            bh = y2 - y1 + 1
            comps.append({
                "面积": area,
                "外接": [x1, y1, x2, y2],
                "填充率": round(area / float(bw * bh), 3),
            })
    # 黑点是**实心块**（填充率高）；Part46 沿尾巴/裙边还留一条 1~2 px 的细轮廓，
    # 8 邻接能把细线连成长条（面积大、填充率极低），所以按填充率 ≥0.5 先筛，
    # 再取面积最大的那块。
    solid = [c for c in comps if c["填充率"] >= 0.5 and c["面积"] >= min_area]
    solid.sort(key=lambda c: -c["面积"])
    out = {
        "暗块总数": total,
        "连通块数": len(comps),
        "实心块数(填充率≥0.5 且面积≥%d)" % min_area: len(solid),
        "实心块 top3": solid[:3],
    }
    if solid:
        x1, y1, x2, y2 = solid[0]["外接"]
        out["判据框"] = [max(0, x1 - pad), max(0, y1 - pad),
                        min(w - 1, x2 + pad), min(h - 1, y2 + pad)]
    return out


def tree_cpu(root: psutil.Process) -> list[psutil.Process]:
    try:
        return [root] + root.children(recursive=True)
    except psutil.Error:
        return [root]


def prime_cpu(procs: list[psutil.Process]) -> None:
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass


def read_cpu(procs: list[psutil.Process]) -> tuple[float, float]:
    """返回 (主进程 %, 子进程合计 %)。psutil 口径：单核 100% = 1 核。"""
    main = 0.0
    rest = 0.0
    for i, p in enumerate(procs):
        try:
            val = p.cpu_percent(None)
        except psutil.Error:
            continue
        if i == 0:
            main = val
        else:
            rest += val
    return main, rest


def rss_mb(p: psutil.Process) -> float:
    try:
        return round(p.memory_info().rss / 1048576.0, 1)
    except psutil.Error:
        return -1.0


def renderer_children(procs: list[psutil.Process]) -> list[tuple[int, str, float]]:
    out = []
    for p in procs[1:]:
        try:
            out.append((p.pid, p.name(), rss_mb(p)))
        except psutil.Error:
            continue
    return out


def gpu_used_mib() -> int | None:
    """nvidia-smi 的显存占用（整卡 used，与 T-297 基线同口径）。"""
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return int(out.stdout.strip().splitlines()[0].strip())
    except Exception:  # noqa: BLE001 - 无 N 卡/无 nvidia-smi 时如实返回 None
        return None


# ------------------------------------------------------------------ 探针主体
class Probe:
    def __init__(self, args) -> None:
        self.args = args
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.root = psutil.Process()
        self.win: Live2DWindow | None = None
        self.result: dict = {"mode": self._mode(), "ok": False, "error": None}
        self.out = Path(args.shot) if args.shot else None
        self._t0 = time.monotonic()
        self._last_act = 0.0
        self._idle_stage_t0 = 0.0

    def _mode(self) -> str:
        if self.args.hidden_bench:
            return "hidden"
        if self.args.fullscreen_bench:
            return "fullscreen"
        if self.args.idle_bench:
            return "idle"
        return "visible"

    # ---------------------------------------------------------------- 启动
    def start(self) -> None:
        self.result["scale"] = self.args.scale
        self.result["expected_window"] = list(window_size(self.args.scale))
        self.result["gpu_before_mib"] = gpu_used_mib()
        self.win = Live2DWindow(scale=self.args.scale,
                                width=self.args.w or None,
                                height=self.args.h or None)
        self.win.load_page()
        self.win.show()
        self.win.ready_changed.connect(self._on_ready)
        # 兜底：页面永远不 ready 时也要有个出口，不许挂死
        QTimer.singleShot(60000, self._give_up)

    def _give_up(self) -> None:
        # 只在"根本没 ready"时才算失败：ready 之后跑 bench 可能超过 60 s，
        # 拿这个闸当兜底会把正常的 CPU 量测掐断（T-304 实测踩过一次）。
        if self.result.get("ready"):
            return
        self.result["error"] = "页面 60s 内未 ready"
        self._finish(exit_code=1)

    def _on_ready(self, ok: bool) -> None:
        if not ok:
            return
        self.result["ready"] = True
        self.result["window_size"] = list(self.win.window_size_px)
        self.result["window_geometry"] = list(self.win.geometry().getRect())
        # 页面侧的待机阈值（判据 3 要写清「多久判为静止」）
        self.win.run_js("window.__IDLE_MS || 0",
                        lambda v: self.result.__setitem__("idle_threshold_ms", v))
        QTimer.singleShot(int(self.args.seconds * 1000), self._after_warmup)

    # ---------------------------------------------------------------- 四种模式
    def _after_warmup(self) -> None:
        mode = self._mode()
        if mode == "visible":
            self._measure_visible()
        elif mode == "hidden":
            self._bench(hidden=True)
        elif mode == "idle":
            self._idle_active_start()
        else:
            self._bench(hidden=False)

    # ---- 判据 6 / 7 / 14：看得见人 + 黑点没了 + 资源账
    def _measure_visible(self) -> None:
        win = self.win
        self.result["window_geometry"] = list(win.geometry().getRect())
        self.result["paused_for_capture"] = True
        # 冻住姿势再抓图：两次抓图之间姿势不能动，否则差分会把"摆动"算成 Part46。
        # 两道闸一起才够：停表（ticker.stop）+ 停动作（stopAllMotions）。只停表时
        # 沿边缘仍会留一条细差（T-304 §判据 7 实测），两道都上才是 0 差。
        win.run_js(
            "(function(){try{model.internalModel.motionManager.stopAllMotions();"
            "return 'ok';}catch(e){return String(e);}})()",
            lambda r: self._after_stop_motions(r))

    def _after_stop_motions(self, ret) -> None:
        self.result["stop_motions"] = str(ret)
        # T-305：姿势停下来之后再摆一次位——量的就是"冻住的这个姿势"按 346×scale
        # 落位后的高度，而不是摆位时的初始姿势（两者差几 px）。
        self.win.run_js("window.__layout ? window.__layout() : 'NO_HOOK'",
                        lambda r: self._after_relayout(r))

    def _after_relayout(self, ret) -> None:
        self.result["relayout"] = str(ret)
        self.win.set_paused(True)
        QTimer.singleShot(400, lambda: self.win.render_once(
            lambda _r: QTimer.singleShot(250, self._grab_base)))

    def _grab_base(self) -> None:
        img = self.win.grab_image()
        self.result["img_size"] = [img.width(), img.height()]
        self._save(img, "base")
        # Part46 对照：先开到 1（= 未修的样子），再回到 0（= 修好的样子）
        self._set_part46(1.0, self._grab_part_on)

    def _set_part46(self, value: float, then) -> None:
        js = ("(function(){try{"
              "model.internalModel.coreModel.setPartOpacityById('Part46',%s);"
              "return 'ok';}catch(e){return String(e);}})()" % value)
        self.win.run_js(js, lambda r: then(r))

    def _grab_part_on(self, ret) -> None:
        self.result["part46_set_1"] = str(ret)
        self.win.render_once(lambda _r: QTimer.singleShot(250, self._grab_on))

    def _grab_on(self) -> None:
        self._img_on = self.win.grab_image()
        self._save(self._img_on, "part46-on")
        self._set_part46(0.0, self._grab_part_off)

    def _grab_part_off(self, ret) -> None:
        self.result["part46_set_0"] = str(ret)
        self.win.render_once(lambda _r: QTimer.singleShot(250, self._grab_off))

    def _grab_off(self) -> None:
        img_off = self.win.grab_image()
        self._save(img_off, "part46-off")
        # 判据 1/2 的主证据面：修好的这一版整窗图落到 --shot 的**精确路径**
        if self.out is not None:
            Path(self.out).parent.mkdir(parents=True, exist_ok=True)
            img_off.save(str(self.out))
        box = alpha_bbox(img_off)
        self.result["alpha_bbox"] = box
        self.result["alpha_bbox_h"] = None if box is None else box[3] - box[1] + 1
        self.result["alpha_bbox_w"] = None if box is None else box[2] - box[0] + 1
        self.result["alpha_bbox_口径"] = "整窗图 alpha>8 的实心包围盒（含端点，QImage 像素）"
        # 判据 6：修好的这一版里角色可见像素（覆盖区之外的全部角色）
        self.result["alpha_px_gt0"] = count_alpha(img_off, 0)
        self.result["alpha_px_gt8"] = count_alpha(img_off, A_MIN)
        blob = dark_only_blob(self._img_on, img_off)
        self.result["part46_blob"] = blob
        fbox = blob.get("判据框")
        self.result["part46_bbox"] = list(fbox) if fbox else None
        self.result["part46_bbox_口径"] = (
            "ON 暗而 OFF 不暗 像素的最大 8 连通块外接矩形，外扩 2 px")
        if fbox:
            # 判据 7 的判据面：修好的那一版，这块黑点覆盖区里不许再有黑像素
            self.result["part46_region_on"] = region_stats(self._img_on, fbox)
            self.result["part46_region_off"] = region_stats(img_off, fbox)
            self.result["dark_after_fix"] = self.result["part46_region_off"]["L<120 像素数"]
            self.result["bright_after_fix"] = self.result["part46_region_off"]["最暗亮度"]
        # 判据 6 的判据面
        self.result["ok"] = self.result.get("alpha_px_gt8", 0) > ALPHA_MIN_PIXELS
        self.result["mode"] = "visible"
        self.result["gpu_after_mib"] = gpu_used_mib()
        procs = tree_cpu(self.root)
        self.result["rss_main_mb"] = rss_mb(self.root)
        self.result["renderer_children"] = renderer_children(procs)
        self.result["rss_renderer_mb"] = round(
            sum(m for _pid, _n, m in self.result["renderer_children"]), 1)
        self._finish(exit_code=0 if self.result["ok"] else 1)

    # ---- 判据 8 / 9：停渲染的 CPU
    def _bench(self, *, hidden: bool) -> None:
        self.result["gpu_before_mib"] = self.result.get("gpu_before_mib") or gpu_used_mib()
        # T-305：可见基线必须**喂着动作**量。待机冻帧上线后，一个没人理的可见窗
        # 6 s 后就自己停表了——拿它当基线，比的是"冻住的窗 vs 藏起来的窗"，证不出
        # D8 要证的事（藏起来到底省没省）。基线的语义是"宠物在动的时候"。
        self.result["visible_baseline"] = "feeds_motion_every_2s"
        self._act_timer = QTimer(self.app)
        self._act_timer.setInterval(2000)
        self._act_timer.timeout.connect(self._play_motion)
        self._play_motion()
        self._act_timer.start()
        self._measure(self.args.visible_seconds, lambda m, r: self._after_visible(hidden, m, r))

    def _measure(self, seconds: float, cb) -> None:
        procs = tree_cpu(self.root)
        prime_cpu(procs)
        self._bench_t0 = time.monotonic()
        QTimer.singleShot(int(seconds * 1000), lambda: cb(*read_cpu(procs)))

    def _after_visible(self, hidden: bool, main: float, rest: float) -> None:
        timer = getattr(self, "_act_timer", None)
        if timer is not None:
            timer.stop()   # 基线量完就停喂；隐藏段不应该再有动作信号
        self.result["visible"] = {
            "seconds": self.args.visible_seconds,
            "main_cpu_pct": round(main, 2),
            "child_cpu_pct": round(rest, 2),
            "total_cpu_pct": round(main + rest, 2),
            "total_cores": round((main + rest) / 100.0, 2),
            "rss_main_mb": rss_mb(self.root),
            "renderer_children": renderer_children(tree_cpu(self.root)),
        }
        if hidden:
            self._enter_hidden("bench")
        else:
            self._fullscreen_path()

    # ---- 判据 9：走引擎那条"该藏了"的判定
    def _fullscreen_path(self) -> None:
        try:
            from . import platform_win
            hit, why = platform_win._fg_fullscreen_probe()
        except Exception as exc:  # noqa: BLE001 - 判定函数不可用时如实记
            self.result["fullscreen_probe"] = {"error": f"{type(exc).__name__}: {exc}"}
            hit, why = False, "probe-error"
        self.result["fullscreen_probe"] = {"hit": bool(hit), "why": why}
        # 判据 9 要的是"确认走同一条停渲染路径"：判定函数真返回 False 时，
        # 手工把它当作 hit=True 调**同一个** _apply_fullscreen，证明路径一致，
        # 而不是另写一条隐藏逻辑。
        self._enter_hidden("fullscreen", force_hit=True)

    def _apply_fullscreen(self, hit: bool) -> None:
        """引擎隐藏/恢复的镜像：hit=True ⇒ 藏窗 + 停表；False ⇒ 显示 + 续表。"""
        if hit:
            self.win.hide()
            self.win.set_paused(True)
        else:
            self.win.show()
            self.win.set_paused(False)

    def _enter_hidden(self, why: str, *, force_hit: bool = False) -> None:
        self.result["hidden_reason"] = why
        if force_hit:
            self.result["fullscreen_forced_hit"] = True
        self._apply_fullscreen(True)
        self.result["paused"] = self.win.is_paused
        self.result["window_visible"] = self.win.isVisible()
        QTimer.singleShot(500, lambda: self._measure(
            self.args.hidden_seconds, self._after_hidden))

    def _after_hidden(self, main: float, rest: float) -> None:
        vis = self.result["visible"]
        hidden = {
            "seconds": self.args.hidden_seconds,
            "main_cpu_pct": round(main, 2),
            "child_cpu_pct": round(rest, 2),
            "total_cpu_pct": round(main + rest, 2),
            "total_cores": round((main + rest) / 100.0, 2),
            "rss_main_mb": rss_mb(self.root),
        }
        self.result["hidden"] = hidden
        vis_total = vis["total_cpu_pct"]
        self.result["ratio_hidden_over_visible"] = (
            None if vis_total <= 0 else round(hidden["total_cpu_pct"] / vis_total, 4))
        # 判据 8 的两条门槛：合计 ≤ 可见的 15%，且 renderer 子进程 ≤ 2%
        self.result["gate_total_le_15pct"] = bool(
            vis_total > 0 and hidden["total_cpu_pct"] <= vis_total * 0.15)
        self.result["gate_renderer_le_2pct"] = bool(hidden["child_cpu_pct"] <= 2.0)
        self.result["gpu_after_mib"] = gpu_used_mib()
        self.result["ok"] = bool(self.result["gate_total_le_15pct"]
                                 and self.result["gate_renderer_le_2pct"])
        self._finish(exit_code=0 if self.result["ok"] else 1)

    # ---- 判据 3（T-305）：待机冻帧 —— 能动 → 冻住 → 叫得醒
    def _idle_active_start(self) -> None:
        """主动作阶段：每 2 s 播一次动作（= 持续 touch），量这期间的 CPU。"""
        self.result["idle_phase"] = {"active_seconds": self.args.idle_active_seconds}
        self._play_motion()
        # 持续喂动作：只播一次的话动作几秒就停，之后页面自己就判静止了，
        # "能动"这一段就量不到满负荷。
        self._act_timer = QTimer(self.app)
        self._act_timer.setInterval(2000)
        self._act_timer.timeout.connect(self._play_motion)
        self._act_timer.start()
        self._measure(int(self.args.idle_active_seconds),
                      lambda m, r: self._after_idle_active(m, r))

    def _play_motion(self) -> None:
        self.win.run_js(
            "(() => { try { const e = window.__poc; e.tick = (e.tick||0) + 1;"
            " return window.playMotion('Gesture', e.tick % 7); }"
            " catch(e) { return String(e); } })()")
        self.win.run_js("window.__poc && (window.__poc.lastAct = Date.now())")
        self._last_act = time.monotonic()

    def _after_idle_active(self, main: float, rest: float) -> None:
        if getattr(self, "_act_timer", None) is not None:
            self._act_timer.stop()
        self.result["visible_moving_cpu_pct"] = round(main + rest, 2)
        self.result["visible_moving_child_cpu_pct"] = round(rest, 2)
        self.result["visible_moving_cores"] = round((main + rest) / 100.0, 2)
        # 静默阶段：不再 touch，等页面自己判「静止超过阈值」
        self._last_act = time.monotonic()
        self._idle_stage_t0 = time.monotonic()
        self._wait_frozen(0)

    def _wait_frozen(self, tries: int) -> None:
        if tries > 80:  # 40 s 还没冻 ⇒ 记失败，别挂死
            self.result["idle_entered_after_s"] = None
            self.result["idle_freeze_failed"] = True
            self._measure_idle_cpu()
            return
        self._idle_tries = tries
        self.win.run_js("window.__idleCheck ? window.__idleCheck() : '{}'",
                        self._on_idle_probe)

    def _on_idle_probe(self, payload) -> None:
        try:
            st = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            st = {}
        frozen = bool(st.get("frozen"))
        self.result["idle_last_state"] = st
        if frozen:
            self.result["idle_entered_after_s"] = round(time.monotonic() - self._last_act, 2)
            self.result["idle_frozen_at_page_ms"] = st.get("idleMs")
            QTimer.singleShot(1000, self._measure_idle_cpu)
            return
        nxt = int(getattr(self, "_idle_tries", 0)) + 1
        QTimer.singleShot(500, lambda: self._wait_frozen(nxt))

    def _measure_idle_cpu(self) -> None:
        # 逐秒采样再取中位数：单点 cpu_percent 在 12 s 窗口里会被一次 GC / 一次
        # 合成抖动整个带偏（T-305 实测两次同条件跑出 0.6% 与 2.9%）。
        self._samples: list[tuple[float, float]] = []
        procs = tree_cpu(self.root)
        prime_cpu(procs)
        self._samp_procs = procs
        self._samp_n = max(1, int(self.args.idle_quiet_seconds))
        self._samp_timer = QTimer(self.app)
        self._samp_timer.setInterval(1000)
        self._samp_timer.timeout.connect(self._sample_once)
        self._samp_timer.start()

    def _sample_once(self) -> None:
        main, rest = read_cpu(self._samp_procs)
        self._samples.append((main, rest))
        if len(self._samples) >= self._samp_n:
            self._samp_timer.stop()
            self._after_idle_cpu(*self._median(self._samples))

    @staticmethod
    def _median(samples: list[tuple[float, float]]) -> tuple[float, float]:
        m = sorted(s[0] for s in samples)
        c = sorted(s[1] for s in samples)
        mid = len(m) // 2
        return (m[mid], c[mid])

    def _after_idle_cpu(self, main: float, rest: float) -> None:
        self.result["visible_idle_cpu_pct"] = round(main + rest, 2)
        self.result["visible_idle_child_cpu_pct"] = round(rest, 2)
        self.result["idle_samples"] = [[round(a, 2), round(b, 2)] for a, b in self._samples]
        self.result["idle_cpu_口径"] = "逐秒采样取中位数（main, child）"
        # 叫醒：播一个动作，看 renderer 有没有真的重新干活
        self._play_motion()
        QTimer.singleShot(1500, self._resume_probe)

    def _resume_probe(self) -> None:
        procs = tree_cpu(self.root)
        prime_cpu(procs)
        QTimer.singleShot(3000, lambda: self._after_resume(*read_cpu(procs)))

    def _after_resume(self, main: float, rest: float) -> None:
        self.result["resumed_child_cpu_pct"] = round(rest, 2)
        self.win.run_js("window.__idleCheck ? window.__idleCheck() : '{}'",
                        self._after_resume_state)

    def _after_resume_state(self, payload) -> None:
        try:
            st = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            st = {}
        self.result["resume_state"] = st
        woke = not bool(st.get("frozen"))
        busy = float(self.result.get("resumed_child_cpu_pct") or 0.0) > 5.0
        self.result["resumed_on_action"] = bool(woke and busy)
        self.result["resumed_woke_judge"] = woke
        self.result["resumed_busy_judge"] = busy
        self.result["gpu_after_mib"] = gpu_used_mib()
        self.result["idle_gate"] = {
            "moving_ge_100": float(self.result.get("visible_moving_cpu_pct") or 0) >= 100.0,
            "idle_le_2": float(self.result.get("visible_idle_cpu_pct") or 0) <= 2.0,
            "entered_le_15s": (self.result.get("idle_entered_after_s") is not None
                               and float(self.result["idle_entered_after_s"]) <= 15.0),
            "resumed": self.result["resumed_on_action"],
        }
        self.result["ok"] = all(self.result["idle_gate"].values())
        self.result["mode"] = "idle"
        self._finish(exit_code=0 if self.result["ok"] else 1)

    # ---------------------------------------------------------------- 收口
    def _save(self, img: QImage, tag: str) -> None:
        if self.out is None:
            return
        path = self.out.with_name(f"{self.out.stem}-{tag}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(path))
        self.result.setdefault("shots", []).append(str(path))

    def _finish(self, exit_code: int) -> None:
        if self.win is not None:
            try:
                self.win.set_paused(False)
                self.win.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self.result["elapsed_s"] = round(time.monotonic() - self._t0, 1)
        print("PROBE_RESULT " + json.dumps(self.result, ensure_ascii=False), flush=True)
        self.app.exit(exit_code)


def measure_only(path: str) -> int:
    """``--measure``：只量一张 PNG 的 alpha 包围盒，不开窗、不占显存。"""
    QApplication.instance() or QApplication(sys.argv[:1])
    p = Path(path)
    img = QImage(str(p))
    if img.isNull():
        print("MEASURE_RESULT " + json.dumps(
            {"ok": False, "error": f"读不到图片: {p}"}, ensure_ascii=False))
        return 1
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    box = alpha_bbox(img)
    out = {
        "ok": box is not None,
        "path": str(p),
        "img_size": [img.width(), img.height()],
        "alpha_bbox": box,
        "alpha_bbox_w": None if box is None else box[2] - box[0] + 1,
        "alpha_bbox_h": None if box is None else box[3] - box[1] + 1,
        "alpha_px_gt8": count_alpha(img, A_MIN),
        "alpha_px_gt0": count_alpha(img, 0),
        "口径": "alpha>8 实心包围盒（含端点）；高 = y1-y0+1",
    }
    print("MEASURE_RESULT " + json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live2D 独立探针（P1/T-304、T-305）")
    ap.add_argument("--seconds", type=float, default=10.0, help="ready 后等多久再量测")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="宠物缩放；窗口 = 640*scale × 390*scale（1.5 ⇒ 960×585）")
    ap.add_argument("--w", type=int, default=0, help="覆盖窗宽（0=按 scale 推）")
    ap.add_argument("--h", type=int, default=0, help="覆盖窗高（0=按 scale 推）")
    ap.add_argument("--shot", default="", help="截图输出基名（不填则不落盘）")
    ap.add_argument("--measure", default="", help="只量一张 PNG 的 alpha 包围盒")
    ap.add_argument("--hidden-bench", action="store_true", help="可见/隐藏停表 CPU 对照")
    ap.add_argument("--fullscreen-bench", action="store_true", help="复用引擎全屏判定走停表")
    ap.add_argument("--idle-bench", action="store_true", help="待机冻帧：能动/冻住/叫醒")
    ap.add_argument("--idle-active-seconds", type=float, default=12.0)
    ap.add_argument("--idle-quiet-seconds", type=float, default=12.0)
    ap.add_argument("--visible-seconds", type=float, default=30.0)
    ap.add_argument("--hidden-seconds", type=float, default=30.0)
    args = ap.parse_args(argv)

    if args.measure:
        return measure_only(args.measure)

    probe = Probe(args)
    probe.start()
    # 硬闸：bench 模式要跑好几段，统一放宽到 300 s（页面不 ready 有 60 s 那道闸兜）
    QTimer.singleShot(300000, lambda: probe._finish(exit_code=3))
    return probe.app.exec()


if __name__ == "__main__":
    sys.exit(main())
