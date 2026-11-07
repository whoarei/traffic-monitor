# 在线升级与失败恢复

适用版本：VPN Traffic 1.5.1。

安装格式继续使用 deb。发布源为公开的 `whoarei/traffic-monitor` GitHub Release；服务器使用 HTTPS 下载，不保存 GitHub 访问令牌。发布流程仍不自动操作服务器，安装由管理员从页面或命令行触发。

## 网页操作

1. 打开侧栏“系统升级”，点击“检查更新”。检查会获取最新正式 Release 及签名清单，校验系统和数据库兼容性。
2. 页面显示当前版本、候选版本、变更说明和签名验证结果。只有更新通过检查且版本更高时，安装按钮才可用。
3. 点击升级并确认短暂维护。系统创建唯一任务，下载文件、验证、保存备份、安装并检查健康状态。
4. 任务日志和阶段进度约每 3 秒刷新。关闭弹窗或浏览器不取消任务；重新打开可查看结果。
5. 成功后点击“刷新页面使用新版”。失败时页面显示保留当前版本、已恢复旧版本或需要管理员处理。

![在线升级界面，版本与数据为隔离测试夹具](images/system-update.png)

截图中的候选版本仅用于界面测试，不代表实际可用版本。安装前的检查与文件下载不停止现有服务。维护阶段暂停连接管理修改，网页、采集器和配额管理器会短暂重启；Xray 是否需要重启取决于该版本的配置变更及当时的身份状态。

## 发布签名

每个可在线安装的 Release 除既有附件外，增加：

- `update-manifest.json`：格式版本、固定仓库、稳定渠道、目标版本、最低可升级版本、签发/过期时间、安装包信息、数据库兼容范围和变更说明。
- `update-manifest.sig`：对清单原始字节的 Ed25519 签名。

GitHub Actions 的发布 job 使用 `VPN_TRAFFIC_SIGNING_KEY` Secret 签名，并确认其公钥与仓库中的 `packaging/update-signing.pub` 一致。构建和 Pull Request 测试不接触发布私钥。私钥临时文件以 600 权限创建，签名完成或失败后清理，不进入 dist、源码包或安装包。

客户端使用首次安装时固定的 `/etc/vpn-traffic/update-signing.pub` 验签，拒绝未知格式、旧版本安装请求、过期或异常时间的清单、已见过的更旧签发记录、非固定仓库地址，以及不匹配的包名、架构、大小或 SHA256。下载跳转仅允许固定的 GitHub HTTPS 主机，不接受网页传入下载地址或命令。

本阶段使用单个固定签名公钥；不自动在线轮换信任根。更换公钥需由管理员通过受信任方式单独完成。清单默认有效一年，过期后需要发布有效清单或新版本；已经开始的受信任安装任务可按创建时的信任时间完成中断恢复。

## 进程与接口

| 组件 | 职责 |
| --- | --- |
| 普通权限 Web | 保留原登录、同源和 CSRF 校验，提交检查或版本号/请求 ID；不执行 apt。 |
| `vpn-traffic-updater.service` | root 升级代理，固定操作集合，通过 Unix 套接字校验调用者 UID。 |
| `vpn-traffic-upgrade-<任务ID>.service` | 每任务独立 systemd 单元，使用任务目录中的固定 Python 代码和公钥副本执行安装/恢复。 |
| `127.0.0.1:9082/status` | 仅提供只读状态，由原 HTTPS Nginx `/traffic/api/updates/status` 精确路由代理并要求 Basic 登录。 |

网页重启不会停止升级代理或任务单元。安装或回退业务包不会删除任务代码及独立服务运行副本。代理重启或主机重启时，读取持久化任务状态：已安装并健康的新版本可完成任务，其余已进入安装阶段的任务按恢复策略处理。多次异常中断会停止自动尝试并报告人工处理。

| 外部接口（相对 `/traffic/`） | 约束 |
| --- | --- |
| `GET api/updates/status` | 认证后的只读状态、当前版本、候选版本、最近任务和日志；不主动访问 GitHub。 |
| `POST api/updates/check` | 同源、CSRF、JSON；获取并验证正式 Release 的清单。 |
| `POST api/updates/start` | 只接受目标版本和 `request_id`，且目标必须匹配已信任清单；重复请求返回同一任务。 |

安装任务串行执行；遇到 apt/dpkg 锁时有界等待。升级阶段用独立维护标记阻止网页和管理器的用户修改，配额检查保留到进入安装准备；恢复或安装完成后解除维护状态。只读查询和升级状态不受管理写锁影响。

## 备份与回退规则

安装前先确认有可信的当前版本 deb。随后暂停配额管理器的配置写入、请求采集器 checkpoint，使用 SQLite 在线备份保存一致性数据库，并保存固定范围的账户、登录、Xray、Nginx、订阅和服务集成配置。

签名、下载、兼容检查或恢复包准备失败发生在安装前，保留现有代码和数据；已经暂停的应用服务会恢复。进入安装阶段后，检查系统服务、包管理状态、应用版本、统计/管理健康状态和数据库完整性。

健康检查失败时，仅在清单允许回退且数据库格式仍与安装前一致时，恢复配置并重新安装可信旧包。**自动回退不恢复旧数据库副本**，因此安装窗口中已经记账的流量和周期变化不会退回备份时间点。

遇到不兼容的数据结构、损坏数据库或旧版本健康检查失败时，状态为 `recovery_required`，维护标记保留。管理员应先检查日志和备份，修复条件后重新执行恢复。需要人工恢复数据库时，必须另行核对备份后新增流量和额度变化。

## 文件与权限

| 路径 | 用途与权限 |
| --- | --- |
| `/etc/vpn-traffic/updater.json` | 固定发布源与运行配置，root / 600。 |
| `/etc/vpn-traffic/update-signing.pub` | 固定发布公钥，root / 644；不含私钥。 |
| `/var/lib/vpn-traffic-updater/` | 根状态目录，root / 700。 |
| `runtime/` | 独立升级代理的持久运行副本。 |
| `jobs/<任务ID>/` | 固定任务代码、签名清单、包、备份、状态和有界日志。 |
| `cache/` | 已验证的已安装版本恢复包及校验凭据。 |
| `/run/vpn-traffic-updater/control.sock` | root:vpn-traffic / 660，调用者 UID 校验。 |
| `/run/vpn-traffic-updater/maintenance` | 维护状态标记，应用用户只读。 |
| `/etc/systemd/system/vpn-traffic-updater.service` | 持久服务配置，业务包回退到旧版时保留。 |
| `/usr/local/sbin/vpn-traffic-updater` | root 管理入口，指向持久运行副本。 |

任务备份包含登录和服务端材料，按私人资料保护。恢复包与任务记录当前保留，不自动清理；磁盘不足时拒绝新安装。完整部署备份已包含升级器的状态、信任配置和运行副本。卸载业务包时停用升级服务，状态与备份继续保留。

## 首次迁移与命令行

首次安装升级能力需要一次受信任的部署。对于此前没有签名清单的版本，应保留与当前已安装文件匹配的原始 deb，由管理员提供可信 SHA256 缓存恢复包：

```sh
sudo vpn-traffic-updater seed --package /path/to/current-installed.deb --sha256 <可信SHA256>
sudo vpn-traffic-updater check
sudo vpn-traffic-updater start --version 1.5.1
sudo vpn-traffic-updater status
```

`seed` 会检查包名、版本、架构、SHA256、已安装文件清单及文件散列。已经带签名清单的手动安装版本，在准备下一次升级时可自动下载和核对当前版本的恢复包；若文件被改动、签名过期或校验失败，需要管理员核对。

```sh
systemctl status vpn-traffic-updater --no-pager
journalctl -u vpn-traffic-updater -n 50 --no-pager
systemctl status vpn-traffic-upgrade-<任务ID> --no-pager
sudo vpn-traffic-updater recover --job <任务ID>
```

`recover` 仅重试处于人工处理状态的已有任务，不允许指定其他包或执行命令。网页业务服务异常时仍可通过此命令行读取状态。

## 验证范围

Python 测试使用真实 Ed25519 签名和 SQLite，并对 apt/systemd 操作进行故障注入，覆盖篡改、过期、签发记录回退、下载损坏、重复与并发请求、安装/健康失败、保留新增用量的回退、进程中断、数据库不兼容、权限及 HTTP CSRF。浏览器回归使用隔离内存 API，覆盖检查、错误显示、后台进度、关闭后重开、日志、成功和回退结果，以及桌面/手机布局。

生产部署另外核对发布附件的签名与散列、完整备份、安装任务结果、服务状态、登录保护、历史与额度保留；不在真实账户上故意制造安装失败或回退数据库。
