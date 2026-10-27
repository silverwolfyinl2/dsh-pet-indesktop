# PR 报告：桌宠菜单第一层加「交给慢通道」入口（G7）

> **基线**：`2786c15`（Merge pull request #190 from MerZlin/codex/plugin-dlc-v5）
> **分支**：本地工作副本（按秋殇规则：**不 push**）
> **日期**：2026-10-15
> **范围**：1 个实现文件（+7 / −0）+ 1 个测试文件（+29 / −0）+ 本报告 + `INDEX.md` 登记 1 行
> **关联**：game-copilot 慢通道闭环（`tools/pet_actions.py::action_loop` → `tools/pet_loop.py::run_once`）

## 一、核心特性

**解决什么问题**：秋殇要"交给慢通道"时，菜单里**找不到这个词**。真入口是
「幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色（跑全）」（= `pet_actions.py loop --pick all`），
藏在两层子菜单里，只能靠记忆点。

**用户看到什么变化**：「游戏助理」菜单的**命令区第一层**多出「**交给慢通道（跑全）**」，
点它 = 老入口那条命令（同一个 `args`）→ 同一条指令账本、同一套闸门、同一个唯一终态。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 新入口（第一层） | `_add("交给慢通道（跑全）", ["loop", "--pick", "all"], tip)`；tooltip 里写明"= 幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色" |
| 2 | 与老入口同一动作 | `_add_on` → 同一个 `_spawn_quiet(["loop","--pick","all"], …)`；**没有**第二套实现、**没有**新命令、**没有**新配置键 |
| 3 | 老路径保留 | 「幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色（跑全）」逐字未动（老习惯与文档锚点不受影响） |

**红线 / 不变量**

- 「**停止快通道**」仍是命令区第一项（急停位置不动）；新项插在**它之后**。
- 7 个开关项的前 7 顺序不动（`test_menu_toggles_and_groups` 的既有契约）。
- 不新增 `menu action id`、不新增配置键、不动 `registry.py` / `menu_layout`（新项是
  `game_assistant` 子菜单的子项，不需要注册进布局引擎）。
- 不改任何 `tools/*`、不碰 `data/pet_loop.json`（`real_ok` 授权面）。

## 二、修改文件说明

`git diff --numstat` 在本仓库对这两个文件**取不到**：它们在本机 clone 里是
**未跟踪的本机追加件**（`git ls-files --error-unmatch` 报 `did not match any file(s) known to git`；
同目录的 `registry.py` 才是被跟踪的）。因此产品文件用 `git diff --no-index --numstat`
对比改动前备份，测试文件用行数差，两者都给出实际命令输出。

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/context_menus/game_assistant.py` | **+7 / −0**（`git diff --no-index --numstat pet/context_menus/game_assistant.py.bak-20261015-g7loop pet/context_menus/game_assistant.py` → `7  0`） | 在 `_add_commands()` 里 `_add_stop(sub)` 之后、`幻唐志` 块之前，插入 4 行注释 + 3 行 `_add(...)`：第一层新增「交给慢通道（跑全）」，args 与老入口逐字相同 |

改动原文（新增 7 行）：

```python
    _add_stop(sub)
    # 2026-10-14 秋殇反馈：菜单里找不到「交给慢通道」这个词 —— 它藏在两层子菜单里
    # （「幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色（跑全）」）。这里在命令区**第一层**再放一个
    # 同名入口：同一个 args（`loop --pick all`）、同一条账本、同一套闸门，
    # 不新造命令、也不复制实现（两处指向同一个 pet_actions 动作）。
    _add("交给慢通道（跑全）", ["loop", "--pick", "all"],
         "交给慢通道受理（= 幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色）：写指令账本 → 三道闸门 → "
         "唯一终态 → 气泡回传")
```

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_game_assistant_menu.py` | **+29 / −0**（225 → 254 行；同样未跟踪，按行数差计） | 新增 `test_loop_entry_found_by_its_name`：① 第一层必须有名字含「交给慢通道」的项；② trigger 它必须派发 `["loop","--pick","all"]`；③ 老入口 trigger 派发**同一个 args**（防"复制出第二套实现"） |

### 文档

| 文件 | 增删 | 意图 |
|---|---|---|
| `docs/PR-REPORT-G7-SLOW-CHANNEL-MENU-ENTRY-2026-10-15.md` | 新增 | 本报告（三份证据） |
| `docs/INDEX.md` | +1 行 | 「PR 报告存档」登记（新文档入场规则第 1 条，`tests/test_pr_report_discipline.py` 强制） |

### 未改动（看起来相关但故意没动）

- `pet/context_menus/registry.py`、`pet/menu_layout.py`：新项不是新的 action id，无需注册。
- `pet/context_menus/shared.py`：复用既有 `add_submenu`，未动。
- `tools/pet_actions.py`、`tools/pet_loop.py`、`tools/htz_daily.py`、`tools/input_lease.py`、
  `一键脚本/`、`server/`：**一行未动**（本次只改菜单定义）。
- 老项 `"0 全部角色（跑全）"` 的文案：保留（序号 0 = 跑全的约定还在）。
- **另有一条预先存在的 ruff 违规没有顺手修**（见第六节）：`game_assistant.py:152` 的
  `import sys as _sys` 残留 import —— 为遵守"只改菜单定义"的硬约束，留给 Lead 决定。

## 三、实现要点

- **位置**：`_add_commands()` 内、`_add_stop(sub)` 之后、`幻唐志` 块之前 ⇒ 第一层、紧挨急停项，
  打开「游戏助理」即可见（不改变急停的第一项地位）。
- **复用而非复制**：`_add(label, args, tip)` = `_add_on(sub, …)` → `_spawn_quiet(args, label)`
  → `Popen([_py_exe(), <game-copilot>/tools/pet_actions.py, "loop", "--pick", "all"])`。
  解释器仍走 `_py_exe()`（PyInstaller 打包后 `sys.executable` 是桌宠自己，拿它跑 py 会起第二只桌宠；
  这条坑 2026-10-05 已踩过，本改动沿用既有防线）。找不到 venv/tool 时该项**自动置灰**（既有逻辑）。
- **为什么"新增 + 保留"而不是"改文案"**：备选 ①「只把 `0 全部角色（跑全）` 改名为
  `交给慢通道（跑全）`」——名字对了，但**位置还在两层里**，秋殇仍要点两下，没解决"一眼能找到"；
  备选 ②「删掉老项只留新项」——会破坏老习惯与既有文档锚点。故取：第一层新增 + 老项保留。
- 两个入口指向**同一个** `args` ⇒ 同一条 `command_id` 账本、同一套闸门（急停/锁屏/受理锁/`real_ok`）、
  同一个唯一终态与审计；不存在"两条路行为不一致"的分叉面。

## 四、性能分析

**方法（可复现）**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv311\Scripts\python.exe D:\AI\_scratch\g7-menu-entry\measure_menu.py D:\AI\AIWORK\game-copilot\refs\dsh-pet-indesktop
# 脚本内容：QApplication(offscreen) → GA.add_game_assistant_menu(QMenu(), None) 构建 N=300 次，统计耗时与第一层动作数
```

**环境**：Windows 11 / Python **3.11.9**（`refs/dsh-pet-indesktop/.venv311`）/ PySide6（offscreen）/ N=300（另有预热 5 次）

| 指标 | 改前（基线） | 改后 | 差 |
|---|---|---|---|
| 菜单构建 median | 2861.6 µs | 2829.0 µs | −32.6 µs |
| mean | 3019.8 µs | 3020.6 µs | +0.8 µs |
| p95 | 3959.9 µs | 4165.4 µs | +205.5 µs |
| `sub.actions()` 第一层动作数 | 17 | 18 | +1 |

（改前/改后各另做一次独立采样：改前 2859.8 / 3025.9 / 4084.1 µs，改后同量级 ⇒ 上表的 p95 差属同进程抖动。）

**结论（逐条）**

1. **稳态开销**：无变化。构建耗时差落在同一进程 300 样本的抖动带内（p95 自身抖动 ±200 µs）；
   菜单只在**右键弹出时构建一次**，不在任何循环、定时器或渲染热路径上。
2. **新增路径成本与触发频率**：一个 `QAction`（`addAction` + `setToolTip` + `connect`），
   与既有 17 个同构项同价；触发频率 = 秋殇点它时（人工，天级几次）。
3. **新的系统调用 / 网络 / 磁盘 / 线程**：**没有**。构建期新项不读文件、不起进程；
   点击时才走既有 `_spawn_quiet`（`subprocess.Popen`，与老入口同一条路）；
   构建期的文件访问（`switches.json`、`htz_chars` 缓存）改动前后完全一致。
4. **内存 / 缓存增长**：一个 QAction（Qt 对象 + 一个闭包），随菜单对象一起回收；无新增缓存。

## 五、实机运行记录

**本机真实环境**（秋殇这台 Windows，非 CI、非 mock）：

1. **根因复现（改前）**——用真菜单构建探针（offscreen，改前文件）：
   `loop_entry=[]`、`first6=['AI 操作','慢通道思考','AI 操作笔记本','','AI 建议','AI 记录（我点哪儿）']`
   ⇒ 第一层**没有**任何名字含「交给慢通道」的项（秋殇的困扰在代码层复现）。
2. **改后**——同一条命令：
   ```
   build_us  n=300  median=2829.0  mean=3020.6  p95=4165.4  max=6023.8  sub_actions=18
   first6=['AI 操作', '慢通道思考', 'AI 操作笔记本', '', 'AI 建议', 'AI 记录（我点哪儿）']
   loop_entry=['交给慢通道（跑全）']
   ```
   ⇒ 开关区 7 项顺序未变，新入口出现在第一层。
3. **断言有效性（先红后绿，同一环境前后对照）**：把产品文件换回改前
   → `FAILED tests/test_game_assistant_menu.py::test_loop_entry_found_by_its_name (assert None is not None)`；
   换回改后 → `1 passed`。证明该用例能区分新旧实现，不是恒真断言。
4. **用户可见行为的确认（待秋殇）**：菜单在**桌宠启动时**构建
   （`add_game_assistant_menu` 只在右键菜单构造时调用）⇒ **必须重启桌宠**才会看到新项；
   此刻正在跑的桌宠实例（`pythonw -m pet`，PID 45764 / 65344）仍是**旧菜单**。
   *为什么不能自动确认*：确认它要重启秋殇正在使用的桌宠（会打断他当前使用），
   我不擅自动他的进程；改这一项也无所谓"点一下会改状态"，所以没有做任何真机点击。
5. **边界 / 失败路径（实机观察）**：`HOME/.venv` 或 `HOME/tools/pet_actions.py` 缺失时，
   `_add_on` 会把该项**置灰**并改写 tooltip（既有防线，新项同样走这条）；本机两者都在，
   所以实测是可用状态（新用例能 trigger 并派发到 `_spawn_quiet`，即证明未被置灰）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.venv311\Scripts\python.exe -m ruff check pet tests scripts` | **1 error（预先存在，非本次引入）**：`pet/context_menus/game_assistant.py:152 F401 'sys' imported but unused`（`_sync_all_switches()` 里的残留 import）。用 `git show`/备份原文喂 stdin 复验（`ruff check --stdin-filename pet/context_menus/game_assistant.py -`）得到**同一条** ⇒ 改前就红，且全仓 ruff 错误数改前=1、改后=1（本次改动**零新增**）。遵"只改菜单定义"未顺手删它。 |
| 聚焦 | `pytest -q tests/test_game_assistant_menu.py tests/test_pr_report_discipline.py` | **38 passed**（改前该两组为 37 passed + 1 个新用例红） |
| 受影响族复跑 | `pytest -q tests/test_game_assistant_menu.py` ×3 | `13 passed` ×3（0.74 / 0.69 / 0.67s） |
| 全量 | `pytest -q`（`QT_QPA_PLATFORM=offscreen`） | **3 failed, 2971 passed, 11 skipped** in 241.03s |
| 断言有效性 | 基线换回后只跑新用例 | 1 failed（见五-3） |

**全量那 3 条失败的归属（同环境前后对照，非本次引入）**

| 失败用例 | 基线（改前）单独跑 | 改后单独跑 | 归属 |
|---|---|---|---|
| `tests/test_architecture.py::test_window_py_line_budget` | failed | failed | 预先存在（本工作副本 `pet/window.py` 是用户自己的脏改动，行数预算跟着变） |
| `tests/test_proactive.py::TestVisionAndWatcherPhase2::test_capture_window_rect_coordinates_and_clamping` | failed | failed | 预先存在（`pet/window.py` / 视觉路径的脏改动） |
| `tests/test_session_end_ffmpeg_guard.py::test_control_group_spawns_normally_without_session_end` | 单独跑 3/3 passed | 单独跑 3/3 passed | **只在全量负载下红的既有 flake**（ffmpeg 会话结束路径，本改动不碰进程/线程） |

## 七、已知限制与后续

- 两处入口**并存是有意的**（老项保留序号 0 = 跑全的约定）；若秋殇希望只留一个，
  删掉那 3 行 `_add(...)` 即可，老路径不受影响。
- 本次**没有**做"点一次跑通全链路"的实机验证：那要真走一遍慢通道受理，受
  `data/pet_loop.json` 的 `real_ok` 授权闸门控制，属另一条线的验收，不在菜单改动范围。
- 老项 tooltip 里还写着"当前模拟档不碰游戏"——现在受理侧走的是真实执行器（受 `real_ok` 控制），
  这句措辞已过时；本次按"只改菜单定义"没动它，留待专门一轮统一文案。

## 八、风险与回滚

- **影响面**：「游戏助理」子菜单第一层多一项；无配置键、无落盘数据、无迁移、无新线程。
- **回滚**：
  ```powershell
  Copy-Item pet\context_menus\game_assistant.py.bak-20261015-g7loop pet\context_menus\game_assistant.py -Force
  ```
  备份路径 `pet/context_menus/game_assistant.py.bak-20261015-g7loop`，
  sha256 = `CF1828936E354CE880098E167465E2B60A27765798360AB46CCA3E829D7B4B3D`。
  再删本报告与 `docs/INDEX.md` 里那一行即可（改后文件 sha256 =
  `5A62D7A4CABFFD3BA3E40FF3FBEEB5E1014FAF95DEA95442B3D6B330AD387E54`）。
- 回滚后**需重启桌宠**才生效（菜单启动时构建）；无残留状态。

**秋殇重启桌宠后应看到**：右键桌宠 → 「游戏助理」→ 第一层（「停止快通道」下一行）出现
「**交给慢通道（跑全）**」，鼠标悬停提示"= 幻唐志 ▶ 跑修炼任务 ▶ 0 全部角色"；点它 =
原来的「0 全部角色（跑全）」（走账本 + 三道闸门 + 气泡回传）。
