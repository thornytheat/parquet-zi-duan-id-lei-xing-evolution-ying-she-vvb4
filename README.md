# schemaevo · Parquet 字段 ID 与类型演进映射

湖仓表治理小工具：上游提交 Parquet 表 schema 变更时，先算清「字段怎么变了、兼容级别是什么、迁移 SQL 按什么顺序执行、谁会读坏」。

## 能力

- 字段匹配：`field_id` 优先，无 id 时按名称精确匹配；改名、字段 ID 变更、新增、删除逐条标注依据。
- 类型提升矩阵：代码内显式表（int8/int16/int32/int64、float/double、decimal 精度、string→int、date→timestamp 等），表外类型对一律 `unknown`（不猜）。
- 兼容级别：按「旧写新读 / 新写旧读」双向判定，输出 `full` / `backward` / `forward` / `none`。
- 迁移计划：有序 `ALTER TABLE` 序列（改名 → 加列/默认值 → 回填 → 类型变更 → 删列最后）、需回填对象、读写方升级顺序；分区字段被删或改类型直接阻断。
- 列引用检查：数据文件清单里引用被删/被阻断列的 → `broken_consumers`；引用改名列的 → 需同步改名。
- 4 个页面：首页（场景 + 三组样例 + 演示步骤）、对比工作台、结果页、复核与导出（历史 + SQL 下载 + 矩阵说明）。
- 运行记录以 JSON 落在 `/app/data/runs/`（compose 命名卷），同一输入可反复重跑，历史可回看。

## 核心规则（纯函数模块）

| 模块 | 文件 | 规则 |
|---|---|---|
| 字段匹配 | `app/matching.py` | id 相同优先；id 同名称变 → renamed；名称同 id 变 → id_changed（人工确认）；无匹配 → added/removed |
| 类型矩阵 | `app/types.py` | 显式提升/阻断表；decimal 精度提升兼容、收窄阻断、标度变更 unknown |
| 兼容判定 | `app/compat.py` | 双向各自判定后合成级别；分区字段增删改类型双向阻断 |
| 迁移计划 | `app/migration.py` | 固定顺序出 ALTER，阻断项不产出 ALTER，删列最后 |
| 引用检查 | `app/consumers.py` | 逐文件逐列比对匹配结果，输出 broken / needs_update / unknown |
| 报表编排 | `app/report.py` | 汇总成可重放的运行记录 + 下载脚本 |

## 启动

```bash
docker compose up -d --build
curl -s http://127.0.0.1:18216/health
```

页面：<http://127.0.0.1:18216/>

## 演示步骤

1. 首页点「用样例会 B 执行对比」，工作台预填 `dwd_payment` 旧/新 schema 与数据文件清单。
2. 点「执行对比」→ 结果页：改名 1、新增 2、回填 1、`decimal(10,2)→(18,2)`，兼容级别 `backward`，ALTER 4 条。
3. 换成样例 C（`dwd_shipment`）→ 级别 `none`、3 条阻断原因、`broken_consumers` 若干、`can_execute=false`。
4. 到「复核与导出」看历史、下载 `*.sql`、查看完整矩阵；同一输入可重复执行。

样例：

| 样例 | 表 | 预期 |
|---|---|---|
| A | `dwd_orders` | `full`，0 条迁移 SQL |
| B | `dwd_payment` | `backward`，rename 1 / added 2 / 回填 1 / ALTER 4 |
| C | `dwd_shipment` | `none`，3 条阻断 + broken_consumers |

## API / 页面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 首页（场景、样例、演示步骤） |
| GET | `/workbench?sample=A|B|C` | 对比工作台（预填样例） |
| POST | `/api/compare` | `{sample}` 或 `{old,new,consumers}`；返回 `run_id`/`summary`/`redirect`；非法输入 400 |
| GET | `/result/{run_id}` | 结果页（匹配表、类型结论、兼容级别、迁移、broken_consumers） |
| GET | `/review` | 历史对比 + SQL 下载 + 矩阵说明 |
| GET | `/api/runs`、`/api/runs/{run_id}` | 运行记录 |
| GET | `/api/runs/{run_id}/migration.sql` | 下载迁移脚本 |
| GET | `/api/matrix` | 完整类型矩阵 |
| GET | `/api/samples/{key}` | 样例原文 |
| GET | `/health` | 健康检查 |

## 目录

```text
app/            main.py + 6 个纯函数模块 + templates/ + static/
sample_data/    a/b/c 三组 old/new/consumers.json + index.json
tests/          test_matching / test_types / test_compat / test_migration / test_api
data/           运行记录（本地直跑时生成，已 gitignore）
```

## 已知限制

- 只做 schema 级别的静态判定，不读取 Parquet 文件字节，也不校验数据文件真实 id 编码；`consumers.json` 里的列引用来自人工/采集清单。
- 类型矩阵是显式小表，未覆盖的合法类型对（如 `string→double`、`timestamp→date`）返回 `unknown`，需人工确认，不自动推断。
- `ALTER TABLE` 为通用 SQL 片段（Spark/Trino 语义），不连接具体引擎执行；回填动作只给建议 SQL 与对象清单。
- 运行记录保存最近 200 条，超出按修改时间裁剪；`data/` 被 gitignore，容器重建需通过命名卷保留。
- 无登录/权限，单机演示定位。
