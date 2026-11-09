# 阶段 A：Persisted Incident + Dry-run Recovery Decision

## 分支与保留边界

开发前 HEAD 为 `8f64590`（含 Stage 5a 可靠性测试），暂存区为空。
将既有未提交蓝绿原型完整保存为本地 checkpoint `9951332`，随后建立
`codex/persisted-incidents`。没有 push 或 merge。阶段 A 修改尚未提交。

原型 `RemediationRun/Event` 是一次性部署实验的数据，不改名、不删除，不作为正式 Incident。
保留 `a01_remediation`，新增单一后继 `a02_incidents`，没有第二条 migration head。
正式模型集中在 `app/incident_models.py`，通过现有 metadata 注册；不再建立第二套 Monitor/CheckResult。

## 检查来源、版本与消费顺序

- 正式 checker 默认 `manual`，Worker 显式写 `automatic`。HTTP 请求不能指定来源。
- 历史行迁移为 `legacy`、revision=0，保留原数据但不参加检测，不能推测它们属于自动检查。
- 新结果保存实际检查 URL、Monitor config_revision 和 source。历史不会因 URL 修改而改写。
- URL 或 is_active 发生实际变化才提升 revision；显示名称变化不提升。受管服务身份/授权策略改变也提升 revision。
- 每个 Monitor 按 CheckResult.id 升序消费，持久化 last_check_id。每批最多 500 条，后续批次继续。
- 仅当前 revision 的 automatic 记录参与计数；manual/legacy/旧版本只推进游标，不增加或清零连续计数。
- 同一 revision 的连续合格失败达到 3 条才开 Incident。配置变化清空失败和恢复计数；旧 Incident 以 superseded 结束，不能标为健康恢复。
- is_active=false 时不触发或验证恢复。重新启用提升版本，需要新版本的完整证据。

正式写入路径在 HTTP 检查前取得 Monitor 行锁，直到提交结果；API 配置修改、策略管理及检测也取得同一行锁。
这是有意增加的同目标串行化：避免 ID 较小的检查晚提交，而检测器已把游标推进到更大 ID。
顺序以数据库 ID 为准，不依赖客户端时间或只有秒级精度的 checked_at。

此保证以 PostgreSQL 默认 READ COMMITTED 和升级后的正式写入路径为前提。
旧版本进程或绕过接口直接写数据库不受保证；部署时应协调更新 API/worker/detector。
SQLite 的 FOR UPDATE 不提供并发证明。

## 状态流转

```text
无 Incident --3 次自动失败--> open
open --1 次自动成功--> recovering
recovering --失败--> open（恢复计数清零）
recovering --累计连续 3 次自动成功--> resolved
open/recovering --配置版本改变--> superseded
```

open 期间继续失败不会重复开事件；resolved 后新的 3 次失败可以开下一事件。
每次 trigger 保存三条 CheckResult 外键与完整证据快照；后续失败、成功、关闭和配置淘汰也有事件。
恢复是被动观测到的恢复，不代表系统执行过修复。没有 Docker restart、shell runbook 或执行器。

## 数据库幂等与并发

- Monitor 行锁序列化同一目标的检测；游标、计数、Incident、决策、事件在同一事务提交。
- 检测器异常回滚整个批次，重启可重读；提交成功后重读由游标跳过。
- PostgreSQL 部分唯一索引保证每个 Monitor 最多一个 open/recovering Incident。
- ManagedService 对 monitor_id 唯一，(project, service) 也唯一：第一版明确一个服务只映射一个主健康监控。
- 受管服务重配置会在同一事务中 supersede 未关闭事件，再变更映射。
- RecoveryDecision.incident_id 唯一，每 Incident 只做一次不可变 dry-run 决策；追加失败不会重复建议。
- 有事务回滚与数据库约束测试；PostgreSQL 真实进程竞争需执行下述验收，不能用 SQLite 通过替代。

## 授权、目标状态与冷却

普通 Monitor 没有 ManagedService 映射，决策为 denied/unmanaged_monitor。名字和 URL 永远不构成授权。
管理员通过本地 `app.service_policy` 建立映射，默认 restart_authorized=false；没有开放 HTTP 授权接口。

建议 restart 需要同时满足：显式授权、启用的当前版本、目标被管理员观察为 running、
观察时间不超过 120 秒且不在未来、最后一条触发检查不超过 120 秒、服务不在冷却期。
否则持久化明确拒绝原因。每个决策保存 mode、action、阈值、策略版本、revision、目标标识、
授权和观察快照。唯一的 action 值为 restart，但它只是建议字符串，不可执行。

running 是管理员声明，Stage A 不连接 Docker 验证容器身份；在阶段 B 不能把这个声明当作真实容器授权证明。
冷却期为 600 秒，从一次 recommended dry-run 决策开始，跨 Incident 保留；策略修改不清除冷却。
这属于“建议冷却”，不是动作次数或执行冷却；阶段 B 需要单独定义动作状态和执行授权。

一次决策是事件打开时的历史评估，不是可长期使用的执行令牌。历史 backlog 可能在同批次中开事件又观察到恢复；
保留当时的决策，但事件 resolved/superseded 后不得执行。阶段 B 必须重查当前事件、最新证据、授权、冷却与容器身份。
拒绝后不会因同一事件后续授权变化而原地改写决策；配置修改形成新 revision 并淘汰旧事件。

## 使用方式

先在 disposable 验收通过后，再考虑在正常开发数据库应用迁移。不要运行 downgrade 来处理正常数据。

```powershell
# 正常开发环境的协调升级；迁移在既有 migrate service 中执行。
docker compose up --build -d
# detector 是独立进程，仅消费数据库，默认每 5 秒扫描。
docker compose logs --tail=100 detector
# 手动 catch-up，幂等；不会发健康请求或执行修复。
docker compose run --rm --no-deps detector python -m app.incidents --once
```

管理员 dry-run 配置例子（替换真实 monitor-id / 仅限自己管理的目标）：

```powershell
# 默认拒绝，只登记映射。
docker compose run --rm --no-deps detector python -m app.service_policy --monitor-id 1 --project local-demo --service web
# 明确允许“建议 restart”，仍然没有执行能力；同时产生新配置版本。
docker compose run --rm --no-deps detector python -m app.service_policy --monitor-id 1 --project local-demo --service web --authorize-restart --observed-state running
# 60 秒 Worker 三次采样可能超过观察 TTL；可信管理员可刷新观察，不重置版本。
docker compose run --rm --no-deps detector python -m app.service_policy --monitor-id 1 --observation-only --observed-state running
```

没有自动 Docker 观察器；声明未知或过期应看到 denied，这是默认保守行为。
使用 psql 查看 `incidents`、`recovery_decisions`、`incident_events` 和 `detector_states`。
保留 CheckResult 的删除约束用于保护证据，暂未增加 Incident UI、HTTP API 或保留期清理功能。

## 独立 PostgreSQL + 真实 HTTP 验收

启动 Docker Desktop（Linux engine），在仓库根目录执行：

```powershell
docker version
.\scripts\verify-incidents.ps1
```

项目名随机生成，独立 audit_data / fixture_data volumes，不发布宿主机端口，
不启动或停止正常项目、Prometheus/Grafana，不执行恢复动作，不删除任何 volume。
验证容器会保留，脚本打印它的日志命令；正常 worker 日志和断言结果都在该容器日志里。

脚本内的真实验收顺序：

1. 拒绝非全新数据库；升级到 a01，插入 legacy Monitor/CheckResult。
2. 升级到 head，验证 legacy 标记并执行 alembic check；降到 a01 再升级，验证旧行不丢失。
3. HTTP fixture 返回真实 503；正式 checker 的手动失败记录不会开事件。
4. 启动 `python -m app.worker`，保持真实 60 秒周期，等待三次 automatic 失败。
5. 两个独立 detector 进程竞争检测，再重新启动两个进程重复消费；断言只有一个事件和决策。
6. PostgreSQL 写事务持有 Monitor 锁并写一条未提交结果，检测线程必须等待；提交后验证游标没有跳过这条记录。
7. 测试驱动把 fixture 切为 200（仅逆转测试故障，并非产品修复）；等待三个正式自动成功，断言同一事件 resolved。
8. 测试 worker 退出，保留数据库和验收容器。阶段 A 没有执行器、restart 或 socket 访问。

正常约需 5–6 分钟，取决于调度时刻。每个故障/恢复等待最多 220 秒；失败会以非零退出码结束。
仅针对脚本打印的测试项目执行不带 `-v` 的 down 可停止测试资源。不要用正常项目名，不要执行 down -v。

## 当前验证结果与未完成项

- **70 项 pytest 通过**（既有 48 项保留，新增 22 项）；SQLite/mock 测试覆盖来源、版本、连续性、默认拒绝、策略、
  观察/证据新鲜度、冷却、重复消费、事务回滚、约束和被动恢复。
- PostgreSQL 方言离线 migration SQL 生成通过；迁移链单 head；Compose 和 PowerShell 语法静态检查通过。
- **Docker Desktop 的 dockerDesktopLinuxEngine 管道不存在。本次没有运行 PostgreSQL migration、
  PostgreSQL 多进程竞争或真实 HTTP Docker 验收；验收脚本已提供，但这些结果均标为未验证。**
- SQLite create_all 和会话重建仅证明逻辑与持久化消费；不能证明 PostgreSQL 行锁及真实进程恢复。
- 没有认证后的远程策略管理、真实容器身份观察或自动恢复动作。这些是阶段 B 的前置设计工作。
- 不承诺恰好一次外部操作，也不宣称 worker 已能处理原评估中的所有进程/网络故障。
- 检测只按合格记录连续性计数；暂未加采样间隔断档规则。仅部署一个正式 Worker，双 Worker
  的重复自动采样可能使三次失败更早达到；Detector 本身可以并行且共享 PostgreSQL 锁。
- 一个批次回滚不影响已提交 CheckResult；持续数据库故障会使检测滞后，恢复后可 catch-up。
- Monitor 行锁跨 HTTP 操作持有，慢目标会延迟该 Monitor 的配置/检测；HTTP 超时仍是阶段超时。

进入阶段 B 前必须实际跑通此验收，并补齐：服务级显式授权主体、Docker 目标身份核验、
动作领取/冷却/不确定状态与人工核对流程。禁止把 recommended 直接当作执行授权。
