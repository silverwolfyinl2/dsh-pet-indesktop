# PR 报告：Live2D 角色层 + AI 表演层（P1 / P2a 与慢通道动作接入）

> **基线**：`93c9adb`（本地分支 `local/game-assistant` HEAD；所在分支领先 `origin/local/game-assistant` 1 个提交）
> **分支**：`local/game-assistant`　**日期**：2026-11-08
> **范围**：17 个代码/测试文件（实现 11 改 + 3 新；测试 2 改 + 1 新），另加文档 2 个（本报告 + `docs/INDEX.md` 登记）。
> 已跟踪代码/测试部分 `git diff --numstat` = **+353 / −46**；
> 新文件 2,726 行（`performance.py` 733 / `live2d_sprite.py` 763 / `live2d_probe.py` 744 / 测试 486；
> `live2d_sprite.py` 与测试为 2026-11-08 20:0x 待机冻帧修复后的最终行数，修复前 757 / 417）。
> 另有**仓库外**运行页 1 个（`refs/live2d/web/index.html`，+258 / −11），见 §七.3。
> **关联**：设计定稿 `game-copilot/docs/桌宠-Live2D与陪伴模式-设计定稿-20261107.md`（D1/D2/D8）；
> 回执 `R-20261108-305/306/307/308-workbuddy`（`D:\AI\AIWORK\共享记忆库\receipts\`）；
> 上游口径见 [`PR-REPORT-v4.4.0-CHAT-MEMORY-ROLEPLAY-2026-10-22.md`](PR-REPORT-v4.4.0-CHAT-MEMORY-ROLEPLAY-2026-10-22.md)（`_finished` 收尾链的来源）。

## 一、核心特性

角色层从 **webm 精灵**扩出 **Live2D 独立窗**（只换主宠，小肥鱼仍走 webm），并第一次让「AI 的回复 / 慢通道点名的动作」
真正落到模型上；同时把「投递成功」和「真的播了」在日志和返回值里拆开，不再自欺。

| # | 能力 | 说明 |
|---|---|---|
| 1 | Live2D 角色层（T-304） | 独立无边框透明置顶窗跑 Live2D（`refs/live2d/web/index.html`），喂给引擎的是**代理 sprite**（协议全满足、自己不画，内部拿 `PetSprite` 当几何内核 ⇒ 位置/钳制/碰撞与 webm 一 px 不差）。整窗鼠标穿透，命中由 `alpha_at` 遮罩给出。 |
| 2 | 尺寸联动 / 待机冻帧 / 显存上限（T-305） | 窗尺寸 = `640*scale × 390*scale`（与 `window.py` 同口径）；待机 6 s 由页面自判冻帧；`MAX_WINDOWS=1`，**只主宠（slot 0）走 Live2D**；`live2d_sprite` 缺省开、显式 `false` 关、缺键不写盘。 |
| 3 | AI 表演层 P2a（T-306） | 回复里可吐 `<表情:…><动作:…><好感:+N>`：`ChatService._finished` 剥标签 → 单槽信箱 → `AppShell._perform_reply` 落到主宠（表情+动作+口型跟台词）。白名单单一真源（23 表情 / 8 动作）；解析是纯函数，格式错原样保留（宁可说错，不可丢话）。 |
| 4 | 1.5× 尺寸档（T-307） | `SCALE_STEPS` 加 `1.5` → 窗 960×585、逻辑 960×540（960 px 档可勾选）。 |
| 5 | 表情管线修复（T-308，运行页） | 旧路径「表情报成功但参数读回不变」：运行页接管表情应用（开机预读 23 个 `.exp3` → `__exprBank`，撤-贴 `coreModel` 公开方法，`core.__t308Hooked` 幂等）。实测 **23/23** 表情参数读回 = 画面值。 |
| 6 | 慢通道点名动作接 Live2D（T-309） | `OverlayShell.request_link_anim` 先走表演层：素材名/关键词 → 情绪 → (表情, 动作)。「舞狮头」「玩游戏气急败坏」在 Live2D 上第一次真的改表情+播动作；映射不出来 = 明确 no-op，**不进 webm 链**。 |
| 7 | 日志三态（同 T-309） | `[SCENE]` 从 `已播=True/False` 拆成 `played= / sent= / via=`：`pool`（池内真播）/ `live2d-sent`（已送运行页）/ `live2d-miss`（明确空操作）/ `upstream-queued`（只投递）。`show_bubble` 同理由恒 `None` 改成如实 `bool`。 |
| 8 | 两处搭车本机补丁 | `PET_PACK_DIR` 进角色包发现链（否则「设置页按它安装、切角色只扫 cwd」= 只装不认）；灵动岛上下停靠细条从 669×16 不透明底色改成中央 96×2 短标（不再拦顶部点击）。 |

**红线 / 不变量**：

- **webm 路径零退化**：无 Live2D（关开关 / 起不来 / 子宠）时拿到 `PetSprite`，行为与改动前一致；`pet_sprite.py` /
  `overlay_window.py` / `sprite_behavior.py` / `window_alerts.py` / `vision.py` 五个文件**零改动**。
- **对话不许被表演层打断**：三处入口（`_finished` 剥标签、`_perform_reply` 执行、口型）全部 `try` 包住；解析异常原样返回文本。
- **记忆注入契约不变**：无 `memory_payload` 时 system 仍可逐字节复算——只是钉的对象从「改动前」换成「人设 + 时间 + 白名单」。
- **渲染代价必须有停表杠杆**：不可见 / 全屏 → `__pauseRender`；待机 6 s → 冻帧；窗口数硬上限 1。
  冻帧这条 2026-11-08 20:0x 才真正成立：webm 播控（换绑/续圈）不再重置页面待机计时（见 §七.9）。

## 二、修改文件说明

### 实现

| 文件 | 增 / 删 | 改动意图 |
|---|---|---|
| `pet/overlay_shell.py` | +139 / −8 | ① `_sprite_factory` 默认实现改 `_make_default_sprite`：Live2D 开（`live2d_sprite` 缺省 True 或 `PET_LIVE2D`）就把主宠换成 **`Live2DSprite` 代理**，`acquire(scale=…)` 按尺寸建窗；slot≠0 的分流回 `PetSprite`；起不来/无可用窗一律退回 webm（渲染层不许把整只宠拖没）。② `request_link_anim` 改三态返回：Live2D 目标先 `play_scene_action`，True=已投递 / False=映射不出来（不进 webm 链） / None=webm 老路。③ `_show_all_hosts` / `_hide_all_hosts` 对主宠 + 全部子宠逐个 `set_render_enabled`（D8 不可见停表；漏 `self.sprite` 就等于主宠那张窗永不停表）。④ 跨屏换宿主、建宠两处 `bind_overlay`，把独立窗按全局原点重挂。⑤ `PET_LIVE2D` 语义收进纯函数 `live2d_enabled_by_env()`：未设/空串 → 交给配置，`0` → **硬关**，其它非空 → 开（修 §七.7 假开关）。 |
| `pet/app.py` | +32 / −1 | 新增 `_perform_reply(text)`，并挂在 `_on_global_chat_finished` 的**任何早退之前**：`take_commands()` → `performer_for(shell).perform()`；Live2D 且宠可见时再 `speak(text)` 出口型。位置理由：全进程只有这里同时拿得到「完整回复」和「能摸到 sprite」，且在 GUI 线程。 |
| `pet/performance.py` | 新增 +733 | 表演层**单一真源**：23 表情 / 8 动作白名单；素材名/关键词 → 情绪 → (表情, 动作) 映射（`_SCENE_MOOD_GROUPS` / `MOOD_PERFORMANCE`）；`parse_reply` 纯函数（无 Qt/文件/配置，正则参数字符类有上界无嵌套量词 ⇒ 最坏线性）；`prompt_block()` 生成注入段；`MouthDriver` 口型驱动（180 ms/字、60 ms tick、只在说话期间 ≤20 s）。 |
| `pet/live2d_sprite.py` | 新增 +763 | Live2D 角色层三件：`Live2DWindow`（Frameless + 置顶 + Tool + 不接受焦点 + 整窗 `WA_TransparentForMouseEvents` + 透明背景）、`Live2DServer`（**本地 127.0.0.1** 静态服务，绕 `file://` 下 fetch 模型 json 的 CORS）、`Live2DSprite` 代理（`paint` 空操作，几何全用内部 `PetSprite`）、`Live2DRenderer` 单例（`MAX_WINDOWS=1`、按 scale 建窗、ready 轮询 300 ms/上限 20 s、待机 6 s 冻帧心跳、`touch()` 1 s 节流、停表 `__pauseRender`）。**2026-11-08 20:0x 补**：`bind_clip`/`restart_clip` 是 webm-only no-op，不再 `touch()` 唤醒页面（唤醒只留 `_run_page` 与 `set_geometry_global`），冻帧才能在部署态成立。 |
| `pet/dsh_scene_bridge.py` | +26 / −5 | `[SCENE]` 日志从二态拆成 `played / sent / via` 三字段（via ∈ pool / live2d-sent / live2d-miss / upstream-queued / upstream-error）。旧代码在回落路径一律 `ok=True`，屏幕没动日志却说播了（`anim=?` 就是证据）。Live2D 映射不出来时明确 `ok=False`。 |
| `pet/multi_window_shared.py` | +26 / −5 | ① `show_bubble` 由恒 `None` 改如实 `bool`（没有可见窗也记 `[BUBBLE] shown` = 10-27「日志说显示了、屏幕上没有」事故根因）。② `request_link_anim` 返回**聚合三态**：任一窗 True ⇒ True；全部窗 False ⇒ False；其余 ⇒ None，供桥层如实写日志。 |
| `pet/catalog.py` | +21 / −2 | ① `SCALE_STEPS` 加 `1.5`（→ 960×585）。② `external_character_dirs()` 把 `PET_PACK_DIR` 排进发现链最前，并按 `normcase(abspath)` 去重保序（重复根会让 `dirs.count(base)==1` 既有断言失效）。 |
| `pet/config.py` | +10 / −0 | 新增 `DEFAULT_LIVE2D_SPRITE = True`；`Config.get("live2d_sprite")` 的缺省按 True。**只补缺省、不写回磁盘**——不为一个默认值去改写用户 `config.json`。 |
| `pet/chat/service.py` | +24 / −0 | `_finished` 在 `finished.emit` **之前**调 `_strip_performance(text)`：摘标签 + 指令投单槽信箱。理由：这是全进程唯一「完整回复原文还在、谁都还没看见」的瞬间，三个对话入口（完整聊天窗 / 快速对话 / 灵动岛）一次覆盖；晚于 `emit` 标签已进会话存档。 |
| `pet/chat/prompt.py` | +23 / −0 | system prompt 追加表演白名单段（`performance_prompt_block()`，`performance` 不可用时静默返回 `""`）。位置刻意在 memory_text **之前**：不破 `test_build_messages_appends_memory_section` 钉死的「system 以记忆段结尾」，也符合「能力说明先于本轮事实」。 |
| `pet/island/dynamic_island.py` | +29 / −14 | 上下停靠不再画整条 669×16 主题底色（深壁纸上是不透明黑边，且 alpha>0 像素拦下顶部点击）：改画中央 `_STRIP_STUB_W=96` 宽、2 px 高强调色短标，其余全透明；窗口几何 / hover 滑出 / 单击展开 / 碰撞结算全走原路。 |
| `pet/agent_link/__init__.py` | +1 / −2 | 兼容导出清理：删不再使用的 `_external_bubble_enabled` 导入；`external_bubble_policy` 标 `noqa: F401`（tests 与旧集成仍从这个包取名）。 |
| `pet/context_menus/game_assistant.py` | +0 / −1 | 删一行未用的 `import sys as _sys`（同批 cleanup，行为零变化）。 |
| `pet/live2d_probe.py` | 新增 +744 | 独立探针：只开 Live2D 窗、不启完整桌宠、不碰在跑的 PID。量尺寸 / CPU / 显存 / 待机冻帧 / 隐藏停表 / 全屏停表；`QWebEngineView.grab()` 取**带 alpha** 窗口像素（WebGL `toDataURL` 在 `preserveDrawingBuffer=false` 下全透明，只能走 grab）做遮罩与表情 diff。 |
| **仓库外** `refs/live2d/web/index.html` | +258 / −11 | 运行页：藏 Part46；加 `__pauseRender`（= `app.ticker.stop()`）；表情接管（`__exprBank` 23 项、`__t308Hooked` 幂等）；动作/表情账本 API（供探针取"真播"证据）。**不在本仓 git**（见 §七.3）。 |

### 测试

| 文件 | 增 / 删 | 覆盖 |
|---|---|---|
| `tests/test_performance_scene_actions.py` | 新增 +486 | 29 例，五块：① 纯函数映射（情绪快照/关键词回落/未知 None；100 个慢通道动作名逐个可解析且有可见层）；② sprite 投递（假窗口断言送出的 JS 表情+动作，未 ready → False，未知动作发 0 条 JS）；③ 壳层分流（Live2D 目标不进 webm `switch_clip`，webm 目标仍走老路）；④ `MultiWindowProxy` 三态返回值与聚合规则（真 proxy + 壳替身）；⑤ 角色层开关语义（`PET_LIVE2D` 三态纯函数 + `_make_default_sprite` 在 `0` 时**短路不碰** Live2D 层，见 §七.7）；⑥ 待机冻帧唤醒边界（换绑/续圈一律不唤醒、表演层与显式 `touch()` 仍必须唤醒；2026-11-08 20:0x 先红 `assert 2 == 0` 后绿）。 |
| `tests/test_memory_recall.py` | +10 / −2 | `test_build_messages_without_payload_is_byte_identical` 从「与改动前逐字节一致」改成「与『人设+时间+白名单』逐字节一致」：段位与顺序钉死，白名单正文从 `performance.prompt_block()` 取（不复制副本）。 |
| `tests/test_island_bevel_fade.py` | +12 / −6 | `test_docked_strip_has_no_bevel` 同步 T-20261107-303 新形态：旧断言在 `(35, 8)` 取整条底板（新设计改为中央 2px 短标、其余全透明 ⇒ 全量里红）；现改为「中央短标两行同色（无高光/阴影）+ 短标外 alpha=0」，自由胶囊态软陶笔对照保留。全量暴露后同步（见 §六 F 组）。 |

### 未改动（故意没动）

- `pet/pet_sprite.py`、`pet/overlay_window.py`、`pet/sprite_behavior.py`、`pet/window_alerts.py`、`pet/vision.py` —— T-304 红线：
  Live2D 代理只**读**这五个文件，几何/物理/告警/视觉一条都不改。
- `pet/live2d_sprite.py` 的页面侧（`refs/live2d/**`）与模型贴图资源：238 MiB，不进 git；改动只在运行页那一个 html。
- 慢通道「脑」（`D:\AI\slowdesk\persona\action_pool.json`、`slow_think` / `slow_direct`）：本批只**消费**动作名，不改生成端。
- 人设 / `slowdesk` 预设：表演白名单是运行时追加，**不写 persona**（T-306 判据）。

## 三、实现要点

1. **为什么是独立窗（D2 方案 A）**：嵌进 overlay 合成窗会变成 Chromium 子控件，黑底与透明叠加风险不可控；独立窗 + 引擎只推全局矩形，
   代价是「鼠标要能穿透到合成窗」——用「整窗鼠标穿透 + `Live2DSprite.alpha_at` 遮罩判定命中」把跨窗口转发代价压到零。
   窗标志刻意带 `WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating`，不抢前台焦点。
2. **代理而不是重写 sprite 协议**：`Live2DSprite` 继承 `PetSprite`、`paint` 空操作、内部 `PetSprite` 当几何内核。
   这样 `_sprite_factory` 的协议全满足，位置/钳制/碰撞/落岛/拖拽一条都不需要改；`MAX_WINDOWS=1` 时主宠走 Live2D、子宠走 webm。
3. **表演标签为什么挂在 `ChatService._finished`**：`finished.emit` 之后聊天窗立刻上屏并落盘（`widgets.py:2470→2495`），
   任何下游点再剥标签都已经晚了（标签已进会话存档）。挂在 `_finished` 的「完整原文还在」那一个瞬间，三个入口一次覆盖；
   指令进**单槽信箱**（最新覆盖旧的），由全局完成回调统一落地，避免并发多轮回复互相打架。
4. **慢通道动作的三态语义**：`OverlayShell.request_link_anim` 的返回值是**聚合视图**（True / False / None），
   `MultiWindowProxy` 汇总多窗（任一 True→True，全 False→False，其余 None），`dsh_scene_bridge` 据此写 `played/sent/via`。
   关键取舍：Live2D 下映射不出来**不进 webm 链**——旧路径最终落到 `Live2DSprite.bind_clip`（no-op），
   把 no-op 记成"已播"正是 T-309 要消灭的假象；宁可日志说 `live2d-miss`。
5. **渲染代价的三条断流阀**：① 不可见/全屏 → `set_render_enabled(False)` → `__pauseRender(true)`（ticker.stop）；
   ② 待机 6 s → 页面自判 + `__idleCheck` 心跳（冻住后心跳放慢到 2 s/次，`touch()` 1 s 节流叫醒）；
   ③ 窗口数硬上限 1。三条都在 `pet/live2d_sprite.py` 内，不依赖外部调用方自觉。
6. **尺寸联动**：`window_size(scale) = round(640*scale) × round((360+30)*scale)`，与 `window.py:939-940` 逐字同口径；
   建宠时用 `_cur_build_slot` 一次性上下文把 slot 身份传给工厂（工厂签名保持 `(lib, pos, scale)` 不变，大量测试注入三参 lambda）。
7. **T-308 为什么不改 bundle**：`loadParameters` 是 Cubism「每帧从动作姿势重算」的地基，改 120 KB minified 类内部风险远大于收益；
   「撤-贴」两个点都在 `coreModel` 公开方法上，包一层即可，升级 bundle 也不冲突（`core.__t308Hooked` 幂等保护）。
8. **`PET_PACK_DIR` 去重保序**：设置页按它安装、切换角色却只扫 cwd/数据目录 = 只装不认；去重是保住既有
   `dirs.count(base) == 1` 断言，保序是不改变既有发现优先级。

## 四、性能分析

**方法（可复现）**

- 微基准（纯函数层，零 Qt 事件循环 / 零窗口 / 零网络 / 零磁盘写）：
  `.venv311/Scripts/python.exe .scratch/live2d-performance-20261108/probe_perf.py --out .tmp/t309/probe_perf-rerun-1815.json`
  （100 个慢通道动作名取自本机真源 `D:\AI\slowdesk\persona\action_pool.json`；`bench()` 先 warmup 再取 n 次样本的 p50/p95/mean/max）
- 运行时代价（真 QtWebEngine 窗 + 真模型）：`.venv311/Scripts/python.exe -m pet.live2d_probe --idle-bench --seconds 40`、
  `--hidden-bench`、`--fullscreen-bench`、`--scale 1.5 --seconds 8 --shot …`（回执 `R-20261108-305` §判据 3–5）
- 环境：`Windows-10-10.0.19045-SP0` / Python `3.11.9` / PySide6 `6.11.2`；本机 8 G 显存卡，实跑 2026-11-08 17:5x 与 18:18（低谷）。

**微基准实测**（2026-11-08 18:18:46 复跑，`.tmp/t309/probe_perf-rerun-1815.json`）

| 指标 | n | p50 | p95 | 归属 |
|---|---|---|---|---|
| `parse_reply` 短回复（14 字，含标签） | 2000 | 4.1 µs | 6.9 µs | 新增热路径：每条回复 1 次 |
| `parse_reply` 长回复带标签（3,720 字） | 300 | 306.1 µs | 467.6 µs | 同上（最坏档） |
| `parse_reply` 长回复纯文本（3,600 字，零命中） | 300 | 3.5 µs | 5.4 µs | 同上（快速返回） |
| `prompt_block()` 生成 | 2000 | 7.7 µs | 11.4 µs | 每轮 prompt 组装 1 次 |
| `resolve_scene_action` 命中 | 2000 | 2.2 µs | 3.1 µs | 每次慢通道点名动作 |
| `resolve_scene_action` 未命中 | 2000 | 4.1 µs | 6.5 µs | 同上 |
| `scene_mood` 命中 | 2000 | 0.5 µs | 0.5 µs | 同上（映射第一段） |
| 100 个动作池全扫 | 200 | 336.8 µs | 463.2 µs | 一次性自检/测试，不在热路径 |
| `Live2DSprite` 构造 | 500 | 21.3 µs | 30.6 µs | 建宠 1 次（含代理与几何内核） |
| `play_scene_action` 命中 | 2000 | 10.2 µs | 17.2 µs | 每次点名动作 |
| `play_scene_action` 未命中（发 0 条 JS） | 2000 | 6.3 µs | 8.7 µs | 同上；`miss_scripts = 0` |

**运行时代价实测**（回执 `R-20261108-305` 判据 3–5；同日 00:58–01:44 由 T-305 执行器测得）

| 指标 | 实测 | 归属 |
|---|---|---|
| 可见运动时 CPU（真窗） | **197.0 %**（child 140.4 %，≈1.97 核） | 角色层固有（Chromium 出帧） |
| 待机冻帧后 CPU（中位） | **1.6 %** | 稳态；冻帧是硬条件 |
| 进入待机耗时 | `4.06 s + ≤2 s ≈ 6.06 s`（页面自报 `idleMs: 6063`） | 与 `IDLE_FREEZE_MS=6000` 对得上 |
| 动作叫醒后 child CPU | **131.2 %**（`resumed` 非冻结态） | 恢复不是只翻标志位，是真出帧 |
| 单窗显存 | 空闲 608 MiB → 1446 MiB，**Δ +838 MiB** | 代价；`MAX_WINDOWS=1` 即为此 |
| 多宠场景 | **Δ +826 MiB，windows_opened=1，其余 4 只 fallback webm** | 身份分流按预期生效 |
| 窗口尺寸 | scale 1.0 → **640×390**；0.72 → **461×281**；1.5 → **960×585** | 与 `window.py:939-940` 同口径（改前恒 520×760） |
| `prompt_block()` 体积 | **550 字符 / 1,234 B**（每轮 system 固定追加） | 输入 token 成本，非 CPU 成本；内容确定性 ⇒ 前缀缓存可复用 |

**实机复核：纠正「待机冻帧后 1.6 %」的探针口径（2026-11-08 19:33–19:48，部署态真桌宠 PID 45668）**

上表「待机冻帧后 CPU 1.6 %」是**离体探针**（`live2d_probe --idle-bench`，没有行为机）的读数。本轮把**部署态**
（真桌宠 `python -m pet`，2026-11-08 19:25:34 启动，overlay 拓扑）并排量了一遍，结论不同：

| 口径 | 页面状态 | 实测 CPU | 证据 |
|---|---|---|---|
| 离体探针（无行为机） | `frozen=true`（`frozenAt=7349 ms`） | 待机冻帧后主进程 **≈1.6 %**（中位） | 上表 `R-20261108-305`；本轮复跑 `.tmp/t309/probe_idle_freeze.json` 仍 `frozen=true` |
| **部署态（真桌宠）** | **从不进入冻帧**，页面持续出帧 | **1.927 核**（pythonw `45668` 0.588 + `QtWebEngineProcess 6132` 1.339；样本 119.36 s / 324 行；主进程逐秒 0.384–0.851） | `.tmp/t309/cpu-idle-193323.csv`（19:33:23–19:35:23） |

三条独立证据说明部署态页面确实没冻：

1. **窗口没动**：8 次窗口枚举（间隔 5 s）Live2D 窗恒为 `640×390 @ (1230,1016)`（`.tmp/t309/windows-watch-1933.json`）⇒ 这些 CPU 不是拖拽/移动造成。
2. **画面在变**：1.2 s 间隔抓 4 帧，相邻帧 `mean_abs_diff 0.55–1.19`、`changed_ratio 1.6 %–2.9 %`，差分掩膜 bbox = 角色本体
   `239–403 × 18–349`（`.tmp/t309/freeze-1938.json` / `freeze-1940.json`，判 `ANIMATING`）。
3. **谁在叫醒它**：在隔离实例给 `Live2DWindow.touch` 装调用栈记录器（不碰在跑的桌宠），每 **6.00–6.25 s** 一次，调用链固定为
   `sprite_behavior._enter_idle → _bind_with_gen → Live2DSprite.bind_clip → Live2DSprite.touch → Live2DWindow.touch`
   （`.tmp/t309/probe_touch_source.py` + `touch-log.txt`，14 条样本 19:45:28–19:48:28）。

**为什么正好卡在阈值**：主宠待机素材 `characters/dafeiyu/frameseq/idle/待机呼吸/meta.json` = `fps 24.0 / frames 144` ⇒ **6.000 s**；
`pet/live2d_sprite.py:107 IDLE_FREEZE_MS = 6000`，`refs/live2d/web/index.html:20 window.__IDLE_MS = 6000`。
行为机每圈待机结束都走 `_enter_idle → _bind_with_gen → bind_clip`，而 `Live2DSprite.bind_clip` 按 T-305 设计是
「no-op + `touch()`」（`pet/live2d_sprite.py:469-475`）⇒ 每 6.0 s 就把页面 `__lastAct` 重置一次；页面能积累的 `idleMs`
上限被压在 ~6.0 s，而 `__idleCheck` 是 1 s 心跳、要恰好落在 `>= 6000` 的瞬间才冻——实测 120 s 内一次都没发生。

**结论修正**：冻帧机制本身没坏（离体 7.349 s 能冻），但「待机 6 s 冻帧」这条省钱杠杆**在部署态不成立**。
部署态空闲稳态按 **≈1.93 核**记，**不要引用 1.6 %**。这是**设计假设与部署现实的偏差**，不是本批引入的缺陷；
要真正省 CPU 得动待机圈换绑节奏或阈值，属需要单独拍板的改动（见 §七.9）。

**修复与修复后复核（2026-11-08 19:59–20:03，`bind_clip`/`restart_clip` 不再唤醒）**

修法：`Live2DSprite.bind_clip` / `restart_clip` 是 webm-only no-op，一律不再 `Live2DWindow.touch()`；
唤醒只保留两条真实可见通道 —— `_run_page`（表情/动作/口型）与 `Live2DWindow.set_geometry_global`（位移）。
分类半修版（只对非待机池唤醒）一并收掉。TDD：`test_live2d_clip_rebind_does_not_wake_the_page` 先红
（`assert 2 == 0`）→ 终修后聚焦面 **29 passed**、ruff `pet tests` 全绿、overlay 相关族 28 passed。

口径：隔离完整 overlay 应用（`.tmp/t309/probe_idle_verify.py`，隔离 `APPDATA/LOCALAPPDATA` + 真 QtWebEngine +
真模型，40 s，窗口 461×281，`idleThreshold=6000`），同一脚本同一环境跑修复前后两版：

| 层 | 换绑唤醒 | 冻帧驻留（连续） | 冻帧态 CPU（中位） | 出帧态 CPU（中位） |
|---|---|---|---|---|
| 未修复（19:48 部署态） | 每 6.00–6.25 s 一次，40/40 条 | 120 s 内从未冻帧 | — | **1.927 核** |
| 半修（分类抑制，`before`） | 4 次/40 s，每 10.25–11.0 s | 最长 **3.93 s**（4 段） | 0.15 核（15.0 %） | 1.52 核（151.6 %） |
| 终修（`after`） | **0 次**（运行期 bind_clip 全消失） | **23.0 s**（11.78→34.78 s，至真实位移） | **0.14 核（14.1 %）** | 2.27 核（真实走动，属预期） |

- 终修后 `after` 的 209 次 touch 全部是桌宠自主走动时的 `set_pos → _sync_window → set_geometry_global`
  （真实位移，按设计唤醒），运行期 `bind_clip` 计数为 **0** —— 换绑唤醒通道确实断开了。
- 尾段中位（17.2 %）与抓帧像素差（1.84 %）抓在走动窗口，不能当冻帧态数字；冻帧态只取连续冻帧段。
- 证据（未跟踪）：`.tmp/t309/probe-idle-compare.md`、`probe-idle-compare.json`、`probe-idle-{before,after}.json`、
  `touch-{before,after}.txt`；复跑命令见对照文件。
- **部署态未复测（当时）**：代码改动要重启桌宠实例才生效，本会话没有替用户重启；隔离完整应用数据已足以验证机制与冻帧态。
  20:06:00 在跑实例已由外部重启并运行修复后代码，随后（20:09–20:14）补做只读复测，见下节。

**部署态复测（2026-11-08 20:09–20:14，修复后实例）**

20:06:00 在跑的真桌宠已重启（PID 46860 shim → 43352 venv pythonw + `QtWebEngineProcess` 25780，命令
`.venv311\Scripts\pythonw.exe -m pet`，cwd = 本 worktree；`pet/live2d_sprite.py` mtime 19:59:25 < 启动时间 ⇒ 跑的就是修复后代码）。
本次只做只读测量，没有再重启桌面实例。

| 口径 | 旧部署态（19:33，未修复） | 新部署态（修复后） | 证据 |
|---|---|---|---|
| 空闲 CPU | **1.927 核**（119.36 s 样本） | **0.08 核**（119.1 s）／**0.09 核**（44.1 s 复跑） | `.tmp/t309/cpu-idle-193323.csv` ↔ `cpu-deployed-fixed-2009.csv` / `cpu-deployed-burst-2021.csv` |
| 窗口位置 | 8 次枚举稳定 | **25 次枚举 / 120 s 逐次一致**（三窗 rect 全不变） | `.tmp/t309/windows-deployed-fixed-2009.json` |
| 逐秒形态 | 主进程逐秒 0.384–0.851 | 中位 **0.03 核/秒**，30 s 内 2 次 <0.5 核·秒短脉冲 | 同 CSV |

⇒ **部署态空闲稳态由 1.927 核降到 0.08–0.09 核（约 22–24×）**，窗口全程不动，换绑唤醒修复在部署态成立。

口径注意：① 部署态**不能再用全窗像素差判冻帧**——透明置顶层会把窗口后面的内容一起抓进来，实测全窗 0.5 s 连拍出现
2 %–16 % 像素差而三窗位置全程不动（背后在跑的 DSH Web GUI 会话文本更新从透明区透出；同一时段另一会话留下的
`deployed-frames-hash.json` 记的 3.3 %–6.9 % 帧差也是同一假象，不可据此判「页面仍在动」）；改用窗内角色不透明区
（260,200 80×60）0.1 s 连拍 21 帧，**18/20 对相邻帧 0 变化**，仅 1 次单帧更新（<0.5 核·秒），来源已钉死为候选①「命中遮罩 `alpha_grid` 强制 render 一次」：
`MASK_REFRESH_S = 1.0`（`pet/live2d_sprite.py:409`）节流的 `_maybe_refresh_mask()` 挂在 `_sync_window()` 的每个
位置/几何 tick 上（`:631`/`:635`），而 `alpha_grid()` 的 JS 因 WebGL 帧缓冲合成后即丢弃、**必须先显式
`app.renderer.render(app.stage)`** 再读 alpha（`:365-387`）⇒ 冻帧态残留 ≈1 Hz 强制单帧；实测周期（0.25 s 连拍下
第 0/4/8/11/15 对出现更新，即 1.0 / 1.0 / 0.75 / 1.0 s）与该节流常数一致，行为机偶发 1 px 位移候选无证据支持。② 部署态**不直读**页面 `frozen` 标志
（无 CDP/注入通道且不替用户挂调试端口）；`frozen=true` 的直读证据只在隔离探针（C 层）。部署态结论以 CPU + 静止窗口为准。

**部署态唤醒闭环与判据边界（2026-11-08 20:31–20:38，同时间轴三组对照）**

20:06 启动的实例（PID 43352 / `QtWebEngineProcess` 25780）上，用同一探针（`.tmp/t309/combo_wakeup_probe.py`）
在同一次运行里同时采「两进程逐秒 CPU + `grabWindow(hwnd)` + 屏幕同矩形抓取 + 窗口 rect」，每组 26 s、第 4.4 s 由
`tools/pet_say.py` 真实投递一次；`--action=` 空串为纯气泡对照组：

| 组 | 触发内容（日志实测映射） | 冻帧基线 0–4 s（合计核） | 唤醒段 6–10 s 合计核 均值 / 同秒峰值 | 回落 13–20 s | 窗句柄抓帧 |
|---|---|---|---|---|---|
| A 纯气泡 | 只写气泡，**无表情无动作**（文本 len=5） | 0.06 | **0.06 / 0.12** | 0.05 | 111 帧逐像素 0 变化 |
| B 动作非空 | `舞狮头` → 表情='爱心眼' 动作='举手切默认手' | 0.05 | **1.97 / 2.10** | （并发污染，不取） | 110 帧 0 变化 |
| B′ 同 B 复跑 | 同上（新探针，文本 len=8） | 0.08 | **1.97 / 2.18** | 0.05 | 112 帧 0 变化 |
| C 表情非空、动作 None | `点击回应-元气挥手` → 表情='默认体型' 动作=None | 0.04 | **1.98 / 2.25** | 0.05 | 111 帧 0 变化 |

- **唤醒闭环成立**：冻帧基线 0.05 核 → 真实活动唤醒 5 s 均值 **1.97–1.98 核**（同秒合计峰值 **2.10–2.25**；
  Chromium 1.23–1.65 + pythonw 0.45–0.67）→ 结束后回 0.05 核。唤醒时长约 6 s（与动作时长一致），未出现「冻死」。
- **窗口 rect 全程不动**：B′/C/A 三组各 110–112 帧 rect 唯一为 `(1230,1016,640,390)` ⇒ 唤醒不是自主走动的假象。
- **判据边界（同一次运行内闭环）**：B/B′/C 三组窗句柄抓帧 110–112 帧**零变化**，同一时段 Chromium 实测 1.23–1.65 核
  ⇒ `QScreen.grabWindow(hwnd)` 是**缓存快照**，不能用于部署态冻帧判定；屏幕同矩形抓取在 A 组（页面全程冻帧）
  也出现 5300+ 像素变化（透明置顶层背后的内容 + 气泡都会变）⇒ 屏幕抓取同样不能单独当判据。
  **可用判据 = 两进程逐秒 CPU 为主**；rect 静止与窗句柄零变化仅作辅证（后者恒真，几乎无判别力）。
- **唤醒源判据修正**：`点击回应-元气挥手`（表情='默认体型'、动作=None）**确实唤醒页面**（Chromium 1.36–1.58 核 × 6 s，
  与 `舞狮头` 同量级）。早先 `.tmp/t309/probe-deploy-wakeup-2026.md` §2 用窗句柄抓帧零变化得出「空映射动作不唤醒」，
  该判据已被本节同时间轴数据推翻。真正不唤醒的是**纯气泡**（无表情、无动作）：A 组全程 0.02–0.10 核。
  ⇒ 唤醒源 = `_run_page` 收到**非空表情或动作**（`默认体型` 也算非空表情）；用「动作是否为 None」单独判唤醒是错的。
- **可见运动第三方法复核（2026-11-08 20:41，角色剪影掩膜）**：静态窗口快照（白底+角色本体）取非白像素得剪影掩膜
  （27,902 px = 窗口 11.2%），只在掩膜内统计屏幕帧差，同时逐帧记录窗口 rect：动作段 **22/23 对相邻帧连续有变化**
  （idle 2/6、静置 8 s 后 2/7），rect 全程 `(1230,1016,640,390)` ⇒ **角色本体可见运动**（排除走动、背景文字与气泡），
  把「CPU 唤醒」升级为可见证据；也解释纯气泡组屏幕变化来自透明区背景。官方 `pet_say --verify-visual` 在本机不可用
  （待机对照 motion=4.17 已高于其 1.5 阈值，背景文字污染；动作 5.095 与对照分不开）。证据
  `.tmp/t309/masked-char-motion-204148.json` + 说明 `.tmp/t309/masked-char-motion-204148.md`。
- **口径**：20:31–20:38 期间另一并发会话仍在触发同类动作（pet 日志 20:31:28 / 20:32:20 / 20:34:13 / 20:36:11 四条，
  气泡文本 4/6/6/6 字交替）；本表只取本侧触发后 6–10 s 段与污染前区间（C 组备用 B′ 即因此）。
  证据 `.tmp/t309/combo-{lion-2032,lion2-2037,wave-2034,bubble-2036}.json`，复算命令见
  `.tmp/t309/probe-deploy-wakeup-2026.md` §5。

**结论**

1. **稳态开销**：**离体探针**待机冻帧后主进程 ~1.6 %（中位），可见运动 197 %（≈2 核）；
   **部署态旧代码空闲稳态实测 1.927 核**（待机冻帧因换绑唤醒进不去，见上「实机复核」）。**已修（2026-11-08 20:0x）**：
   webm 播控（`bind_clip`/`restart_clip`）不再唤醒页面，唤醒只留表演层（`_run_page`）与位移（`set_geometry_global`）；
   隔离完整 overlay 应用实测换绑唤醒 **0 次**、连续冻帧 **23.0 s**、冻帧态 **0.14 核**（旧 1.927 核）。
   不可见/全屏 `__pauseRender` 停表杠杆不变（离体实测生效）。**部署态已于 2026-11-08 20:06:00 重启生效并复测：
   空闲 0.08 核（119.1 s）/0.09 核（44.1 s）、窗口 25 次枚举全静止 ⇒ 相比未修复的 1.927 核降约 22–24×**（见上「部署态复测」）。
   部署态**唤醒闭环**（20:31–20:38 三组对照）：冻帧基线 0.04–0.08 核 → 真实动作/表情唤醒 5 s 均值 **1.97–1.98 核**
   （同秒合计峰值 **2.10–2.25**）→ 结束回 0.05 核，窗口 rect 全程不动；窗句柄抓取恒零变化属缓存快照，
   **不能**当冻帧判据（见上「唤醒闭环与判据边界」）。**可见运动**另有第三方法复核：动作期角色剪影掩膜内 22/23 对
   相邻帧连续变化（待机 2/6、静置 2/7，窗口 rect 不动）⇒ 冻帧不吞表演。
2. **新增路径绝对成本与触发频率**：`parse_reply` 每条回复 1 次（p50 4.1 µs；最坏 3.7k 字带标签 306 µs）；
   `prompt_block()` 每轮 1 次 7.7 µs，真正的成本是每轮 **+550 字符 / 1,234 B** 输入体积；
   `resolve_scene_action` / `play_scene_action` 只在慢通道点名动作时各 2–17 µs；`Live2DSprite` 构造只在建宠时 21 µs。
   没有一条进入逐帧路径（逐帧路径仍全部在 Chromium 侧）。
3. **新增系统调用 / 网络 / 磁盘 / 线程**：新增 **1 个 Python 线程**（本地 `127.0.0.1` 静态 http 服务，为绕 `file://` 的 CORS）、
   **1 张 QWebEngineView**（Chromium 自带 GPU/renderer 进程，不是 Python 线程）、**3 个 QTimer**
   （ready 轮询 300 ms 且 ready 即停；idle 心跳活动 1 s / 冻结 2 s；口型 60 ms 只在说话期间 ≤20 s）。
   **无外网请求**；磁盘只在页面装载时读 `refs/live2d` 本地资源；无新增持久化写入。
4. **内存与缓存**：+838 MiB 显存/窗（硬上限 1 窗 = +838 MiB 量级，多宠场景实测 +826 MiB）；
   Python 堆新增可忽略（代理构造 21 µs、无大对象）；新缓存只有 23 个表情参数表与几何遮罩（页面内），无落盘缓存文件。

## 五、实机运行记录

**本机真实环境**（Win10 22H2 / Py3.11.9 / PySide6 6.11.2；非 CI、非 mock；真 QtWebEngine 进程 + 真 Live2D 模型）。
**2026-11-08 19:25:34 已在本机把桌宠重启到本批代码并完成「用户可见验收」**（见 §5.6；旧进程 PID 27580 / 32148
= 2026-11-07 20:06 的 webm 旧代码，已结束）。20:06:00 部署态实例再次重启（PID 46860 → 43352）以加载 20:00 的冻帧修复，
20:09–20:14 的复测数字见 §四「部署态复测」。下面 5.1–5.5 的探针口径证据仍然有效，5.6 是部署态与用户可见面的复核。

### 5.1 根因现场复现（旧行为：投递冒充已播）

旧代码 `dsh_scene_bridge` 的回落路径是「调用上游 → 一律 `ok=True` →日志写 `已播=True`」，而 Live2D 的 `bind_clip` 是 no-op。
探针把目标换回 webm 替身即可复现「只投递」与「池内真播」混为一谈：

```
$ .venv311\Scripts\python.exe .tmp\t309\probe_overlay_chain.py
INFO:pet.dsh_scene_bridge:[SCENE] 回落上游 request_link_anim：'舞狮头' sent=None
INFO:pet.dsh_scene_bridge:[SCENE] agent=dsh action='舞狮头' played=False sent=True via=upstream-queued anim=?
{"name": "舞狮头", "target": "webm(no play_scene_action)", "returned": null, "pending": "舞狮头"}
```

### 5.2 修复后（真 Live2D 窗 + 真模型）：点名动作真的改了表情和动作

```
$ .venv311\Scripts\python.exe .tmp\t309\probe_overlay_chain.py     # 真实 OverlayShell + MultiWindowProxy + OverlayShell 目标
{"name": "舞狮头", "returned": true, "log": "[SCENE] agent=dsh action='舞狮头' played=False sent=True via=live2d-sent anim=?",
 "before": {"currentExpression": null, "lastMotion": null},
 "after":  {"currentExpression": "爱心眼", "lastMotion": "Gesture#0:举手切默认手"},
 "expression_changed": true, "motion_changed": true}
{"name": "玩游戏气急败坏", "returned": true, "via": "live2d-sent",
 "after": {"currentExpression": "屑屑眼", "lastMotion": "Gesture#3:竖手指切出来动画"},
 "expression_changed": true, "motion_changed": true}
{"checks": {…16 项…}, "ok": true}
```

场景动作直投（`Live2DSprite.play_scene_action`，5 case / 7 checks 全 true）：`舞狮头`→爱心眼+举手；`玩游戏气急败坏`→屑屑眼+竖手指；
`左转奔跑`/`举手` 只改一层；`瞎编的动作` → `sent=false`、画面零变化。

### 5.3 边界与失败路径（本该失败/本该置灰的实机观察）

```
INFO:pet.dsh_scene_bridge:[SCENE] Live2D 无对应动作：'瞎编的动作'（no-op）
INFO:pet.dsh_scene_bridge:[SCENE] agent=dsh action='瞎编的动作' played=False sent=False via=live2d-miss anim=?
```

- 未知动作：不进 webm 链、页面零 JS（`play_scene_action_miss_scripts = 0`），跳过合成链的旧 `switch_clip` 不再被调用。
- Live2D 起不来/超上限：主宠自动退回 `PetSprite`（探针 6 checks 全 true：`win_type_is_multi_window_proxy` / `proxy_has_no_pool_api` …）；
  多宠场景 `windows_opened=1 / fallback 4`，证明只有主宠上 Live2D。
- 骨架 webm 目标：仍走 `upstream-queued`（`sent=True played=False`），旧路径未破坏。

### 5.4 表情管线真机取证（T-308）

23 个表情逐张截图 + 参数读回（`.tmp/t308/`，`3 passed in 110.85s`，exit 0）：**23/23** 参数读回 = 画面值
（21 条正向 + 2 条纯归零互斥），页面自报 `__exprLoaded=23`。T-306 端到端（真回复 → 标签 → 表情+动作+口型）像素差：
`baseline_noise_ratio 0.0` → `e2e_diff_ratio 0.0274`（`e2e_changed_px 3419`）；口型 `duration_ms 2520 / planned_frames 42 /
mouth_peak 0.9731 / mouth_valley 0.0`。T-305 尺寸四档截图（`.tmp/t305/size15*.png`）逐张量过 alpha 包围盒。

### 5.5 无法自动验证的能力（为什么不能自动）

- **肉眼观感**（角色边缘、动作连贯、色差）：脚本无法判定 ⇒ 用 `QWebEngineView.grab()` 带 alpha 像素 + diff 作为可复算代理
  （WebGL `toDataURL` 在 `preserveDrawingBuffer=false` 下返回全透明，是这条路径存在的直接原因）。
- **offscreen 环境**：`QT_QPA_PLATFORM=offscreen` 下 Chromium 会刷 GPU context lost 噪声（日志可见），探针仍量到
  `platform=offscreen ready=True elapsed~3.8s`；属环境噪声，不是页面失败。
- ~~**真窗观感验收未做**：需要重启桌宠（会替换在跑进程），本轮**没有**替用户做这个决定。~~
  **已补做（2026-11-08 19:25–19:29）**：重启已执行、部署态真窗验收见 §5.6；仍无法自动验证的只剩秋殇的肉眼观感。

### 5.6 用户可见验收（2026-11-08 19:25–19:29，部署态真桌宠）

**重启动作**：结束旧进程（PID 27580 / 32148，2026-11-07 20:06 启动的 webm 旧代码）→ 仓库根执行
`.venv311\Scripts\pythonw.exe -m pet`；新实例 PID 45156（shim）→ 45668（venv pythonw）+ `QtWebEngineProcess` 6132，
启动时间 **2026-11-08 19:25:34**。日志真源：`data\_appdata\dsh-pet-standalone\pet-45668.log`。

**启动链日志（逐行原文）**：

```
19:25:35,734 INFO 渲染拓扑: overlay(单合成窗)
19:25:36,329 INFO live2d: 开窗 640x390（scale=1.00，第 1 张）
19:25:40,079 INFO live2d: 运行页 ready（motions=['Idle', 'Gesture']，expressions=['体型1', …, '默认体型', '＞＜']）
```

**慢通道 → 表演层端到端（真桌宠，19:28:13.301）**：

```
19:28:13,301 INFO live2d: 慢通道动作 '舞狮头' -> 表情='爱心眼' 动作='举手切默认手'
19:28:13,302 INFO [SCENE] agent=game-copilot action='舞狮头' played=False sent=True via=live2d-sent anim=?
19:28:13,372 INFO [BUBBLE] shown agent=game-copilot len=14 host=MultiWindowProxy ret=True
```

⇒ 一次调用同时产生 **Live2D 表情 + 动作 + 气泡**三件可见结果；`via=live2d-sent` 证明走表演层而不是 webm 回落；
`played=False` 如实标注「Live2D 不走池内播放」，不再是旧代码的「一律 played=True」。

**窗口层面**（8 次枚举 / 5 s 间隔，`.tmp/t309/windows-watch-1933.json`）：三张窗齐全且全程稳定 ——
灵动岛条 `669×16 @ (751,0)`、**Live2D 角色窗 `640×390 @ (1230,1016)`**、overlay 合成窗 `2560×1400 @ (0,0)`；
三者均 `Qt6112QWindowToolSaveBits` + `layered` + `noactivate`（无边框 / 置顶 / 不抢焦点，与设计一致）。

**画面层面**：窗口抓图可见蓝发角色真实渲染在桌面之上（`.tmp/t309/live2d-window-193412.png`，720×470 grab，
角色立绘完整、背景透明）；点名动作后主宠像素差 **5.86**（> 1.5 即判「动作真播」）。

**用户可见验收结论**：

| 验收项 | 结果 | 判据 |
|---|---|---|
| 角色层真的是 Live2D（不是 webm） | ✅ | 启动日志 `overlay` + `live2d: 开窗` + `运行页 ready`；独立 `640×390` 窗；`QtWebEngineProcess` 存在 |
| 窗口可见、位置合理、不抢焦点 | ✅ | `(1230,1016)` 在主屏右下，`noactivate` 置顶透明窗 |
| 慢通道点名动作真的改表情 + 动作 | ✅ | 日志 `表情='爱心眼' 动作='举手切默认手'` + 像素差 5.86 |
| 气泡真的出现 | ✅ | `[BUBBLE] shown … host=MultiWindowProxy ret=True` |
| 观感（边缘 / 连贯 / 色差） | ⚠ 未由秋殇肉眼确认 | 本报告只有像素与状态代理证据；秋殇可直接看桌面主宠确认 |
| 空闲 CPU 是否如报告所称 1.6 % | ❌ 旧实例不成立 → ✅ 修复后部署态实测 0.08 核 | 旧部署态 ≈1.93 核（§四「实机复核」）；2026-11-08 20:0x 修复、20:06:00 重启后，部署态实例空闲 **0.08 核**（119.1 s）/0.09 核（44.1 s），窗口 25 次枚举全静止（§四「部署态复测」） |

> 本节同时划掉 §5.5 末条「真窗观感验收未做」与 §七.2「用户可见验收未做」：重启已执行，剩的只有**肉眼观感**这一项无法自动验证。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.venv311\Scripts\python.exe -m ruff check pet tests` | **All checks passed!**（exit 0，2026-11-08 18:29 复跑） |
| 聚焦·新功能 | `pytest tests/test_performance_scene_actions.py -q` | **25 passed** in 1.36 s（exit 0，2026-11-08 18:28 复跑；22 例 + §七.7 新增 3 例）；待机冻帧修复后 **29 passed**（2026-11-08 20:00） |
| 聚焦·记忆回归 | `pytest tests/test_memory_recall.py -q` | **1 failed, 52 passed** in 3.00 s；唯一红=既存 `test_manual_pin_remember_topic_unarchives`（下条） |
| 聚焦·逐字节契约 | `pytest tests/test_memory_recall.py::test_build_messages_without_payload_is_byte_identical -q` | **1 passed** in 1.49 s |
| 相关族回归 | `.tmp/t309/regression-20261108.log`（前一轮执行器记录的 519 例相关族） | **11 failed, 507 passed, 1 skipped** in 83.90 s（逐条归因见下） |
| 开关语义·红转绿（§七.7） | `pytest tests/test_performance_scene_actions.py -q`（新增 3 例先跑） | 修前 **2 failed**：`ImportError: live2d_enabled_by_env` + `touched == ["instance"]`（`PET_LIVE2D=0` 仍进 Live2D 分支）；修后 **25 passed**（exit 0） |
| 待机唤醒·红转绿（§七.9） | `pytest tests/test_performance_scene_actions.py -q` | 修前 **1 failed / 14 passed**（`assert 2 == 0`：换绑/续圈仍唤醒页面）→ 终修后 **29 passed** in 1.39 s（exit 0，2026-11-08 20:00） |
| 静态检查（本轮） | `.venv311\Scripts\python.exe -m ruff check pet tests` | **All checks passed!**（exit 0，2026-11-08 20:02） |
| overlay 相关族（本轮） | `pytest tests/test_overlay_dead_switches.py tests/test_sprite_visibility.py -q` | **28 passed** in 2.02 s（exit 0，2026-11-08 20:02；唤醒改动未碰显隐/拖拽路径） |
| overlay 回归 | `pytest tests/test_sprite_visibility.py tests/test_overlay_dead_switches.py -q` | **28 passed** in 2.11 s（exit 0，2026-11-08 18:28；开关改动未碰显隐/拖拽路径） |
| HEAD 基线对照 | `.tmp/head-baseline`（93c9adb 纯净树）`pytest tests/test_memory_recall.py::test_manual_pin_remember_topic_unarchives -q` | **1 failed in 2.58 s** ⇒ 该红在基线就存在，**不是本批引入** |
| 断言有效性（变异） | `.tmp/t309/mutate_t309.py` + `mutation-20261108.log`（M1–M4 各注入一处缺陷后重跑） | **4/4 命中**：M1→2 红、M2→2 红、M3→2 红、M4→1 红；还原后 sha 逐字节回原值，再跑 **22 passed**（当时用例数） |
| 全量 | `pytest -v`（实际收集 **9162** 例；`QT_QPA_PLATFORM=offscreen`，日志 `.tmp/t309/full-verbose-1848.log`） | **14 failed / 9116 passed / 32 skipped in 1700.16 s（28:20）**，2026-11-08 18:37–19:05 低谷期跑；14 条逐条归因见下（本批引入 1 条 = 灵动岛停靠细条测试未同步，已同步并复跑 20 passed） |
| 灵动岛停靠细条同步（全量暴露） | `pytest tests/test_island_bevel_fade.py -q` | 修前 **1 failed**（`test_docked_strip_has_no_bevel`：旧断言在 (35,8) 取整条底板，新设计短标外全透明）；同步到 T-20261107-303 形态后 **20 passed** in 9.37 s（exit 0） |
| 架构红线 | `pytest tests/test_architecture.py` | 本批未单独跑（未新增/恢复任何被退役模块） |
| 报告纪律（20:40 补丁后） | `.venv311\Scripts\python.exe -m pytest tests/test_pr_report_discipline.py -q` | **233 passed** in 3.10 s（exit 0，2026-11-08 20:39；本轮只改报告/HANDOFF/INDEX，产品代码零改动） |

**14 条红逐条归因（结论：本批引入 = 1 条，已修）**

| 组 | 数量 | 用例 | 归因 |
|---|---|---|---|
| A | 9 | `tests/test_game_assistant_menu.py`（toggles / submenu / slow / links / laptop / atomically / reverts / loop） | **既存红**：2026-10-27 全量日志里同样 9 条红，与本批无关 |
| B | 1 | `tests/test_memory_recall.py::test_manual_pin_remember_topic_unarchives` | **既存红**：基线 93c9adb 纯净树复跑同样红（见上表）；属 v4.4.0 升级带入的已知回归，待单独修 |
| C | 1 | `tests/test_island_network_envelope.py::test_beyond_envelope_readings_never_change_the_plan` | **跨分钟假红**：`_plan_inputs()` 里时钟文本 `18:45`→`18:46` 随分钟边界翻动（测试把时间也当计划输入比较），与本批无关 |
| D | 1 | `tests/test_screen_capture_ocr.py::test_self_built_sessions_are_injected_into_the_engine_cfg` | **环境缺依赖**：`ModuleNotFoundError: omegaconf`（本机 venv 未装，与本批无关） |
| E | 1 | `tests/test_tts_sapi_thread.py::test_sapi_synth_writes_a_wav_from_a_background_thread` | **环境**：SAPI5 后台线程合成失败 `TtsUnavailable (-2147352567, '发生错误')`（与改动无关） |
| F | 1 | `tests/test_island_bevel_fade.py::test_docked_strip_has_no_bevel` | **本批引入（测试未同步）**：动态岛上下停靠改中央 2px 短标后，旧断言取样点 (35,8) 从「整条底板」变成全透明；已按 T-20261107-303 新形态同步测试 → **20 passed** |

> 说明：上一轮记录的 C 组「负载计时假红」`test_topic_hit_build_messages_fast_and_persists_async` 本次全量**未复现**（负载条件不同）。

**本轮复核（2026-11-08 18:1x–18:3x，交接续接会话）**：上一轮在执行「三条聚焦测试」时被中途中止，本轮原样复跑三条（结果见上表）；
另重跑 ruff 与 HEAD 基线对照、复跑性能探针（`probe_perf-rerun-1815.json`），并核对全部证据文件存在性。
**本会话（18:25–18:31）补做 §七.7**：先写 3 例新测试（红：`live2d_enabled_by_env` 不存在 + `PET_LIVE2D=0` 仍进 Live2D 分支），
再把开关判定收成纯函数 `live2d_enabled_by_env()` 并接进 `_make_default_sprite`，红转绿（25 passed）、ruff 全绿、overlay 相关族 28 passed。
**本会话（18:37–19:12）补跑全量 + 收尾**：全量 **14 failed / 9116 passed / 32 skipped in 28:20**（归因见上表）；全量暴露的 F 组
（灵动岛停靠细条测试未同步）已按 T-20261107-303 新形态同步并复跑 **20 passed**，ruff 全绿。至此 §六 各门与「交付三份证据」齐备。
**本会话（20:28–20:42，交接续接会话）补文档增量**：把「部署态唤醒闭环 CPU 实测 + 判据边界」写进 §四 与 §七.10
（同时间轴四组对照，见 §四「唤醒闭环与判据边界」）；**无产品代码改动**，`tests/test_pr_report_discipline.py` **233 passed**。

## 七、已知限制与后续

1. **全量套件已跑（2026-11-08 18:37–19:05 补，续接会话）**：**14 failed / 9116 passed / 32 skipped in 28:20**，逐条归因见 §六
   （本批引入 1 条 = 灵动岛停靠细条测试未同步，已同步测试并复跑 20 passed）。踩坑记录：`pytest -q | Tee-Object` 在后台管道里
   两次「假死」（输出背压，日志分别止于 14%/18%、进程 CPU 停滞），改用 `pytest -v | Out-File` 一次跑通；全量占约 28 分钟 CPU，
   合并前仍需在目标环境跑一次（本机 2 条红是环境依赖：`omegaconf` 未装 / SAPI5 合成失败）。
2. ~~**用户可见验收未做**~~ **已完成（2026-11-08 19:25–19:29，部署态）**：旧进程 27580 / 32148（2026-11-07 20:06 的 webm 旧代码）
   已结束，新实例 PID 45156 → 45668 于 19:25:34 启动；Live2D 开窗 / 运行页 ready / 慢通道动作→「爱心眼 + 举手」/ 气泡 均实测可见（§5.6）。
   仍无法自动验证的只剩**秋殇的肉眼观感**（角色边缘 / 动作连贯 / 色差），本报告只用像素与状态代理。
3. **仓库外运行页是成对资产**：`D:\AI\AIWORK\game-copilot\refs\live2d\web\index.html`
   （当前 sha256 `9e74cacc…`，20,462 B；`__pauseRender` ×1、`__t308Hooked` ×2；T-306 基线 sha256 `23ec8a10…`）。
   它**不在本仓 git**，所以：换机/分发必须连同 `refs/live2d/`（238 MiB）一起同步；**回滚代码与回滚页面必须成对**，
   只回滚一边会得到「代码对、页面没钩子」的假故障。
4. **T-308 判据 6（不眨眼 / 不呼吸）字面不成立**：用 T-306 备份页对照证明是**既有缺陷**（不是本卡引入），未修；
   要修需改运行页且会改变角色观感，建议单独拍板。
5. **好感 / 信任只算不落盘**：P2b 的 4 档时段表与数值存档未做；本模块只把数值指令算出来（`STAT_LIMIT=20`）。
6. **每宠 Live2D 未做**：`MAX_WINDOWS=1`，第二只宠一律 webm；要放开需先解决显存（8 G 卡实测单窗 +838 MiB）。
7. ~~**`PET_LIVE2D` 文档与实现不一致（小缺陷，待修）**~~ **已修（2026-11-08 18:31，交接续接会话）**：旧实现读
   `os.environ.get("PET_LIVE2D")` 的真值，`"0"` 也算开——文档写「`PET_LIVE2D=0` 时走 webm」= 假开关。现收成纯函数
   `overlay_shell.live2d_enabled_by_env()`：未设/空串 → 交给配置 `live2d_sprite`（缺省 True），`0` → **硬关**，其它非空 → 开；
   `_make_default_sprite` 在 `0` 时工厂层短路、完全不碰 Live2D 层。判据：`tests/test_performance_scene_actions.py` 新增 3 例
   先红（2 failed：`ImportError` + `touched == ["instance"]`）后绿（25 passed）；ruff 全绿；overlay 相关族 28 passed。
   配置 `"live2d_sprite": false` 仍是可靠关法，两者不冲突（显式环境变量优先）。
8. **资源未纳入版本控制**：`refs/live2d/` 238 MiB（模型/贴图/运行页）；`PET_LIVE2D_ROOT` 可覆盖路径（笔记本同步布局不同）。
9. ~~**待机 6 s 冻帧在部署态失效（本轮实测，需拍板）**~~ **已修（2026-11-08 19:59–20:03，候选①的加强版）**：
   根因：行为机待机圈每 6.0–6.25 s 走一次 `_enter_idle → _bind_with_gen → bind_clip → touch`，而主宠待机素材正好
   6.000 s = `IDLE_FREEZE_MS`（144 帧 @ 24 fps），页面每 6 s 被叫醒一次、永不冻帧（部署态实测 1.927 核）。
   修法：`bind_clip`/`restart_clip` 是 webm-only no-op，**一律不唤醒**；唤醒只留 `_run_page`（表演层）与
   `Live2DWindow.set_geometry_global`（位移）。隔离完整 overlay 应用修复前后对照：换绑唤醒 4 次/40 s → **0 次**，
   连续冻帧 3.93 s → **23.0 s**，冻帧态 1.52 核出帧（半修版）→ **0.14 核（14.1 %）**；
   证据 `.tmp/t309/probe-idle-compare.md`。**行为变化**：待机时画面基本静止直到真实活动；**部署态已于 2026-11-08 20:06:00
   重启生效并复测空闲 0.08 核**（旧 1.927 核，约 22–24×↓；§四「部署态复测」）；观感待秋殇确认。
   候选②（下调 `IDLE_FREEZE_MS`）③（暂停待机换绑）未采用——①已足且不动阈值/素材节奏。
10. **部署态冻帧判据（2026-11-08 20:31–20:38 三组对照测得）**：`QScreen.grabWindow(hwnd)` 抓到的是**缓存快照**
    （动作期 Chromium 1.23–1.65 核而出帧 110–112 帧零变化），屏幕同矩形抓取在纯气泡冻帧组也会变 ⇒ 两者都不能单独判冻帧；
    判据以**两进程逐秒 CPU**为主，窗口 rect 静止与窗句柄零变化仅作辅证。唤醒源是 `_run_page` 的**非空表情或动作**
    （`点击回应-元气挥手` 的 `表情='默认体型'` 也算非空 ⇒ 会唤醒，1.36–1.58 核 × 6 s），只发气泡不唤醒。
    数据 `.tmp/t309/combo-*.json`，说明 `.tmp/t309/probe-deploy-wakeup-2026.md` §5。**可见运动**另有第三方法
    （2026-11-08 20:41 角色剪影掩膜：动作期 22/23 对连续变化、待机 2/6、静置 2/7、窗口 rect 不动），见
    `.tmp/t309/masked-char-motion-204148.md`。

## 八、风险与回滚

- **行为变化（2026-11-08 20:0x 待机冻帧修复）**：Live2D 页面的唤醒只剩表演层（`_run_page`）与位移
  （`set_geometry_global`）；待机时画面基本静止（冻帧）直到真实活动，webm 播控（换绑/续圈）不再产生视觉唤醒。
  这是省 CPU 的设计取舍，观感变化需秋殇确认；**部署态实测空闲 1.927 核 → 0.08 核（约 22–24×↓）**，
  待机窗口像素只剩偶发单帧更新（§四「部署态复测」）。回滚：恢复 `Live2DSprite.bind_clip`/`restart_clip` 里的 `self.touch()`
  并重启桌宠实例。
- **影响面**：角色渲染层（主宠）/ overlay 壳的建宠与显隐 / 聊天回复收尾（三个入口）/ 慢通道动作执行与日志 /
  灵动岛上下停靠绘制 / 角色包发现链 / 尺寸档位。子宠 webm 路径与五个几何物理文件零改动。
- **开关**：配置 `live2d_sprite`（缺省 **True**；显式 `false` 关；缺键**不写盘**）；环境 `PET_LIVE2D`（开发期分流：
  `0` = 硬关、未设 = 交给配置，见 `overlay_shell.live2d_enabled_by_env`）；
  环境 `PET_LIVE2D_ROOT`（运行页路径覆盖）；环境 `PET_LIVE2D_NO_SANDBOX=1`（受限 shell 下跑 Chromium）。
- **配置迁移**：无新增持久化键被写入；未改 `config-slot-N.json` / `overlay-active-pets.json` 结构。
- **回滚步骤**：
  1. `git checkout -- pet/agent_link/__init__.py pet/app.py pet/catalog.py pet/chat/prompt.py pet/chat/service.py pet/config.py pet/context_menus/game_assistant.py pet/dsh_scene_bridge.py pet/island/dynamic_island.py pet/multi_window_shared.py pet/overlay_shell.py tests/test_memory_recall.py`
  2. 删除新文件 `pet/performance.py pet/live2d_sprite.py pet/live2d_probe.py tests/test_performance_scene_actions.py`
  3. **成对**回滚仓库外运行页到 `index.html.bak-t306`（sha256 应回到 `23ec8a10…`）
  4. 若只想临时关掉角色层：配置 `"live2d_sprite": false` 后重启，无需回滚代码。
- **残留状态**：无新增落盘数据（性能探针、截图、变异备份都在 `.tmp/` / `.scratch/`，未跟踪；本报告的验证产物同样未跟踪）。
