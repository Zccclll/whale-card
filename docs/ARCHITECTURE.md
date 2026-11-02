# 架构

## 一句话

`card.db` 是一张卡的全部；`mind_server.py` 是把卡变成 HTTP 服务；
宿主（任何能发 HTTP 的 agent）每轮问它"现在该想起什么"，答完再把这一轮写回去。

```
宿主 agent ──POST /context──▶ mind_server ──▶ card2.recall()    ◀── 组装【我是谁】+【我记得的事】
           ◀── 上下文块 ─────┘                └─ identity / ctxweight / hormone / synapse
           ──POST /report───▶ mind_server ──▶ card2.remember()  ──▶ timeline 出生证 + 哈希链
```

## 表结构（SQLite 单文件）

| 表 | 作用 |
| --- | --- |
| `mem` | 记忆主表。`core` 主干、`subj/verb/obj` 三段、`w` 权重、`pool` 池、`region` 分区、`tier` 层级、`erase_stage` 侵蚀阶段、`history` 演化史、`valid_until` 有效期 |
| `detail` | 一条记忆拆出来的细节片段（可单独衰减、单独召回） |
| `core_vec` | 主干向量缓存（`core_vec_of` 懒加载，写完立刻 commit） |
| `meta` | 卡级状态：人设、名字、激素当前值、计数 |
| `identity` / `anchor` | 身份锚与外锚（封印校验用） |
| `mem_birth` | 出生证：每条记忆的 `(mid, hash, ts)`，哈希串成链 |
| `mem_tomb` | 删除台账：谁删的、为什么、删之前的哈希 |
| `synapse` | 突触边：`temporal`（时间相邻）/ `tag`（同标签）/ `co`（一起被想起）/ `ground`（实体共现） |
| `timeline` | 事件流水：`boot` / `alive` / `talk` / `mem` / `erase` / `consolidate` …，也是"链"的载体 |
| `skill` | 程序性记忆 + SM2 复习状态 |
| `dialogue` | 对话原文（短期层，会被压成记忆后归档） |

## 写入流程（`remember`）

1. 时间归一化（"昨天" → 绝对日期，用**说话那一刻**做参照，不是 now）
2. 切主干：`split_memory` 取核心句，其余进 `detail`
3. 算显著度：情绪词、实体数、惊讶度（与最近 200 条比 Jaccard ≥0.72 视为重复，给老条目续命）
4. 分区（事件/事实/偏好/闪光/自我/习惯）+ 分层（默认 mid）
5. 写 `mem` + `detail`，编码向量（有模型才编）
6. `timeline.birth_stamp()` 开出生证，接进哈希链

## 召回流程（`recall`）

1. 分舱预筛：按池/分区/时间窗先砍掉大部分行
2. 打分：字面 + 语义（cos）+ 权重 + 时间近因 + 分区加成
3. 突触联想：命中节点的电势按边扩散 3 跳（每跳 0.75 衰减），联想结果打 0.55 折并入，且不超过直接命中的一半
4. 取 top-k，更新 `recall_n` / `t_last_recall`（长期记忆的"验票"和 90 天保护期都靠这两个字段）

## 衰减与遗忘

- `weight_now(w0, pool, t_anchor, now, region, tier)`：指数衰减，不同池半衰期不同；`tier in CORE_TIERS` 直接返回 `w0`（底色不衰减）；长期池扣掉 90 天保护期。
- 每个 tick：
  - 短期池到期 → 海马体打分决定"合并进中期"还是"丢"
  - 细节片段先衰减，掉光再动主干
  - 权重 < `ERASE_W_FLOOR` 后开始侵蚀：**擦动词 → 擦宾语 → 删整条**，每步都是概率性的
  - 删除必须走 `_forget_row()`：先记台账，再删
- 处境类记忆到期 → 自动降级成普通记忆（`situation_expire`）

## 身份与"她是不是她"

- `identity.py`：人设、名字、头像，写入即封印（`sealed=1`），之后改要显式强行。
- `anchor.py`：把身份的哈希存到卡外面的文件里 —— 卡被别处打开时，也能验"这还是那张卡"。
- `cardslot.py`：插卡时把宿主的 `AGENTS.md` 换成通道规则（"你不是她本人，先问卡"），拔卡时按字节还原并核对 sha256。**这一步是为了防止"人设被宿主 prompt 顶掉"**。

## 服务接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/context` | 给一句话，拿回该想起的上下文块（含【我是谁】） |
| POST | `/report` | 回写这一轮（用户说了什么、她答了什么） |
| GET | `/health` | 体检：条数、链、出生证、锚、天数、语义维度 |
| GET | `/status` | 细状态：激素、心境、池子、召回统计 |
| GET | `/console` | 网页控制台 |
| GET | `/graph` | 星云图数据 |
| GET | `/timeline` | 时间线尾部 |
| GET | `/syslive` | 实时 CPU / 内存 / 磁盘（2 秒轮询用） |
| POST | `/emb/remodel` | 换向量维度全量重编码（换模型时用） |

## 为什么是这些约束

- **单文件**：搬家只要拷一个文件，减少"导出/导入"这类会丢东西的环节。
- **纯 numpy**：省掉 torch 的 2 GB 和 CUDA 版本地狱；代价是推理慢一点（对话级延迟无感）。
- **WAL + busy_timeout=30s**：控制台 / 桥 / 服务 / 快照会同时开同一个卡。**读路径绝不留写事务**——这条踩过：检索缓存忘 commit，别人一写就永久 locked。
- **维度由卡决定**：卡里多数向量是 768 维，就自动选 768 维模型，不靠环境变量猜；混维度时明说可用比例，不假装 100%。
