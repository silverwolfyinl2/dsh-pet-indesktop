"""本机扩展菜单注册中心 —— **唯一侵入点**（2026-10-27）。

## 为什么单独一个文件

这些条目原先散在 `registry.py` 的 `ACTION_LABELS` / `ACTION_ICONS` /
`MenuActionRegistry._specs` 三处，一共 11 行、8 个 id。上游每次改 registry
的内部结构（v4.4.0 就改了 +126/−13），这些手工条目都要跟着挪一遍——
这是升级冲突的来源之一。

现在 `registry.py` **末尾只调用一次** `install_local_menus()`，加菜单改本文件即可，
不再碰 registry 的三张表。

## 上游兼容性

只要 registry 还导出这四个名字，本文件就工作：

    ACTION_LABELS / ACTION_ICONS / MENU_ACTIONS._specs / MenuActionSpec

缺任何一个时 `install_local_menus` 会抛异常，registry 侧捕获后记 warning、
**不阻断桌宠启动**——菜单少一项总比起不来强。

## 加一个新菜单

在 `TOP_ENTRIES` 加一行 `(id, 文案, 图标, add_xxx_menu)`；
若该菜单内部还有子项（靠 id 查文案/图标），在 `SUB_ENTRIES` 补上。
"""
from __future__ import annotations

from .ext_market import add_ext_market_menu
from .game_assistant import add_game_assistant_menu

# (id, 文案, 图标, builder)
TOP_ENTRIES = (
    ("game_assistant", "游戏助理", "automation", add_game_assistant_menu),
    ("ext_market", "扩展市场", "web", add_ext_market_menu),
)

# 子项：由对应菜单内部渲染，这里只提供文案与图标（文案为空则只用图标）
SUB_ENTRIES = (
    ("ga_ai_ops", "AI 操作", "interaction"),
    ("ga_ai_advice", "AI 建议", "chat"),
    ("ga_ai_laptop", "AI 操作笔记本", "application"),
    ("ga_ai_record", "AI 记录", "screen"),
    ("ga_voice", "语音播报", "chat"),
    ("learn_mouse", "", "screen"),          # 上游无此文案，仅图标
)


def install_local_menus(labels: dict, icons: dict, specs: dict, spec_cls) -> int:
    """把本机菜单写进 registry 的三张表，返回写入的条目数。"""
    written = 0
    for aid, label, icon, builder in TOP_ENTRIES:
        if label:
            labels[aid] = label
        if icon:
            icons[aid] = icon
        specs[aid] = spec_cls(builder)
        written += 1
    for aid, label, icon in SUB_ENTRIES:
        if label:
            labels[aid] = label
        if icon:
            icons[aid] = icon
        written += 1
    return written
