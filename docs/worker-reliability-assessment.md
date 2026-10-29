# Stage 5a：Worker 可靠性评估

## 本次范围与证据

评估 worker/checker、SQLAlchemy session/engine、模型、Compose 和 API/worker 测试。
本阶段未改业务实现或部署策略；新增 5 项确定性故障测试，合计 **39 passed**。
测试使用 SQLite 和 HTTP/故障注入 mock，不等同于 PostgreSQL 断线或 OS 杀进程验证。
Docker CLI 存在，但本次 Docker Desktop Linux 引擎管道不存在，因此下述 Docker 实验尚未执行。

## 1. Worker 意外退出

**已有能力：**重新启动后立即查询 active monitors；历史在数据库里，不随进程丢失。
**不足：**Compose worker 没有 restart 策略，进程崩溃后不会自动恢复。
普通 Python Exception 在每项/每轮被捕获，不应把网站报错等同于进程崩溃；SIGKILL、OOM、解释器退出则不能被这些 catch 恢复。
`depends_on` 只控制启动顺序，不是运行期监督。指标 HTTP 服务存活也不能证明调度循环持续推进。
**建议最小改动：**下一阶段单独增加 `restart: unless-stopped` 并验证崩溃恢复和主动 stop 的差别；保留当前立即启动一轮语义。随后考虑最近完成一轮时间指标，避免仅以 `up` 判断 worker 健康。

## 2. PostgreSQL 暂时不可用后恢复

**已有能力：**发现阶段失败由 run_worker 捕获，按现有节奏进入下一轮；单项失败关闭独立 session，后续项不复用失败事务。SQLAlchemy 连接池通常会使识别为断线的连接失效，后续连接可重建，不应断言数据库重启就永久卡死。
**验证：**原测试验证事务失败隔离；新增测试第一次 session 创建抛 OperationalError，下一轮执行真实 run_cycle/checker 并在 SQLite 保存两条记录。
**不足：**没有显式 connect_timeout、SQL statement_timeout 或 pool_pre_ping。网络黑洞和查询阻塞不能保证在 60 秒内返回；下一轮重试以当前调用已返回为前提。旧连接首次使用可能先失败一次。停机期间没有待重试任务或补跑记录，恢复后只检查当前状态。
提交成功但确认包丢失时，客户端不能确定该行是否已经落库。新增 refresh 故障测试证明：commit 后 refresh 失败，历史和 counter 已存在，虽然外层会日志报 Check failed。不要简单对整个 run_check 自动重试，否则可能重复记录。
**建议最小改动：**先在真实 PostgreSQL 实验中测量恢复，再加有界连接/语句超时和 pool_pre_ping；pre_ping 不能保护已经开始的事务，也不能解决提交不确定性。优化日志区分“提交失败”和“提交后读取失败”，不急于引入重试队列。

## 3. 网站 Timeout / Connection Error

**已处理：**checker 捕获 TimeoutException 和 RequestError；保存 DOWN、空 status_code、错误类别和耗时，随后继续其他 monitor。只有提交成功才记录业务指标。
**验证：**已有 timeout 参数测试；新增 worker ConnectError 测试确认两个 active monitors 均保存失败结果。API 测试也覆盖这些错误。
**边界：**HTTPX 5 秒是网络阶段超时，不是整次操作的绝对 deadline；DNS、多个阶段、缓慢分段响应等不能用 5 秒推算精确停机上界。串行检查使后续 monitor 等待。若数据库同时不可用，HTTP 失败结果也可能无法持久化。
**建议：**现有常规错误处理保留；先测有界场景，不必增加自动重试（否则放大网站负载和周期时长）。确有严格延迟要求时再设计整体 deadline。

## 4. 同时启动两个 Worker

**已有能力：**单个进程的轮次不重叠。
**不足：**不同进程没有 claim、锁或调度唯一键；相同 monitor 可同时执行并各自提交。新增顺序执行两个独立周期的测试证明没有去重，但不是并发测试。
同一 Windows 主机启动两个 main 通常因 8001 端口占用使第二个失败；这只是端口冲突，不是分布式互斥。两个 Docker 容器有独立网络命名空间，可以同时监听 8001，因而仍会重复执行。
**建议最小改动：**当前明确只部署一个实例。若要防误启动，可用 PostgreSQL 会话级 advisory lock 作单 worker 主锁，并用专用连接维持生命周期；连接丢失后必须停止工作、重新获取锁才能继续。单纯对每轮加锁只能阻止重叠，不能阻止第二实例在解锁后紧接着再跑一轮。严格“每个时间窗恰好一次”需要额外持久化调度语义，不在本阶段实现。

## 5. 检查一半时终止

**已有能力：**SIGTERM/Ctrl+C 只设置 event；当前 run_check 可以完成提交，之后跳过剩余 monitor，等待状态可被唤醒。新增测试在真实 checker 的模拟 HTTP 阶段设置 stop，确认只保存当前项。
**不足：**这不是实际 OS 信号测试。Compose 只给 30 秒 grace period，超过后会强杀。SIGKILL 在提交前终止则未提交事务随连接关闭回滚，没有检查历史；提交后终止则历史保留。commit 与 counter/抓取之间存在窗口，数据库有记录但 Prometheus 未观测到是允许出现的。重启不会补回丢失的那次检查。
**建议：**明确采用 best-effort 周期检查而非恰好一次。先验证正常 SIGTERM 的收尾行为及强杀边界；需要更严格保证时才添加持久化执行状态。增加 grace period 本身不能解决无界阻塞。

## 隔离 Docker 故障实验方案（尚未执行）

不要在开发数据库上进行 stop/kill 实验。每次使用唯一项目名、新 volume 和空闲端口：

```powershell
$testProject = 'sm-reliability-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$oldApiPort = $env:API_PUBLISHED_PORT
$oldDbPort = $env:DB_PUBLISHED_PORT
$env:API_PUBLISHED_PORT = '18080'
$env:DB_PUBLISHED_PORT = '15480'
docker compose -p $testProject up --build -d db migrate api worker
$base = 'http://127.0.0.1:18080'
$m = Invoke-RestMethod -Method Post -Uri "$base/monitors" -ContentType 'application/json' -Body '{"name":"Reliability test","url":"http://api:8000/health"}'
# 等待启动就绪和至少一条自动检查，再保存时间、历史 ID 和日志作为基线。
Invoke-RestMethod "$base/monitors/$($m.id)/checks"
docker compose -p $testProject logs --timestamps worker
```

只运行这四个服务，不启动 Prometheus/Grafana，避免端口冲突。每个实验记录开始/恢复时间、容器 ID、退出码、历史 ID/checked_at、HTTP 错误和 worker 日志。历史数组先赋值再枚举有效 id，避免 Windows PowerShell 5.1 包装空数组陷阱。

| 故障注入 | 当前实现预期与验收标准 | 下一阶段改进后的目标 |
| --- | --- | --- |
| worker 启动稳定超过 10 秒后执行 `docker compose -p $testProject kill -s SIGKILL worker` | 观察超过一个周期仍退出、历史停止增加；`start worker` 后重新写入 | 加 restart 策略后在 90 秒内重新产生记录；主动 stop 不自动复活 |
| `docker compose -p $testProject stop db`，跨过至少一个周期后 `start db` | worker 仍存活、有 Cycle/Check failed 日志；DB ready 后 120 秒内再次写入（仅本地短检查的实验阈值，不是现有代码保证） | 有界超时后可明确恢复上界；不要求补齐停机历史 |
| 用本地受控 HTTP fixture：一个端口接受连接后保持静默超过 5 秒，另一个无监听端口 | timeout / ConnectError 保存 DOWN，后续健康 monitor 仍执行；不要依赖公网黑洞地址 | 现有功能应通过，无需改变业务行为 |
| `docker compose -p $testProject up -d --no-deps --scale worker=2 worker` | 两个容器日志都执行同一 monitor；可出现两条近时结果；每个容器 8001 都可启动 | 未来主锁方案只允许一实例执行；锁持有者退出后另一实例接管 |
| 在受控 fixture 已收到请求且尚未回复时 `stop -t 30 worker` | 正常超时/响应在宽限内完成时当前项保存，剩余项不执行；日志 Worker stopped | 保留此行为，测量收尾时长 |
| 同一阶段注入 `kill -s SIGKILL worker` | 提交前无新记录，重新 start 后可继续；允许缺失这次结果 | 明确 best-effort；不能声称无丢失 |

延迟 fixture 必须输出“已收到请求”作为同步点，强杀测试不能仅 sleep 猜测时机。提交后窗口可用测试包装器在 db.commit 返回后暂停并发出同步标记，再强杀；恢复后按原历史 ID 查询行仍存在。提交确认包丢失需要代理/网络故障注入，当前 mock 不证明此场景，列为后续专项验证。

清理仅停止实验项目，保留 volume 便于追查：

```powershell
docker compose -p $testProject down
$env:API_PUBLISHED_PORT = $oldApiPort
$env:DB_PUBLISHED_PORT = $oldDbPort
```

## 最小改进顺序

1. 先实际执行独立 PostgreSQL/进程故障实验，补齐证据。
2. 单独加入并验收 worker restart 策略。
3. 依据实测添加数据库超时和连接池预检查，改善错误阶段日志。
4. 如确需防双实例，再实现 PostgreSQL 主锁及失锁测试。
5. 任务持久化/补跑属于新的产品语义，不将 Redis/Celery 等引入本阶段。

## Stage 5b：真实故障实验尝试（2026-10-29，Asia/Tokyo）

### 前置检查与阻塞证据

分支：`feat/worker-reliability`。检查时已有未提交文件：README.md、tests/test_worker.py，以及尚未跟踪的本评估文档；全部保留。
已重新阅读此前评估及 compose.yaml，未修改 worker、checker 或 Compose 运行策略。

在允许访问宿主 Docker 的执行环境中实际运行：

```powershell
docker version --format '{{.Server.Version}}'
docker compose ls
```

两个命令均退出 1，返回同一错误：

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine;
check if the path is correct and if the daemon is running:
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

这说明此次无法访问 Docker Desktop Linux 引擎，不是 worker 故障实验结果；仅据此不能判断 Desktop 未启动、引擎尚未就绪还是其他本机配置问题。
没有创建实验项目/容器/volume，没有执行 stop、kill、down 或 down -v，没有触碰正常项目数据。本次没有容器日志证据可提供，以上为真实 CLI 连接失败证据。

### 本次结果矩阵

| 实验 | 注入前预期（来自 Stage 5a，非本次实测） | 实际结果 | 当前结论/后续最小建议 |
| --- | --- | --- | --- |
| Worker 意外终止 | 强杀后保持退出；手动启动恢复检查 | **未执行**：引擎不可连接 | 不能宣称已验证；实测后再决定加入 restart 策略 |
| PostgreSQL 中断与恢复 | worker 记录失败但不退出；连接恢复后后续周期继续写入 | **未执行**：引擎不可连接 | 不确定实际恢复时间和旧连接影响；测量后决定超时/pre_ping |
| 检查中停止 Worker | 已接收请求作为同步点，SIGTERM 后当前检查收尾，剩余项不执行 | **未执行**：引擎不可连接 | 不能以 Event mock 当作信号实测；需测退出码、耗时及历史记录 |

此前 39 项 pytest 结果属于 Stage 5a 的 SQLite/mock 测试，本次未将其作为 Docker 故障实验的证据，也未重复运行来替代实验。

### 引擎恢复后的隔离检查要求

1. 启动 Docker Desktop 并确认 Linux containers 引擎可用；先运行 `docker version` 和 `docker compose ls`，成功后才建立实验环境。
2. 使用唯一 project name，只启动 `db migrate api worker`。不启动 Prometheus/Grafana，从而不发布实验用的 9090/3000。
3. Project name 只隔离容器、默认网络及默认命名 volume，**不会隔离宿主机端口**。选择 API/DB 候选端口时同时检查正在运行的容器映射和宿主监听端口：

```powershell
docker ps --format 'table {{.Names}}\t{{.Ports}}'
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 18080,15480 }
```

4. 如果有占用，换空闲端口；不要停止占用端口的正常项目。启动前暂存并设置 `API_PUBLISHED_PORT` / `DB_PUBLISHED_PORT`，最终在 `finally` 恢复原值。端口检查仍有竞态，若启动报告 address already in use，停止注入故障、重新选端口，不把启动失败当作 worker 故障。
5. 启动后用 `docker compose -p $testProject ps -a` 和容器 Mounts 检查实际发布端口及 volume 名称确实属于新 project；等待迁移成功、API/db 健康及至少一条自动检查历史，才开始故障注入。
6. 每个实验只操作明确的 `-p $testProject`。结束时仅对该实验项目执行 `down`（不带 `-v`），保留独立数据库 volume 和证据。不删除正常项目的任何 volume。
7. 在正常 API/worker 容器内不要植入延迟端点。检查中停止实验应在隔离环境内使用临时受控监听进程和“请求已接收”的同步标记，不新增生产服务或修改业务代码；没有同步标记就不能声称是在检查中途终止。

**Stage 5b 状态：前置检查完成，三个真实故障实验均待 Docker Desktop 引擎恢复后执行。**
