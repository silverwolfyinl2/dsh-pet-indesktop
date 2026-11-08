# -*- coding: utf-8 -*-
"""表演层（P2a）：让 LLM 的回复**操作** Live2D，而不只是换了个画法。

问题背景（T-20261108-306）：引擎此前用 **webm 的动作名**驱动 Live2D
（``待机呼吸``、``点击回应-竖手指``…），那些名字在 Live2D 模型里根本不存在，
``Live2DSprite.bind_clip`` 直接吃掉调用。于是 AI 从来没有真正操作过角色层。

本模块补的是「甲」路线（秋殇 2026-11-08 00:36 裁决「按照你的推荐来」= 甲+乙叠加）：
LLM 在回复正文里吐标签，解析器把标签摘出来变成指令，表演层再把指令落到模型上。
乙（4 档时段表 + 好感/信任的存档与基调）是 P2b，本模块只负责把数值指令**算出来**，
不落盘、不改人设。

四条硬约束，改动前先读：

1. **单一真源**。23 个表情名、8 个动作名只在本文件出现一次（``EXPRESSIONS`` /
   ``MOTIONS``），别的模块一律 ``from .performance import ...``。名字第二个副本
   出现的那一刻，就会出现「白名单说 A、执行按 B」的错配。慢通道动作的
   「素材名 → 情绪 → (表情, 动作)」映射（``_SCENE_MOOD_GROUPS`` /
   ``MOOD_PERFORMANCE``）同样只在本文件出现一次 —— 它是 Live2D 下唯一能看见
   「点名动作」的接法（``resolve_scene_action``）。
2. **解析器是纯函数**（``parse_reply``）。不碰 Qt、不碰文件、不读配置，输入到输出
   完全确定 —— 好单测、好复算、好回放。
3. **宁可说错，不可丢话**。格式错 / 名字不在白名单 / 括号不闭合 ⇒ 标签**原样留在
   文本里**，只留一条 warning。吞字的代价比说出一个怪标签大得多。
4. **webm 路径必须优雅无效**。``PET_LIVE2D=0`` 或窗口起不来时，引擎拿到的是
   ``PetSprite``（没有 ``set_expression``）：所有表演动作变成 no-op 并记 ``skipped``，
   一个异常都不许抛 —— 渲染层不该把整只宠拖没。

正则说明（判据 1 case 10 要求 10 万字 1 秒内返回）：``_TAG_RE`` 的参数字符类是
``[^<>\n]{0,40}``，**有上界、无嵌套量词**，最坏情况是线性扫描，不存在回溯爆炸。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 单一真源
#: 模型可切表情（``大肥鱼.local.model3.json`` → FileReferences.Expressions，23 条，
#: 顺序与文件一致）。名字就是 exp3 里的 ``Name``，页面按名字索引。
EXPRESSIONS: tuple[str, ...] = (
    "体型1", "体型2", "兔牙", "前倾", "加载", "单片眼镜", "圈圈眼", "尾巴开关",
    "屑屑眼", "流冷汗", "流泪", "爱心眼", "竖食指关", "竖食指开", "胸口衣服",
    "脱鞋", "脸红", "裙子切换", "钢盆", "黑框眼镜", "黑脸", "默认体型", "＞＜",
)

#: 模型可播动作（同文件 → FileReferences.Motions，2 组 8 条）。(组名, 动作名)；
#: ``File`` 形如 ``Animation/竖手指切出来动画.motion3.json``，动作名 = 文件名去扩展。
MOTIONS: tuple[tuple[str, str], ...] = (
    ("Idle", "IDLE"),
    ("Gesture", "举手切默认手"),
    ("Gesture", "狗尾草切回默认手"),
    ("Gesture", "狗尾草切换出来"),
    ("Gesture", "竖手指切出来动画"),
    ("Gesture", "竖手指切回默认"),
    ("Gesture", "脱捕"),
    ("Gesture", "默认手切举手"),
)

#: 口语别名 → 真名。LLM 会写「竖手指」，模型里没有这条动作（真名是
#: 「竖手指切出来动画」）。别名表是**唯一**的容错层：解析器仍然按用户/模型写的
#: 那个词出指令（回执要能看出「它想干什么」），由 ``resolve_motion`` 在落地时翻。
GESTURE_ALIASES: dict[str, str] = {
    "竖手指": "竖手指切出来动画",
    "竖手指切出来": "竖手指切出来动画",
    "竖手指收回": "竖手指切回默认",
    "举手": "举手切默认手",
    "举手切回": "举手切默认手",
    "狗尾草": "狗尾草切换出来",
    "狗尾草收回": "狗尾草切回默认手",
    "待机": "IDLE",
    "待机呼吸": "IDLE",
    "idle": "IDLE",
}

EXPRESSION_NAMES: frozenset[str] = frozenset(EXPRESSIONS)
MOTION_NAMES: tuple[str, ...] = tuple(name for _group, name in MOTIONS)

#: 好感/信任单次变化的上限。超出即钳制（判据 1 case 9），且**不写存档**——
#: 存档由 P2b 负责，本模块只产出指令。
STAT_LIMIT = 20

#: 数值指令的键（与标签名一一对应）。
STAT_KEYS: tuple[str, ...] = ("好感", "信任")

#: 标签正则。``{0,40}`` 是刻意设的上界：参数字符类有界 ⇒ 无回溯爆炸可能。
_TAG_RE = re.compile(r"<(?P<kind>表情|动作|好感|信任)\s*[:：]\s*(?P<arg>[^<>\n]{0,40})>")
#: 半开标签（有 ``<`` 没有 ``>``）只用来出 warning，不参与替换。
_OPEN_TAG_RE = re.compile(r"<(?:表情|动作|好感|信任)\s*[:：][^<>\n]*$")
#: 数字指令参数：可带正负号，允许全角符号。
_NUM_RE = re.compile(r"^([+\-＋－]?)\s*(\d{1,4})$")


# ---------------------------------------------------------------- 解析结果
@dataclass(frozen=True)
class ParseResult:
    """``parse_reply`` 的返回值：**净化后的文本** + **按出现顺序**的指令 + 告警。"""

    text: str
    commands: tuple[tuple, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def has_commands(self) -> bool:
        return bool(self.commands)


def _warn(warnings: list[str], msg: str) -> None:
    warnings.append(msg)
    logger.warning("表演标签：%s", msg)


def _coerce_stat(arg: str) -> int | None:
    match = _NUM_RE.match(arg.strip())
    if match is None:
        return None
    sign = -1 if match.group(1) in ("-", "－") else 1
    return sign * int(match.group(2))


def resolve_expression(name: str) -> str | None:
    """表情名 → 模型真名；不在白名单返回 ``None``（**不做模糊匹配**：表情名之间
    差异太小，猜错比不猜更难查）。"""
    name = str(name or "").strip()
    return name if name in EXPRESSION_NAMES else None


def resolve_motion(name: str) -> tuple[str, str] | None:
    """动作名/别名 → ``(组名, 真名)``。三级：真名 → 别名 → 唯一前缀。

    前缀匹配要求**唯一命中**：``竖手指`` 能命中两条真名时宁可不猜（返回 None），
    让标签原样留在文本里 —— 与「宁可说错，不可丢话」同一条纪律。
    """
    name = str(name or "").strip()
    if not name:
        return None
    for group, motion in MOTIONS:
        if motion == name:
            return (group, motion)
    alias = GESTURE_ALIASES.get(name) or GESTURE_ALIASES.get(name.lower())
    if alias:
        for group, motion in MOTIONS:
            if motion == alias:
                return (group, motion)
    hits = [(group, motion) for group, motion in MOTIONS if motion.startswith(name)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        logger.warning("表演标签：动作名「%s」有 %d 条前缀命中，不猜", name, len(hits))
    return None


# ---------------------------------------------------------------- 慢通道动作（T-20261108-309）
#: 慢通道 / Agent 联动点名的是 **webm 素材名**（`D:\AI\slowdesk\persona\action_pool.json`
#: 的 100 条中文名），Live2D 模型里没有这些动作 —— 旧路径 ``Live2DSprite.bind_clip``
#: 直接吃掉调用，屏幕上什么都不发生。这里补的是**有损但可见**的接法：
#: 素材名 → 情绪 → (表情真名, 动作真名)，让「点名动作」在 Live2D 上真的看得见。
#:
#: 数据来源（不拍脑袋）：
#: * 分组真源 = ``action_pool.json`` 的 ``emotionActions``（2026-11-08 快照，11 组 60 条）。
#:   同一素材出现在多组时按下面书写顺序**先命中者优先**。
#: * 未覆盖的 40 条素材走 ``_MOOD_KEYWORDS`` 关键词兜底，再不行落 ``neutral``。
#: * 情绪名（proud/happy/…）沿用 action_pool 的键，不另造一套词汇。
_SCENE_MOOD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("annoyed", (
        "被吓一跳", "玩游戏气急败坏", "工作状态-垂头叹气冒汗",
        "工作状态-思考冒泡", "点击回应-傲娇生气",
    )),
    ("calm", (
        "待机呼吸休闲", "悠闲哼歌", "点击回应-元气挥手",
        "女仆屈膝礼仪", "大口吃零食",
    )),
    ("determined", (
        "原地重力下蹲压缩", "原地跳跃抓碎头顶物品", "轻快摇摆舞",
        "工作状态-忙碌点按",
    )),
    ("excited", (
        "小幅度原地360度旋转展示", "工作状态-雀跃庆祝", "可爱宅舞",
        "轻快摇摆舞", "舞狮头",
    )),
    ("focus", (
        "轻快摇摆舞", "写代码", "原地专心玩魔方",
        "工作状态-忙碌点按", "工作状态-清点归档", "深度思考碎碎念",
        "工作状态-思考冒泡",
    )),
    ("happy", (
        "轻快摇摆舞", "点击回应-元气挥手", "点击回应-开心跃动",
        "点击回应-挠痒咯咯笑", "吃早餐", "晨间刷牙",
        "吃午餐", "吃白饭", "吃晚餐",
        "涮火锅", "大口吃零食", "偷吃零食被抓住",
        "吃冰淇淋融化", "吃饺子", "收红包",
        "写福字", "中秋赏月吃月饼", "装点圣诞树",
        "拆礼物",
    )),
    ("neutral", (
        "待机呼吸休闲", "照镜子",
    )),
    ("proud", (
        "工作状态-雀跃庆祝", "小幅度原地360度旋转展示",
    )),
    ("shy", (
        "点击回应-害羞惊讶", "女仆屈膝礼仪",
    )),
    ("surprise", (
        "被吓一跳", "打瞌睡被惊醒", "工作状态-原地踱步张望",
        "东张西望", "讨糖南瓜灯", "萌化小幽灵",
    )),
    ("tired", (
        "超大伸懒腰", "哈欠连天", "原地小憩沉眠",
    )),
)

#: ``action_pool.json`` 快照（60 条）**没有覆盖**的素材：按语义就近归类（2026-11-08）。
#: 单独一张表而不是往上面硬塞 —— 上面是外部快照，这里是本机判断，来源必须分得开，
#: 将来 slowdesk 侧改了动作池，哪些要跟着动一眼可见。
_EXTRA_MOOD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("annoyed", ("用鲸鱼尾巴拍打地面",)),
    ("calm", ("摇扇纳凉",)),
    ("excited", ("蓝鲸现世",)),
    ("focus", (
        "下五子棋", "碎碎念-发呆碎碎念", "碎碎念-对屏碎碎念",
        "碎碎念-擦桌碎碎念", "穿针乞巧", "轻快记录",
    )),
    ("happy", (
        "三球抛接", "动物环绕", "原地敲击桌面互动", "变鸽子",
        "吹气球", "吹笛子", "堆雪人", "小提琴演奏",
        "扑克魔术", "抽陀螺", "插茱萸赏菊", "整体换装试色",
        "荡秋千", "踢毽子", "骑木马", "鲸鱼吐泡泡特效",
    )),
    ("surprise", ("被落叶淹没",)),
)


#: 动作只用「单向前进」的几条（举手 / 竖手指 / IDLE）：成对的「切回默认手」类
#: 动作会把手势状态带回默认，连续点名时就会一上一下地抖，故不取。
MOOD_PERFORMANCE: dict[str, tuple[str | None, str | None]] = {
    "annoyed": ("屑屑眼", "竖手指切出来动画"),
    "calm": ("默认体型", None),
    "determined": ("＞＜", "竖手指切出来动画"),
    "excited": ("爱心眼", "举手切默认手"),
    "focus": ("黑框眼镜", "默认手切举手"),
    "happy": ("兔牙", "举手切默认手"),
    "neutral": ("默认体型", None),
    "proud": ("爱心眼", "举手切默认手"),
    "shy": ("脸红", "默认手切举手"),
    "surprise": ("＞＜", "举手切默认手"),
    "tired": ("黑脸", None),
}

#: 全部情绪键（供探针/回执遍历；集合与 ``MOOD_PERFORMANCE`` 严格一致）。
SCENE_MOODS: tuple[str, ...] = tuple(MOOD_PERFORMANCE)

#: 关键词兜底（**顺序敏感**：先匹配到的赢）。只收「品类」词，不收具体素材名 ——
#: 具体素材名归 ``_SCENE_MOOD_GROUPS`` 管，两处都写同一名字才是双份真源。
_MOOD_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("吓", "surprise"), ("惊", "surprise"), ("拖拽", "surprise"),
    ("生气", "annoyed"), ("气急", "annoyed"), ("烦", "annoyed"),
    ("睡", "tired"), ("困", "tired"), ("哈欠", "tired"), ("懒", "tired"),
    ("舞", "excited"), ("庆祝", "excited"), ("旋转", "excited"),
    ("害羞", "shy"), ("礼仪", "shy"),
    ("写", "focus"), ("工作", "focus"), ("思考", "focus"), ("专", "focus"),
    ("吃", "happy"), ("喝", "happy"), ("零食", "happy"), ("餐", "happy"),
    ("玩", "happy"), ("放", "happy"), ("花", "happy"), ("猫", "happy"),
    ("奔跑", "calm"), ("踏步", "calm"), ("走路", "calm"), ("漂浮", "calm"),
)


def scene_mood(name: str) -> str | None:
    """慢通道素材名 → 情绪键（不认识返回 ``None``，不硬猜）。

    三级：``_SCENE_MOOD_GROUPS`` 精确名 → ``_MOOD_KEYWORDS`` 品类词 → ``None``。
    """
    text = str(name or "").strip()
    if not text:
        return None
    for mood, names in _SCENE_MOOD_GROUPS:
        if text in names:
            return mood
    for mood, names in _EXTRA_MOOD_GROUPS:
        if text in names:
            return mood
    for keyword, mood in _MOOD_KEYWORDS:
        if keyword in text:
            return mood
    return None


def resolve_scene_action(name: str) -> tuple[str | None, str | None] | None:
    """慢通道点名动作 → ``(表情真名, 动作真名)``；两层都可能为 ``None``。

    优先级（都不与表演标签的语义冲突）：

    1. 名字本身是 Live2D 动作真名/别名（``竖手指``/``举手``…）⇒ 直接播那个动作，
       表情不换 —— 慢通道可以**精确**点 Live2D 动作；
    2. 名字是 webm 素材名/关键词 ⇒ 按情绪映射；
    3. 都不认识 ⇒ ``None``（调用方记 no-op，**不静默**）。

    纯函数，无 Qt、无文件、无配置。
    """
    text = str(name or "").strip()
    if not text:
        return None
    exact = resolve_motion(text)
    if exact is not None:
        return (None, exact[1])
    mood = scene_mood(text)
    if mood is None:
        return None
    plan = MOOD_PERFORMANCE.get(mood)
    return plan


def parse_reply(text: str) -> ParseResult:
    """从一条回复里切出表演指令。**纯函数**：同样输入永远同样输出。

    返回的 ``text`` 已经去掉**全部合法**标签（多个按出现顺序、连续标签之间不插空格
    —— 判据 5 要求「一个汉字不少」，插空格就是改字）；不合法的标签原样保留。

    指令元组形态：

    * ``("expr", "爱心眼")``      —— 表情，名字是**模型真名**
    * ``("gesture", "竖手指")``   —— 动作，名字是**模型/用户写的那个词**（可能是别名），
      落地时才由 ``resolve_motion`` 翻成真名 —— 这样回执能看出「它想做什么」
    * ``("stat", "好感", 2)``     —— 数值指令，已钳制到 ±``STAT_LIMIT``
    """
    raw = "" if text is None else str(text)
    warnings: list[str] = []
    commands: list[tuple] = []
    out: list[str] = []
    cursor = 0

    for match in _TAG_RE.finditer(raw):
        out.append(raw[cursor:match.start()])
        cursor = match.end()
        kind = match.group("kind")
        arg = match.group("arg")
        token = match.group(0)

        if kind == "表情":
            name = resolve_expression(arg)
            if name is None:
                out.append(token)
                _warn(warnings, f"未知表情「{arg.strip()}」，标签原样保留")
                continue
            commands.append(("expr", name))

        elif kind == "动作":
            if resolve_motion(arg) is None:
                out.append(token)
                _warn(warnings, f"未知动作「{arg.strip()}」，标签原样保留")
                continue
            commands.append(("gesture", arg.strip()))

        else:  # 好感 / 信任
            if not arg.strip():
                out.append(token)
                _warn(warnings, f"「{kind}」缺少数值，标签原样保留")
                continue
            value = _coerce_stat(arg)
            if value is None:
                out.append(token)
                _warn(warnings, f"「{kind}:{arg.strip()}」不是整数，标签原样保留")
                continue
            if abs(value) > STAT_LIMIT:
                clamped = max(-STAT_LIMIT, min(STAT_LIMIT, value))
                _warn(warnings, f"「{kind}」{value:+d} 超范围，钳制为 {clamped:+d}")
                value = clamped
            commands.append(("stat", kind, value))

    out.append(raw[cursor:])
    cleaned = "".join(out)

    # 半开标签：只在**没有**被上面合法匹配掉时才告警，且不替换任何字符。
    if "<" in cleaned and _OPEN_TAG_RE.search(cleaned):
        _warn(warnings, "检测到未闭合的表演标签，文本原样保留")

    return ParseResult(text=cleaned, commands=tuple(commands), warnings=tuple(warnings))


# ---------------------------------------------------------------- 白名单注入
#: 注入进 system prompt 的段首标记（探针与回执按它定位那一段）。
PROMPT_MARKER = "【表演标签】"


def prompt_block() -> str:
    """给 LLM 看的白名单段。**不注入它，模型就会瞎编名字。**

    形态选择：一类一列、一名一行。判据 7 要求「拼好的那一段不少于 20 行」，
    而且逐个列出比逗号串更容易让模型照着抄 —— 逗号串里抄错一个字的概率高得多。
    """
    lines = [
        PROMPT_MARKER,
        "你的回答正文里可以夹带尖括号表演标签，让身体和表情配合台词。",
        "标签会被系统摘掉：用户看不到、也不会被念出来，所以你不用回避、也不用解释。",
        "写法：<表情:名字> <动作:名字> <好感:+N> <信任:+N>",
        "克制使用：一轮最多两个标签，只在真的有情绪或动作时加，不要每句都挂。",
        "名字必须从下面两张表里**逐字**照抄，写错的名字标签会原样留在对话里、很难看。",
        "",
        f"表情（{len(EXPRESSIONS)} 个，只能用这些）：",
    ]
    lines += [f"{index}. {name}" for index, name in enumerate(EXPRESSIONS, 1)]
    lines += ["", f"动作（{len(MOTIONS)} 个，只能用这些）："]
    lines += [f"{index}. {name}" for index, name in enumerate(MOTION_NAMES, 1)]
    lines += [
        "",
        f"好感 / 信任是整数，单次范围 -{STAT_LIMIT}..+{STAT_LIMIT}，例如 <好感:+2> <信任:-1>。",
        "这两个只在对话里真的让人觉得关系变好或变差时才写，平时不写。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 口型节奏
#: 每字占多少毫秒（说话节奏）。取值依据（实测 vs 仓库既有两档）：
#: ``pet/dsh_scene_bridge.py:44`` 是「语音念完气泡才消失」的口径 = 220ms/字；
#: ``pet/divination/service.py:61`` 是"看一眼"的显示口径 = 90ms/字。口型跟的是
#: **念**，故取 220 与卡里建议的 6 字/秒（≈167ms）之间 —— 180ms/字 ≈ 5.6 字/秒。
MOUTH_MS_PER_CHAR = 180
MOUTH_MIN_MS = 600
MOUTH_MAX_MS = 20000
#: 口型节拍（毫秒）。60ms ≈ 16.7 Hz，肉眼与 30fps 的动画帧率对齐不会糊。
MOUTH_TICK_MS = 60
#: 低于这个值算「闭嘴」（判据 4 的开合判定阈值）。
MOUTH_OPEN_THRESHOLD = 0.05


def mouth_duration_ms(text: str, *, audio_duration_s: float | None = None) -> int:
    """台词时长：有音频用音频真长，没有就按字数估（夹在 [0.6s, 20s]）。"""
    if audio_duration_s is not None:
        try:
            value = float(audio_duration_s) * 1000.0
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return int(max(MOUTH_MIN_MS, min(MOUTH_MAX_MS, value)))
    count = len(str(text or "").strip())
    return int(max(MOUTH_MIN_MS, min(MOUTH_MAX_MS, MOUTH_MS_PER_CHAR * max(1, count))))


def mouth_envelope(index: int, total: int) -> float:
    """第 ``index`` 帧的张口度（0..1）。确定性函数：同样输入同样输出，好复算。

    形状 = 两条不同频率的正弦叠加 + 音节相位，模拟「字与字之间会合一下嘴」；
    不用随机数，否则回执里的帧数无法复算。
    """
    if total <= 0:
        return 0.0
    phase = index / float(total)
    wave = 0.55 + 0.45 * math.sin(index * 0.9)
    syllable = 0.5 + 0.5 * math.sin(phase * math.pi * total * 0.12)
    return max(0.0, min(1.0, 0.12 + 0.88 * wave * syllable))


class MouthDriver:
    """把一句台词变成一串 ``set_mouth(v)`` 调用（说完必闭嘴）。

    依赖全部注入，方便测试用假时钟：``set_mouth(value)`` 与
    ``schedule(ms, fn)``（默认走 ``QTimer.singleShot``）。不 import Qt。
    """

    def __init__(
        self,
        set_mouth: Callable[[float], object],
        *,
        schedule: Callable[[int, Callable[[], None]], None] | None = None,
        tick_ms: int = MOUTH_TICK_MS,
        ms_per_char: int = MOUTH_MS_PER_CHAR,
    ) -> None:
        self._set_mouth = set_mouth
        self._schedule_impl = schedule or self._qt_schedule
        self._tick_ms = max(10, int(tick_ms))
        self._ms_per_char = max(10, int(ms_per_char))
        self._generation = 0

    @property
    def tick_ms(self) -> int:
        return self._tick_ms

    def duration_ms(self, text: str, *, audio_duration_s: float | None = None) -> int:
        if audio_duration_s is not None:
            return mouth_duration_ms(text, audio_duration_s=audio_duration_s)
        count = len(str(text or "").strip())
        return int(max(MOUTH_MIN_MS, min(MOUTH_MAX_MS, self._ms_per_char * max(1, count))))

    def speak(
        self,
        text: str,
        *,
        audio_duration_s: float | None = None,
        on_done: Callable[[dict], None] | None = None,
    ) -> dict:
        """开始动嘴。返回的统计字典会在结束时**就地更新**（同一对象）。"""
        self._generation += 1
        generation = self._generation
        duration = self.duration_ms(text, audio_duration_s=audio_duration_s)
        total_frames = max(1, int(round(duration / float(self._tick_ms))))
        stats: dict = {
            "duration_ms": duration,
            "planned_frames": total_frames,
            "mouth_opened_frames": 0,
            "mouth_peak": 0.0,
            "mouth_valley": 1.0,
            "mouth_closed_at_end": False,
            "driven_by": "audio" if audio_duration_s else "text_length",
            "chars": len(str(text or "").strip()),
        }
        if audio_duration_s:
            stats["audio_duration_s"] = round(float(audio_duration_s), 3)

        def _tick(index: int) -> None:
            if generation != self._generation:
                return  # 新一轮已经把这一轮作废
            if index >= total_frames:
                self._emit(0.0)
                stats["mouth_closed_at_end"] = True
                stats["mouth_valley"] = min(stats["mouth_valley"], 0.0)
                if on_done is not None:
                    on_done(stats)
                return
            value = mouth_envelope(index, total_frames)
            self._emit(value)
            if value > MOUTH_OPEN_THRESHOLD:
                stats["mouth_opened_frames"] += 1
            stats["mouth_peak"] = max(stats["mouth_peak"], value)
            stats["mouth_valley"] = min(stats["mouth_valley"], value)
            self._schedule_impl(self._tick_ms, lambda: _tick(index + 1))

        _tick(0)
        return stats

    def shut(self) -> None:
        """立刻闭嘴并作废在飞的节拍（打断/新回复覆盖）。"""
        self._generation += 1
        self._emit(0.0)

    def _emit(self, value: float) -> None:
        try:
            self._set_mouth(max(0.0, min(1.0, float(value))))
        except Exception:  # noqa: BLE001 - 口型失败绝不许打断说话
            logger.debug("口型帧投递失败", exc_info=True)

    @staticmethod
    def _qt_schedule(ms: int, fn: Callable[[], None]) -> None:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(ms), fn)


# ---------------------------------------------------------------- 表演层 facade
#: 表演层在 webm 路径（无 Live2D）下的统一返回词。
SKIP_NO_LIVE2D = "skipped:no_live2d"
SKIP_UNKNOWN = "skipped:unknown"


class Performer:
    """``set_expression`` / ``play_gesture`` / ``set_mouth`` 三层落到一个 sprite 上。

    ``sprite_getter`` 是**可调用**而不是对象：主宠换角色/重开窗会换 sprite 实例，
    拿住旧引用就是对着已销毁的 Qt 对象说话（PySide6 会抛 RuntimeError）。
    """

    def __init__(
        self,
        sprite_getter: Callable[[], object | None],
        *,
        schedule: Callable[[int, Callable[[], None]], None] | None = None,
    ) -> None:
        self._sprite_getter = sprite_getter
        self._schedule = schedule
        self._mouth: MouthDriver | None = None
        self.last_expression: str | None = None
        self.last_gesture: str | None = None

    # ------------------------------------------------------------ 内部
    def _sprite(self):
        try:
            return self._sprite_getter()
        except Exception:  # noqa: BLE001 - 取不到就当没有
            logger.debug("取 sprite 失败", exc_info=True)
            return None

    @property
    def mouth(self) -> MouthDriver:
        if self._mouth is None:
            self._mouth = MouthDriver(self.set_mouth, schedule=self._schedule)
        return self._mouth

    def supports_live2d(self) -> bool:
        return callable(getattr(self._sprite(), "set_expression", None))

    # ------------------------------------------------------------ 三个动作
    def set_expression(self, name: str) -> str:
        """切表情。返回 ``applied:<真名>`` / ``skipped:no_live2d`` / ``skipped:unknown``。"""
        real = resolve_expression(name)
        if real is None:
            logger.warning("表演层：未知表情「%s」", name)
            return SKIP_UNKNOWN
        sprite = self._sprite()
        setter = getattr(sprite, "set_expression", None)
        if not callable(setter):
            return SKIP_NO_LIVE2D
        try:
            ok = bool(setter(real))
        except Exception:  # noqa: BLE001
            logger.exception("表演层：切表情失败「%s」", real)
            return SKIP_NO_LIVE2D
        if ok:
            self.last_expression = real
            return f"applied:{real}"
        return SKIP_NO_LIVE2D

    def play_gesture(self, name: str) -> str:
        """播动作。返回 ``played:<真名>`` / ``skipped:no_live2d`` / ``skipped:unknown``。"""
        resolved = resolve_motion(name)
        if resolved is None:
            logger.warning("表演层：未知动作「%s」", name)
            return SKIP_UNKNOWN
        _group, real = resolved
        sprite = self._sprite()
        player = getattr(sprite, "play_gesture", None)
        if not callable(player):
            return SKIP_NO_LIVE2D
        try:
            ok = bool(player(real))
        except Exception:  # noqa: BLE001
            logger.exception("表演层：播动作失败「%s」", real)
            return SKIP_NO_LIVE2D
        if ok:
            self.last_gesture = real
            return f"played:{real}"
        return SKIP_NO_LIVE2D

    def set_mouth(self, value: float) -> str:
        """口型（0..1）。无 Live2D 时静默跳过 —— 说话链路上不能因为没窗口就崩。"""
        sprite = self._sprite()
        setter = getattr(sprite, "set_mouth", None)
        if not callable(setter):
            return SKIP_NO_LIVE2D
        try:
            setter(value)
        except Exception:  # noqa: BLE001
            logger.debug("表演层：写口型失败", exc_info=True)
            return SKIP_NO_LIVE2D
        return "applied"

    # ------------------------------------------------------------ 批量
    def perform(self, commands: Iterable[tuple]) -> list[str]:
        """按出现顺序执行一串指令，返回每条的落地结果（顺序与指令一致）。"""
        results: list[str] = []
        for command in commands or ():
            if not command:
                continue
            kind = command[0]
            if kind == "expr":
                results.append(self.set_expression(command[1]))
            elif kind == "gesture":
                results.append(self.play_gesture(command[1]))
            elif kind == "stat":
                # P2b 才存档。这里只记录，不落盘（判据 1 case 9 的「不许写进存档」）。
                results.append(f"stat:{command[1]}{command[2]:+d}")
            else:
                results.append(SKIP_UNKNOWN)
        return results

    def speak(self, text: str, *, audio_duration_s: float | None = None,
              on_done: Callable[[dict], None] | None = None) -> dict:
        """边说话边动嘴。无 Live2D 时 ``driven_by`` 仍是实际口径，帧数恒 0。"""
        if not self.supports_live2d():
            duration = mouth_duration_ms(text, audio_duration_s=audio_duration_s)
            stats = {
                "duration_ms": duration,
                "planned_frames": 0,
                "mouth_opened_frames": 0,
                "mouth_peak": 0.0,
                "mouth_valley": 0.0,
                "mouth_closed_at_end": True,
                "driven_by": "audio" if audio_duration_s else "text_length",
                "chars": len(str(text or "").strip()),
                "skipped": SKIP_NO_LIVE2D,
            }
            if on_done is not None:
                on_done(stats)
            return stats
        return self.mouth.speak(text, audio_duration_s=audio_duration_s, on_done=on_done)


# ---------------------------------------------------------------- 进程级投递
#: 解析与执行不在同一个对象里（回复原文在 ``ChatService``，sprite 在 ``AppShell``），
#: 所以中间放一个**单槽**信箱：GUI 单线程、写完立刻取，不需要锁，也不许累积。
#: 超过 ``_STASH_TTL_S`` 还没人取的指令视为过期丢弃 —— 宁可不动，不要隔一轮才动脸。
_STASH_TTL_S = 5.0
_stash: dict = {"commands": (), "ts": 0.0}


def stash_commands(commands: Sequence[tuple]) -> None:
    import time as _time

    _stash["commands"] = tuple(commands or ())
    _stash["ts"] = _time.monotonic()


def take_commands() -> tuple:
    import time as _time

    commands = _stash["commands"]
    stamp = _stash["ts"]
    _stash["commands"] = ()
    _stash["ts"] = 0.0
    if not commands:
        return ()
    if _time.monotonic() - stamp > _STASH_TTL_S:
        logger.info("表演层：指令已过期（%.1fs），丢弃", _time.monotonic() - stamp)
        return ()
    return commands


def performer_for(shell) -> Performer:
    """给一个 ``AppShell``/``OverlayShell`` 造表演层（主宠 sprite）。"""
    return Performer(lambda: getattr(shell, "sprite", None))


__all__ = [
    "EXPRESSIONS", "MOTIONS", "MOTION_NAMES", "GESTURE_ALIASES", "STAT_LIMIT",
    "MOOD_PERFORMANCE", "SCENE_MOODS", "scene_mood", "resolve_scene_action",
    "ParseResult", "parse_reply", "resolve_expression", "resolve_motion",
    "prompt_block", "PROMPT_MARKER", "MouthDriver", "Performer", "performer_for",
    "mouth_duration_ms", "mouth_envelope", "stash_commands", "take_commands",
    "SKIP_NO_LIVE2D", "SKIP_UNKNOWN",
]
