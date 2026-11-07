# VPN Traffic 技术方案

适用版本：1.4.0 · 更新日期：2026-11-07

本文说明 `traffic-monitor` 的整体实现、统计口径、月度额度、接口、部署和维护方法。配套的 [离线 HTML 版本](technical-design.html) 包含目录、配色切换及打印 / 保存 PDF 功能。本文依据仓库源码整理；原服务器的账号、实时状态和私人验收附件保存在交付工作区。

## 目标与适用范围

项目为已有 Xray VLESS + REALITY + Vision 部署增加按连接身份统计和管理的能力：

- 每条连接的上传、下载、累计、今日和自然月用量。
- 最近 24 小时的小时历史，以及按天、按连接查询和 CSV 导出。
- 新增连接、调整月度额度、手工启停、轮换或撤销凭据、查看私人在线订阅。
- 全系统共用的月度重置日期与时间，以及额度用尽后的临时加额恢复。
- Web 页面左下角的 CPU、内存和根分区磁盘占用；手机布局显示在页面底部。
- 深色控制台、额度监测和超额提醒；侧栏选中项不使用左侧亮色竖线，手机连接记录重排为卡片。

运行环境为使用 systemd 的 Debian / Ubuntu、Python 3.10 及以上版本和系统 `tzdata`。应用使用 Python 标准库、SQLite、原生 JavaScript 和 CSS，没有运行时 Python 第三方依赖。Debian 安装包为 `Architecture: all`。

安装前需已有 `/usr/local/bin/xray`、`/usr/local/etc/xray/config.json`、`xray.service`、`/root/xray-deployment/manifest.json`、`/root/xray-deployment/backup.sh`、`/usr/local/sbin/xray-subscription` 和配置好 HTTPS 8443 的订阅站点。本包依赖这些部署约定；迁移到其他 Xray 管理系统前需要适配集成层。每条需要独立统计的连接必须使用唯一的 Xray `email`。

## 整体架构与职责

查询链路：浏览器 → HTTPS Nginx `/traffic/` → Web `127.0.0.1:9081` → SQLite 只读查询。

采集链路：采集器 → Xray StatsService `127.0.0.1:10085` → 计算增量 → SQLite 事务写入。资源指标来自 `/proc/stat`、`/proc/meminfo` 和根分区。

管理链路：浏览器 → 同源及 CSRF 校验 → Web → 本地 Unix 套接字 → 特权管理器 → 更新 Xray 配置、身份清单和订阅。

| 组件 | 代码 | 职责与权限 |
| --- | --- | --- |
| 流量采集器 | `app/collector.py` | `vpn-traffic` 用户；每 30 秒读取流量，每约 5 秒采集资源；提供停止前 checkpoint。 |
| 数据层 | `app/store.py` | 增量账本、周期、历史聚合、资源快照、迁移和查询一致性。 |
| Web 服务 | `app/server.py` | `vpn-traffic` 用户；数据目录只读；静态页面、JSON API、CSV 及受限管理代理。 |
| 特权管理器 | `app/manager.py` | root；每约 2 秒检查配额；执行限定的连接、计划和临时流量操作。 |
| 管理客户端 | `app/manager_client.py` | JSON 行协议，连接 `/run/vpn-traffic-manager/control.sock`。 |
| 系统集成 | `app/integration.py` | 配置统计 API、Nginx、账户权限、订阅保留路由、备份和卸载恢复。 |
| 资源采样 | `app/system_info.py` | 读取 Linux 系统指标，避免每次网页请求都阻塞采样。 |
| CLI 与页面 | `app/main.py`、`web/` | 维护命令及浏览器管理界面。 |

三个应用进程分别由 `vpn-traffic-collector`、`vpn-traffic-web`、`vpn-traffic-manager` systemd 服务管理。SQLite 写入由采集器和管理器的事务串行协调；Web 使用只读连接。服务通过 `ProtectSystem`、目录读写白名单、`NoNewPrivileges` 和内存限制约束运行范围。

## 身份与流量账本

统计使用 Xray 的 `user>>>email>>>traffic>>>uplink/downlink` 计数。上传表示客户端发往服务器，下载表示服务器返回客户端。VLESS 链接和该身份的在线订阅共享同一身份；多人共用同一身份时只得到合计流量。

采集器保存每个身份上次的原始计数和进程代际。代际由系统启动 ID、Xray PID 和进程启动时间组合；采集前后会检查代际，防止跨重启读取混合结果。

```text
同一代际且当前计数 >= 上次计数：增量 = 当前计数 - 上次计数
代际变化或计数下降：             增量 = 当前计数
```

原始计数、累计值、本期用量、日汇总和采样明细在同一事务提交。重复采集相同读数不会重复累计。系统时钟回退时推迟采样，避免重复写入错误时间段。

正常 Xray 停止前，systemd 的 `ExecStop` 会调用 `vpn-traffic checkpoint --quiet`，让采集器保存尾段计数；采集器不可用时不会阻止 Xray 停止。进程突然崩溃或服务器掉电时，可能丢失上次成功采集之后的流量。

累计从启用统计时开始，不补算此前用量。速率为最近采样间隔的平均字节 / 秒。计数属于代理流量，不能直接作为服务商网络账单。

## 月度额度与系统统一重置

每个身份独立设置基础月额度和手工启用状态，全部身份使用同一份 `reset_schedule`。时间按 `Asia/Shanghai` 执行；日期可选每月 1–31 日和 `HH:MM`。不存在的日期按当月月末执行，后续月份仍保留原设置日。

```text
U = period_up + period_down       本期已用
Q = quota                        基础月额度；0 表示不限额
B = bonus                        本期临时额度
有效额度 = Q + B                 仅对有限额身份适用
可用流量 = max(0, Q + B - U)
```

| 条件 | 转发状态 |
| --- | --- |
| 手工停用 `enabled = false` | 停用，原因是 `disabled`。 |
| 已启用，且 `Q > 0`、`U >= Q + B` | 停用，原因是 `quota`。 |
| 已启用，且不限额或尚有可用流量 | 可用。 |

修改系统计划只调整计划和下次执行时间，保留所有身份本期已用量、基础额度及临时额度。系统重置时统一清零本期上传、下载和临时额度，保留基础月额度、累计和历史；手工停用的身份仍停用。

“立即重置本期用量”只清零指定身份的本期用量，保留临时额度和全局重置时间。页面“本月流量”按自然月统计，“本期已用”按全局计划统计，两者可能不同。

管理器每约 2 秒读取当前计数，结合已落库用量判断是否超额，避免等待 30 秒普通采样后才停用。触及额度时先 checkpoint，再应用配置。该机制可能超出一个检查间隔及缓冲中的传输量，不是逐字节硬上限。

## 临时加额与连接管理

临时加额以“增加指定可用量”为语义，先补齐已经超出的部分，再增加申请量。设本次增加量为 `A`：

```text
新的临时额度 B' = max(B, U - Q) + A
```

例如基础额度为 50 GiB，已经使用 51 GiB，临时增加 5 GiB 后，临时额度为 6 GiB、有效额度为 56 GiB，可继续使用 5 GiB。操作保留本期已用量、累计和历史，不修改基础月额度。

因超额停用的身份在获得可用额度后恢复；手工停用的身份不自动恢复。临时额度在下次全系统重置时失效。不限额身份不接受临时加额。`request_id` 与 `allowance_operations` 记录保证同一加额请求重试时不会重复增加流量，参数冲突会拒绝。

新增连接生成独立 UUID 和 Short ID，创建 VLESS 分享链接及私人在线订阅。凭据轮换沿用身份标识和历史，旧凭据失效。撤销后保留统计历史。管理器先检查候选配置，再更新配置和身份清单；必要时保留受限权限快照，Xray 重启失败时尝试恢复原配置和原清单。

为了中断已建立的超额连接，身份加入、启停、轮换和撤销等改变 Xray 配置的操作会短暂重启 Xray；其他连接需要重连。只改额度或计划且未改变转发状态时不重启。停用身份的订阅暂时不可用，恢复后沿用原地址。

## 小时历史、连接筛选与导出

页面默认展示严格滚动的最近 86,400 秒，按北京时间整点切分。首尾小时通常不完整，因此一般为 25 个区间；恰好整点查询时为 24 个。近 7 天、近 30 天及自定义日期按自然日聚合。

小时历史从 30 秒采样明细即时生成。每条明细包含结束时间、跨度和上传 / 下载增量；先裁剪到查询窗口，再按各小时的时间重叠比例分配，采用累计比例的整数差值，保证完整区间内字节守恒。

```text
分配字节 = floor(样本字节 × 右边界累计比例)
         - floor(样本字节 × 左边界累计比例)
```

零跨度样本归入其时间戳所在小时。没有任何采样覆盖的小时标记 `observed = false`，页面显示“—”，CSV 数值留空。部分覆盖的小时只包含实际记录到的采样用量，不外推缺失流量。小时分布是基于采样的估计，不能还原采样间隔内每秒的真实速率。

历史区域有独立连接筛选，连接行的“历史”按钮会选中该身份并定位历史区域，不改变总览筛选。总览筛选仍可同步历史筛选。CSV 沿用当前连接、时间范围和小时 / 天粒度，使用 UTF-8 BOM，并转义可能被表格软件视为公式的名称。

历史查询采用参数化 SQL，校验身份和日期范围；日查询的结束日不得早于开始日，首尾日期差不得超过 3660 天。小时查询流式读取样本，每个身份构造约 25 个区间；表格分页展示，图表合并当前选中身份。

### 控制台呈现

1.4.0 将流量趋势放在连接列表上方，下载使用青色实线、上传使用蓝色虚线，并保留图例和单位。没有采样覆盖的小时会断开折线；不会为了连续曲线补入零值。点击图中区间或通过键盘聚焦，可读取该时段的上传、下载或“未采集”状态。

历史明细默认收起，可展开查看全部记录和分页；连接行的历史入口自动展开明细。日期输入在按天或自定义范围下显示，小时视图继续禁用日期输入。CSV 沿用原有接口与参数。

额度面板优先展示到额连接，连接列表提供临时加额入口；加额弹窗按最新已知用量估算操作后的可用流量，最终结果由管理器 checkpoint 和事务处理决定。手机上每条连接重排为卡片，全部原有统计字段及管理操作仍可访问。页面使用本地 SVG 图标和系统字体，继续遵守既有 CSP，无需放宽脚本或样式来源。

## 服务器资源展示

| 指标 | 计算口径 | 来源 |
| --- | --- | --- |
| CPU 占用率 | `(总 ticks 增量 - 空闲 ticks 增量) / 总 ticks 增量 × 100%`；空闲包括 idle 和 iowait。 | `/proc/stat` 前 8 项，避免重复计入 guest。 |
| 内存占用率 | `(MemTotal - MemAvailable) / MemTotal × 100%`。 | `/proc/meminfo`；无 MemAvailable 时回退到 free + buffers + cached。 |
| 磁盘占用率 | 根分区 `used / total × 100%`。 | `shutil.disk_usage("/")`；不合并其他挂载点。 |

采集器约每 5 秒写入最新指标，浏览器在页面可见时约每 5 秒读取。快照超过 20 秒或时间异常时 `available = false`，页面显示不可用，不将缺失数据显示成 0%。CPU 的首次采样使用短间隔获取差值。

资源卡在桌面侧栏底部显示，手机移到页面底部。当前只保存最新资源快照，没有 CPU、内存或磁盘的历史曲线。

## 数据模型与保留策略

数据库为 `/var/lib/vpn-traffic/traffic.sqlite3`，schema 版本为 3。数据库使用回滚日志模式和完整同步；Web 的只读事务提供查询快照，避免依赖只读挂载下无法创建的 WAL 共享文件。

| 表 | 关键内容 | 保留规则 |
| --- | --- | --- |
| `devices` | `email`、名称、转发状态、累计、原始计数、代际、采样时间、速率。 | 累计及历史身份保留。 |
| `daily` | `(day, email)` 主键及上传、下载日汇总。 | 长期保留。 |
| `samples` | 时间戳、身份、上传 / 下载增量和采样跨度。 | 约 30 天；定期清理。 |
| `limits` | 基础额度、临时额度、本期上传 / 下载、手工启用和停用原因。 | 当前周期状态持久化。 |
| `reset_schedule` | `id = 1` 的全局计划、周期起点和下次重置时间。 | 系统唯一计划。 |
| `allowance_operations` | 请求标识、身份、临时加额参数。 | 当前实现未设置定期清理。 |
| `events` | Xray 重启及管理事件。 | 约一年；定期清理。 |
| `meta` | 启用时间、采集 / 管理健康状态、错误和最新资源 JSON。 | 状态键覆盖更新。 |

`samples_time` 和 `samples_identity_time` 索引支持时间及单身份查询。小时历史查询不新增小时账本，也不修改配额。旧版独立重置设置迁移到全局计划；若旧设置全部一致则沿用，否则采用默认每月 1 日 00:00，既有额度和已用量保留。

## HTTP API 与访问控制

下表路径相对于 `/traffic/`，例如外部 `/traffic/api/summary` 转发到后端 `/api/summary`。

| 方法与路径 | 用途及主要参数 |
| --- | --- |
| `GET /api/summary` | 连接汇总、额度、全局计划和采集状态。 |
| `GET /api/history?view=hourly&device=<id>` | 最近 24 小时；省略 device 表示全部。 |
| `GET /api/history?view=daily&from=YYYY-MM-DD&to=YYYY-MM-DD&device=<id>` | 按天查询；省略 view 默认 daily。 |
| `GET /api/export.csv` | 与历史查询共用参数和统计口径。 |
| `GET /api/system` | 最新资源值、采样时间和 available 状态。 |
| `GET /api/health` | 流量采集及管理器健康；异常返回 503，资源新鲜度单独查询。 |
| `GET /api/session` | 获取管理请求需要的 CSRF token。 |
| `GET /api/settings` | 查询系统月度重置计划。 |
| `POST /api/settings` | `reset_day`、`reset_time`；更改系统计划。 |
| `POST /api/links` | 新建连接；`label`、`quota`、`enabled`、`request_id`。 |
| `POST /api/links/<id>/update` | 修改名称、额度或手工启用状态。 |
| `POST /api/links/<id>/bonus` | `amount`、`request_id`；临时增加可用流量。 |
| `POST /api/links/<id>/reset` | 清零该身份本期用量。 |
| `POST /api/links/<id>/rotate` | 轮换连接凭据。 |
| `POST /api/links/<id>/revoke` | 撤销连接。 |
| `GET /api/links/<id>/sharing` | 管理员主动查看 VLESS 及私人订阅链接。 |

额度和流量字段使用整数字节，页面及 CLI 可按 GiB 换算，`1 GiB = 1024³ B`。身份级请求中的独立重置日期或时间被拒绝。常见错误包括输入无效 400、未登录 401、同源或 CSRF 失败 403、路径不存在 404、后端暂不可用 503。

外部请求通过现有 HTTPS 和 Nginx Basic 登录认证。后端只监听回环地址；管理 POST 还校验精确 Origin、`X-VPN-CSRF`、JSON 类型与 16 KiB 请求体上限。Nginx 不向后端传递 Basic Authorization，关闭此位置的访问日志。响应带 CSP、nosniff、禁止嵌入和禁止缓存等约束。

特权操作仅经过 Unix 套接字，权限为 `root:vpn-traffic / 660`，管理器使用 `SO_PEERCRED` 校验调用者为 root 或应用 UID。连接客户端只在发送前对套接字尚未就绪进行重试，不自动重发已经发送的修改。

当前为单管理员模型，没有账户自助门户或多管理员角色隔离。汇总和历史接口不返回 UUID、订阅令牌或私钥；分享接口会向已登录管理员返回所选连接凭据，应按私人资料保存。

## 打包、部署与维护

### 构建和安装

```sh
python3 -m unittest discover -s tests -v
python3 build.py --output dist
dpkg-deb --info dist/vpn-traffic_1.4.0_all.deb
cd dist
sha256sum -c vpn-traffic_1.4.0_all.deb.sha256
sudo apt install ./vpn-traffic_1.4.0_all.deb
sudo vpn-traffic web-password
```

安装脚本创建应用用户、检查现有部署、生成或保留登录信息、接入统计 API 和网页路由、验证服务单元并启动三个服务。首次启用统计配置可能重启 Xray；升级是否重启取决于 Xray 配置是否发生变化。升级沿用数据库和登录密码。安装前配置快照位于 `/var/backups/vpn-traffic/before-*`。

网页地址为 `https://<服务器IP>:8443/traffic/`。登录信息在 `/etc/vpn-traffic/web-access.json`，root / 600；密码散列位于 `/etc/vpn-traffic/htpasswd`，root:www-data / 640。常规配置位于 `/etc/vpn-traffic/config.json`，数据库由 vpn-traffic 所有、权限 600。真实密码和部署清单不进入 Git。

### 日常命令

```sh
sudo vpn-traffic status
sudo vpn-traffic show
sudo vpn-traffic schedule-update --reset-day 15 --reset-time 09:30
sudo vpn-traffic link-create --label "家人手机" --quota-gib 20
sudo vpn-traffic link-update --device link-xxx --quota-gib 50
sudo vpn-traffic link-bonus --device link-xxx --amount-gib 5
sudo vpn-traffic history --from 2026-11-01 --to 2026-11-07 --device link-xxx
sudo vpn-traffic export --from 2026-11-01 --to 2026-11-07 --output /root/traffic.csv
systemctl status vpn-traffic-collector vpn-traffic-manager vpn-traffic-web --no-pager
journalctl -u vpn-traffic-collector -u vpn-traffic-manager -u vpn-traffic-web -n 50 --no-pager
curl -fsS http://127.0.0.1:9081/api/health
```

CLI 的 `history` 和 `export` 当前使用按天查询；小时视图通过网页或 HTTP API 查询。CLI 中查看密码和分享链接的输出含凭据，按私人资料处理。

### 备份、恢复和卸载

```sh
sudo vpn-traffic backup --output /var/backups/vpn-traffic/traffic.sqlite3
sudo /root/xray-deployment/backup.sh
```

第一条使用 SQLite 在线备份获得一致性副本；第二条为已有完整部署备份，集成后也包含一致性数据库、应用、配置和服务单元。完整归档含登录信息和服务端材料，存放在受保护目录。

恢复前先备份当前状态，安装相同或更新版本的 deb，停止三个应用服务，再恢复可信数据库并修正所有者和权限。不得在服务运行时直接替换数据库；恢复历史备份会回退本期用量和额度状态，需要核对。操作示例见项目 README。

`sudo apt remove vpn-traffic` 停止服务、撤除本包网页路由及统计集成；数据库、登录信息和备份保留，包括 purge 时。仅恢复仍与本包设置一致的 Xray 字段，避免覆盖后续人工修改。

### GitHub CI 与版本发布

应用及安装包版本统一由 `app/version.py` 定义。普通提交和 Pull Request 自动进行语法检查、单元测试、deb 构建及附件校验；推送与代码版本相同的 `vMAJOR.MINOR.PATCH` 标签时，检查全部通过后自动发布 GitHub Release。附件包括 deb、SHA256、当前提交的源码归档及技术方案的 Markdown / HTML 版本。

正式发布先准备草稿，上传全部附件后再公开；已发布的同名版本不会覆盖。分支构建仅有仓库读取权限，标签发布 job 使用内置令牌获得 Release 所需写权限，不使用服务器凭据，也不自动部署。发布步骤与重试方法见 [CI 与版本发布](releasing.md)。

## 验证依据与实现边界

仓库的 37 项 `unittest` 在 Ubuntu 24.04 环境通过，覆盖计数去重、重启保留、午夜分摊、全局周期、临时额度及幂等、管理校验、小时窗口、单身份过滤、未采集区间、CSV 和资源指标。1.4.0 Debian 安装包已成功构建。

CI 另增加 4 项发布校验测试，检查标签一致性、版本格式及错误标签的命令行拒绝行为；完整测试集共 41 项。

1.4.0 新增 `tests/qa_dashboard.cjs` 浏览器回归，以本地内存 API 夹具在原有 CSP 下运行，不访问真实 VPN。覆盖移除侧栏亮色边、小时/天/自定义范围、独立连接筛选、CSV、采样缺口、分页、新增与分享、复制、编辑与本期重置、临时加额恢复、手工停用保留、统一计划、CSRF 更新、资源过期、恶意名称文本化和空状态；检查 1440、1024、768、390、320 像素宽度及手机弹窗。Playwright 仅为开发及 CI 依赖，不引入运行时 Node.js 依赖。

原部署验收还验证过 HTTPS 认证、网页新增及额度管理、小时与天视图、独立历史筛选、小时 CSV、桌面资源卡和 390×844 手机布局。私人交付目录保留当时的验收记录与截图；项目文档不将当时的资源快照作为当前服务器状态。

`tests/test_*.py` 在临时数据库及模拟对象上运行，`qa_dashboard.cjs` 使用独立内存夹具。其余 `verify_*.py`、旧版 `qa_*.cjs` 和清理脚本为分版本的现场验收工具，部分修改连接状态，运行前应检查目标、版本和配置；旧版浏览器脚本的 Playwright 路径也需按环境调整。

主要边界包括：采样明细约保留 30 天、更早小时分布无法恢复；资源仅保留最新值；突发故障可能漏记尾段流量；额度轮询可能产生少量超额；改变转发状态会短暂重启 Xray。当前实现面向单台服务器，部署路径、8443 HTTPS 入口及 10085 / 9081 回环端口具有约定，不包含跨服务器聚合或任意面板的自动适配。

维护技术方案时，应结合 `app/`、`packaging/` 和测试同步更新 Markdown 与离线 HTML；部署现场的账号、数据库和验收快照继续保存在仓库外。
