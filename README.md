# folioimpact

`folioimpact` 是一个完全离线的 Python 3.12 标准库 CLI，用来重放档案事故中的三类不确定性：

1. 见证只证明某组在区间内持有**一版或两版**封存文档；
2. 审阅副本只出现部分页，折叠摘要经证明等价后不增加独立段数；
3. 同组地点之间的交接只有在区间相交时才形成合法交换链。

所有 ID、文档、段和页都是自造符号；程序不读取也不包含任何原文数据。

## 快速开始

在仓库根目录执行：

```bash
python -m unittest discover -s tests -v

python -m folioimpact validate fixtures/case_replay.json
python -m folioimpact bound    fixtures/case_replay.json -o out/bound.json
python -m folioimpact mitigate fixtures/case_replay.json -o out/mitigate.json
python -m folioimpact crosscheck fixtures/case_replay.json -o out/crosscheck.json
python -m folioimpact all fixtures/case_replay.json -o out/all.json
```

Windows PowerShell/cmd 下也可使用：

```powershell
py -3.12 -m unittest discover -s tests -v
py -3.12 -m folioimpact bound fixtures\case_replay.json -o out\bound.json
```

需要以 Python isolated mode 运行时，可用仓库根启动器：

```bash
python -I run_folioimpact.py crosscheck fixtures/case_replay.json
```

所有 JSON 均以 UTF-8 读写，`ensure_ascii=False`，Windows 控制台会尽量重配为 UTF-8。CLI 成功返回 `0`；输入或模式错误输出一个 JSON 错误对象到 `stderr` 并返回 `2`。

## 交付的合成夹具

- [`fixtures/case_replay.json`](fixtures/case_replay.json)：完整主实例。包含同源折叠、直接审阅链、跨地点交接链、三个时间片、强制业务复核、保护上限、输入内安全对策，以及恰好 16 个缓解动作。
- [`fixtures/case_collision.json`](fixtures/case_collision.json)：无解实例。保护上限要求该敏感段组为 `0`，强制复核又要求世界中保证出现 `alpha`；优化器枚举全部 65,536 个动作子集后输出可复核的相撞世界。

主夹具的确定性预期包括：

- `ts_open_copy / sg_ledger`：最少 1、最多 2；最小世界选 `dv_closed_summary_v2`，最大世界选 `dv_open_ledger_v1`。
- `ts_south_review / sg_ledger`：最少 1、最多 2，证据链为 `w_north_holding -> ho_north_to_south -> rc_south_desk`。
- `ts_open_copy / sg_map`：最少 0、最多 1。
- `mitigate`：最小代价 3、两条动作，ID 为 `A01_safe_north_alternative` 与 `A04_shorten_north_holding`。
- 相撞夹具：`feasible=false`，最大泄露世界为 2 段，最小复核世界为 0 段且缺少保证段 `alpha`。

## CLI

```text
python -m folioimpact validate   CASE.json
python -m folioimpact bound      CASE.json [-o RESULT.json] [--no-countermeasures]
python -m folioimpact mitigate   CASE.json [-o PLAN.json]
python -m folioimpact crosscheck CASE.json [--max-vars 12] [-o CHECK.json]
python -m folioimpact all        CASE.json [-o ALL.json]
python -m folioimpact unittest  [-v]
```

- `bound`：对每个 `组 × 敏感段组 × 时间片` 输出最小/最大独立段数、两个边界的确定性版本分配和交换链，并评估输入中的 `countermeasures`。
- `mitigate`：枚举最多 16 个动作的全部 `2^N` 个子集，找最小代价可行计划。
- `crosscheck`：对小实例同时运行组件算法、完整全世界枚举和逐段真值表参考实现。
- `all`：一次输出 `bound`、`mitigate`、`crosscheck` 三部分。

---

## 输入格式：`folioimpact.case/v1`

顶层字段：

```json
{
  "schema": "folioimpact.case/v1",
  "case_id": "synthetic-case",
  "groups": [],
  "sensitive_groups": [],
  "independent_segments": [],
  "segment_versions": [],
  "document_versions": [],
  "pages": [],
  "time_slices": [],
  "witnesses": [],
  "handoffs": [],
  "review_copies": [],
  "mandatory_reviews": [],
  "protection_targets": [],
  "countermeasures": {},
  "mitigation_actions": []
}
```

区间统一为半开整数区间 `[start, end)`。坐标是任意精度 Python 整数，不按时间单位展开，因此 `10^9` 级跨度没有性能问题。

### 1. 组、敏感段和版本化段

```json
"groups": [
  {"id": "g_archive", "sites": ["north", "south"]}
],
"sensitive_groups": [
  {"id": "sg_ledger", "protection_cap": 1}
],
"independent_segments": [
  {"id": "alpha", "sensitive_group": "sg_ledger"},
  {"id": "alpha_fold_source", "sensitive_group": "sg_ledger"}
],
"segment_versions": [
  {"id": "sv_a1", "independent_segment": "alpha"},
  {"id": "sv_fold1", "independent_segment": "alpha_fold_source"}
]
```

`protection_cap` 可作为该敏感段组在所有组/时间片上的默认上限；也可用显式 `protection_targets` 覆盖或提供完整策略矩阵。

### 2. 封存文档版本

每份文档版本是多个版本化段的有序/去重组合：

```json
"document_versions": [
  {"id": "dv_v1", "segment_versions": ["sv_a1", "sv_b1", "sv_g1"]},
  {"id": "dv_v2", "segment_versions": ["sv_a2", "sv_fold1"]}
]
```

### 3. 页与同源折叠

```json
"pages": [
  {"id": "pg_a1", "segment_version": "sv_a1"},
  {"id": "pg_fold", "segment_version": "sv_fold1",
   "folded_summary_of": "pg_a1", "equivalence_proven": true}
]
```

当 `equivalence_proven=true` 时，`pg_fold` 与 `folded_summary_of` 指向的页合并其独立段身份。并查集允许传递折叠；最终规范 ID 取等价类中字典序最小者。只有规范独立段进入计数，所以折叠摘要不会新增独立段。已证明折叠不能跨越不同敏感段组。

### 4. 时间片

```json
"time_slices": [
  {"id": "ts_open", "interval": [1700000100, 1700000120]}
]
```

如果业务上的“时间片”是一个离散时刻，可写成 `[t, t+1)`。

### 5. 见证与可能世界

```json
"witnesses": [
  {
    "id": "w_north",
    "group": "g_archive",
    "site": "north",
    "interval": [1700000000, 1700001000],
    "alternatives": ["dv_v1", "dv_v2"],
    "variable": "x_edition"
  }
]
```

- `alternatives` 长度必须是 `1` 或 `2`。长度 `1` 是固定持有；长度 `2` 产生一个二选一变量。
- 省略 `variable` 时，变量 ID 等于见证 ID。
- 多个见证写同一个 `variable` 时共享同一个二选一选择；两个见证的第 `0/1` 个候选分别对齐。
- 一个完整世界给每个变量赋 `0/1`，固定见证始终取唯一版本。

变量按“可能显示的规范段集合是否相交”连接成歧义组件。每个组件最多允许 14 个二选一变量，即最多 `2^14 = 16384` 个局部世界；超出会返回输入错误。不同组件的可能独立段集合互不相交，因此边界可逐组件精确求出后相加。

### 6. 交接与审阅副本

```json
"handoffs": [
  {"id": "ho_ns", "group": "g_archive",
   "from_site": "north", "to_site": "south",
   "interval": [1700000200, 1700000300]}
],
"review_copies": [
  {"id": "rc_south", "group": "g_archive", "site": "south",
   "interval": [1700000250, 1700000400],
   "pages": ["pg_a1", "pg_fold"]}
]
```

合法链可以是：

```text
w_north -> rc_north                         （同地点直接复核）
w_north -> ho_ns -> rc_south                （一次交接）
w_a -> ho_ab -> w_b -> ho_bc -> rc_c        （多跳，经持有区间接续）
```

实现要求每个相邻节点的可用区间相交；因此交接区间必须与起点持有区间相交，同时也检查它和下一持有/审阅区间的接续重叠。时间片只要求审阅端点的可用区间与时间片相交；交接接触可以发生在该时间片之外，代表此前已合法移交。

多条链都可行时，确定性选择：

1. 交接次数最少；
2. 节点 ID 元组字典序最小；
3. 证据页 ID 字典序最小。

### 7. 强制业务复核与保护上限

```json
"mandatory_reviews": [
  {
    "id": "mr_north_min",
    "review_copy": "rc_north",
    "time_slice": "ts_open",
    "sensitive_group": "sg_ledger",
    "minimum_independent_segments": 1,
    "required_segments": ["alpha"]
  }
],
"protection_targets": [
  {"group": "g_archive", "sensitive_group": "sg_ledger",
   "time_slice": "ts_open", "cap": 1}
]
```

鲁棒方向是非对称的：

- 保护上限看所有世界的**最大值**：不能因为版本不确定就把可能泄露一概当作已公开，也不能忽略高泄露世界；
- 业务复核下限看所有世界的**最小值**：不能把只在部分世界出现的段当作必然保留；
- `required_segments` 必须在每个世界都可见，才算有保证。

`review_copy` 可省略；省略时要求应用于整个 `组 × 敏感段组 × 时间片` 聚合边界。提供时只评估该审阅副本作用域。

### 8. 输入内安全对策

```json
"countermeasures": {
  "selected_action_ids": ["A01_safe_alternative", "A04_trim_holding"]
}
```

`bound` 会用与 `mitigate` 相同的动作语义评估这些对策，并输出：

```json
"sufficient_in_all_legal_worlds": true
```

只有每个保护目标的 `maximum_visible <= cap` 才为 `true`。

---

## 16 个缓解动作

`mitigation_actions` 最多 16 个；主夹具提供恰好 16 个。每个动作都有唯一 ID、非负整数代价、种类和目标。

### `suspend_review`

暂时停止某个审阅副本：

```json
{"id": "A02", "kind": "suspend_review", "cost": 1,
 "target": "rc_north"}
```

可给可选 `pause_interval`；省略表示暂停整个审阅区间。多个暂停区间按并集从审阅区间中减去，因此支持“只在某段时间暂停”。

### `shorten_interval`

缩短见证持有、交接或审阅区间：

```json
{"id": "A04", "kind": "shorten_interval", "cost": 1,
 "target": "w_north", "interval": [1700000000, 1700000049]}
```

动作区间必须是原区间的子区间。多个同类动作作用于同一目标时取交集。缩短可能破坏区间相交，从而断开交换链。

### `replace_version`

推荐替代审阅版：

```json
{"id": "A01", "kind": "replace_version", "cost": 2,
 "target": "rc_north", "replacement_pages": ["pg_a1"]}
```

替代页是该动作认证后的推荐审阅内容，在动作生效且审阅区间仍与时间片相交时作为确定性信息边界，不再依赖未知见证版本。若两个 `replace_version` 动作同时指向同一审阅副本，该子集被视为冲突并跳过。

## 优化与平局规则

搜索全部 `2^N` 个子集，`N <= 16`：

1. 主目标：总代价最小；
2. 同代价：动作条数更少；
3. 仍相同：排序后的动作 ID 元组字典序更小。

可行性条件：

```text
对所有 protection_targets 和所有法律世界：visible_count <= cap
对所有 mandatory_reviews 和所有法律世界：min_visible >= required_minimum
并且每个 required_segment 在所有世界中均可见
```

搜索使用 Python 大整数作为“段 × 局部世界”的位平面，并用位切片计数器同时求所有世界的最小/最大值及确定性取到边界的世界编号，不逐个展开 `2^14` 个世界来评估每个动作子集。

若无解，输出：

- 枚举的子集数；
- 一个 `too_much_disclosure_world`：越过保护上限的最大泄露世界，含版本分配和交换链；
- 一个 `too_little_information_world`：低于强制复核边界的最小信息世界，同样含可复核证据。

## 交叉校验

`crosscheck` 命令和 unittest 中的小实例执行三种独立计算：

1. **组件/位平面算法**：生产路径；
2. **全世界参考**：枚举全部全局变量赋值并直接求并集；
3. **逐段参考**：为每个独立段建立跨世界可见性位真值表，再逐世界计数。

测试会比较三种方法的最小/最大边界以及每个世界的段掩码。`tests/test_mitigate.py` 还构造了恰好 14 个二选一变量的组件，并与 16,384 个全世界参考交叉校验；15 个变量会被模式/限制校验拒绝。

## 设计上的确定性

- 对象 ID 排序、变量排序、组件排序和页/链平局规则均固定；
- 最小边界平局时取最低世界编号；最大边界平局时也取最低世界编号；
- 输出不使用随机数、当前时间或网络；
- 同一输入重复运行得到字节级稳定的 JSON（除了你指定的输出路径外）。

## 目录结构

```text
pyproject.toml
run_folioimpact.py
folioimpact/
  __init__.py
  __main__.py
  cli.py          # argparse JSON CLI
  errors.py
  intervals.py    # 半开整数区间运算
  model.py        # UTF-8 JSON 校验、折叠并查集、变量/组件
  engine.py       # 计划状态、交换链图、位平面边界、详细证据
  bound.py        # bound 报告
  mitigation.py   # 16 动作枚举、可行性、相撞世界
  crosscheck.py   # 全世界与逐段参考
fixtures/
  case_replay.json
  case_collision.json
tests/
  test_bound.py
  test_mitigate.py
  test_cli.py
```
