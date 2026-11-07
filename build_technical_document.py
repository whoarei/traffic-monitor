"""Generate a self-contained, host-specific HTML technical design after deployment QA."""
from datetime import datetime,timedelta,timezone
from html import escape
from pathlib import Path


def build(out,summary,verification,package_sha256,system_metrics=None):
    esc=escape
    version=summary['version']
    server=summary['server']
    metrics=system_metrics or verification['resourceSnapshot']
    generated=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    def size(value):
        for unit in ('B','KiB','MiB','GiB','TiB'):
            if value<1024 or unit=='TiB':return f'{value:.2f} {unit}'
            value/=1024
    def table(headers,rows):
        return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+esc(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'
    nav=[('overview','目标与部署'),('architecture','整体架构'),('history','小时历史与连接筛选'),('resources','系统资源指标'),('api','接口与交互'),('data','存储、权限与兼容'),('operations','部署与运维'),('verification','验证结果与边界')]
    overview=table(['项目','已部署状态'],[
        ('应用版本','VPN Traffic '+version),('服务器',server),('查询入口',f'https://{server}:8443/traffic/'),
        ('安装包',f'vpn-traffic_{version}_all.deb，Architecture: all'),('账户数量',len(summary['devices'])),
        ('时区','Asia/Shanghai（北京时间，UTC+8）'),('系统重置计划',f'每月 {summary["schedule"]["reset_day"]} 日 {summary["schedule"]["reset_time"]}，所有账户共用'),
        ('安装包 SHA256',package_sha256)])
    resources=table(['指标','计算口径','采集方式'],[
        ('CPU 占用率','相邻采样的 (总 ticks 增量−空闲 ticks 增量) / 总 ticks 增量 ×100%；空闲包含 idle 与 iowait。','读取 /proc/stat 首行；只计前 8 项，避免 guest 再次计入 user/nice。'),
        ('内存占用率','(MemTotal−MemAvailable) / MemTotal ×100%；不把可回收缓存直接视为已用内存。','读取 /proc/meminfo；旧内核无 MemAvailable 时回退到 free+buffers+cached。'),
        ('磁盘占用率','根目录所在分区的 used / total ×100%，不汇总其他挂载盘。','Python shutil.disk_usage("/")；与 df 的保留块口径可能略有差异。'),
        ('新鲜度','采样超过 20 秒或时间异常时 available=false，页面显示 — 和“暂不可用”。','采集器约每 5 秒写入，浏览器可见时每 5 秒读取。')])
    snapshot=table(['文档生成时的指标快照','数值（非实时）'],[
        ('CPU',str(metrics['cpu_percent'])+'%'),('内存',f'{metrics["memory_percent"]}% · {size(metrics["memory_used"])} / {size(metrics["memory_total"])}'),
        ('根分区磁盘',f'{metrics["disk_percent"]}% · {size(metrics["disk_used"])} / {size(metrics["disk_total"])}')])
    api=table(['接口','用途与约束'],[
        ('GET /api/summary','连接汇总、额度、统一重置计划和采集状态。'),
        ('GET /api/history?view=hourly&device=<id>','最近 24 小时的小时历史；device 为空表示全部。未知连接返回 400。'),
        ('GET /api/history?view=daily&from=YYYY-MM-DD&to=YYYY-MM-DD&device=<id>','按北京时间自然日查询，日期顺序及最大跨度受校验。'),
        ('GET /api/export.csv（同上参数）','导出当前连接、范围及粒度；小时 CSV 的未采集数值留空。'),
        ('GET /api/system','最近的 CPU、内存、磁盘指标及 sampled_at、available。'),
        ('GET /api/health','检查流量采集与管理服务就绪；资源新鲜度另由 /api/system 表达。'),
        ('POST /api/settings、/api/links/...','既有统一周期、连接管理与临时额度操作；仅登录后同源、通过 CSRF 校验的请求可调用。')])
    storage=table(['数据','位置 / 保留策略'],[
        ('累计、每日汇总、账户额度、统一重置计划','/var/lib/vpn-traffic/traffic.sqlite3；累计和每日汇总长期保留。'),
        ('30 秒采样','samples(timestamp,email,up,down,seconds)，保留 30 天；samples_time 与 samples_identity_time 支持时间和连接过滤。'),
        ('资源指标','meta.system_info，仅保留最新 JSON；没有新增资源时序历史表。'),
        ('小时历史','查询时从 samples 流式聚合，不增加第二份小时账本，不反写历史用量。'),
        ('Web 静态资源','/usr/share/vpn-traffic/web/，随 deb 更新。'),
        ('应用和服务','/usr/lib/vpn-traffic/；vpn-traffic-collector、vpn-traffic-manager、vpn-traffic-web 三项 systemd 服务。')])
    body=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>VPN Traffic {esc(version)} 技术方案</title><style>
    :root{{--ink:#173642;--muted:#71848e;--line:#dfe8eb;--teal:#087e78;--navy:#153d4b}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth;scroll-padding-top:24px}}body{{margin:0;background:#f4f7f8;color:var(--ink);font:15px/1.9 'Segoe UI','Microsoft YaHei',sans-serif}}.layout{{max-width:1290px;margin:auto;display:grid;grid-template-columns:205px minmax(0,1fr);gap:30px;padding:35px 28px 60px}}aside{{position:sticky;top:28px;align-self:start}}.brand{{font-weight:750;letter-spacing:.03em;font-size:18px}}.kicker{{font-size:10px;letter-spacing:.15em;color:var(--teal);font-weight:700}}nav{{margin-top:25px}}nav a{{display:block;font-size:12px;padding:8px 12px;color:#74848d;text-decoration:none;border-left:2px solid transparent}}nav a:hover{{color:var(--teal);border-color:var(--teal);background:#eaf4ef}}.sidebar-note{{border-top:1px solid var(--line);padding:18px 10px;margin-top:24px;font-size:11px;color:var(--muted)}}header,section{{background:white;border:1px solid var(--line);border-radius:12px;padding:28px 30px;margin-bottom:20px;min-width:0}}header{{background:linear-gradient(120deg,#fff,#edf6f2);padding:34px 32px}}h1{{font-size:32px;line-height:1.4;letter-spacing:-.03em;margin:12px 0 16px}}h2{{font-size:22px;line-height:1.5;margin:0 0 16px;letter-spacing:-.02em}}h2 span{{font:12px Consolas,monospace;color:#9aaaae;margin-right:10px}}h3{{font-size:16px;margin:20px 0 7px}}p{{margin:9px 0 15px}}a{{color:var(--teal);text-underline-offset:3px}}.muted{{color:var(--muted);font-size:12px}}.chips{{display:flex;flex-wrap:wrap;gap:7px;margin-top:20px}}.chip{{padding:5px 11px;border:1px solid #cfe3db;border-radius:20px;font-size:11px;background:#fff;color:#49746c}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:8px;margin:17px 0}}table{{width:100%;border-collapse:collapse;font-size:12px;min-width:420px}}th,td{{padding:11px 13px;border-bottom:1px solid #e7edef;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{background:#f5f8f8;color:#69818a;font-size:11px}}tr:last-child td{{border-bottom:0}}td:first-child{{min-width:135px}}code,pre{{font-family:Consolas,'Cascadia Code',monospace}}code{{font-size:12px;overflow-wrap:anywhere}}pre{{background:var(--navy);color:#e7f1f4;padding:17px 20px;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;line-height:1.8}}ul,ol{{padding-left:23px}}li{{margin:7px 0}}.callout{{background:#edf7f3;border-left:3px solid #4f9e88;border-radius:0 7px 7px 0;padding:13px 17px;font-size:13px;margin:18px 0}}.flow{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#f7faf9;border:1px dashed #d2e2dc;padding:18px;border-radius:9px;margin:14px 0}}.node{{background:#fff;border:1px solid #d8e5e4;border-radius:7px;padding:9px 12px;font-size:12px;text-align:center}}.node small{{display:block;color:#8a9ba3;font-size:10px}}.arrow{{color:#90aaa2}}.footer{{font-size:11px;color:#8c9ca4;padding:12px 3px}}@media(max-width:850px){{.layout{{display:block;padding:20px 15px 35px}}aside{{position:static;margin-bottom:20px}}nav{{display:flex;overflow:auto;gap:5px;margin-top:12px}}nav a{{white-space:nowrap;border:1px solid var(--line);border-radius:20px;padding:5px 10px;font-size:10px}}.sidebar-note{{display:none}}header,section{{padding:23px 20px}}h1{{font-size:27px}}h2{{font-size:20px}}}}@media print{{body{{background:white;font-size:10pt}}.layout{{display:block;padding:0}}aside{{display:none}}header,section{{border-radius:0;break-inside:auto;padding:18px}}table{{font-size:9pt}}pre{{background:#f4f6f7;color:#183b47}}.flow{{break-inside:avoid}}}}
    </style></head><body><div class="layout"><aside><div class="brand">VPN / ENGINEERING</div><p class="kicker">TECHNICAL DESIGN</p><nav>{''.join('<a href="#'+i+'">'+esc(t)+'</a>' for i,t in nav)}</nav><div class="sidebar-note">部署服务器<br><code>{esc(server)}</code><p>生成于北京时间<br>{generated}</p>离线 HTML · 无外部脚本或字体</div></aside><main>
    <header><span class="kicker">DEPLOYED DESIGN / VERSION {esc(version)}</span><h1>VPN 流量监控技术方案</h1><p>最近 24 小时的小时视图、单连接历史查询，以及服务器资源占用展示。本文记录已部署实现和本次验证范围。</p><div class="chips"><span class="chip">北京时间汇总</span><span class="chip">30 秒流量采集</span><span class="chip">5 秒资源采集</span><span class="chip">独立连接查询</span><span class="chip">已部署并验证</span></div></header>
    <section id="overview"><h2><span>01</span>目标与部署状态</h2><p>在现有 VPN Traffic 上增强历史可读性与服务器状态可见性。连接身份、额度、临时流量和统一重置计划沿用现有数据，历史查询为只读操作。</p>{overview}<p><a href="https://{esc(server)}:8443/traffic/">打开已部署管理页面 ↗</a>。登录信息通过独立私人文件交付，本文不包含密码、UUID、订阅令牌或服务端私钥。</p></section>
    <section id="architecture"><h2><span>02</span>整体架构与数据流</h2><div class="flow"><div class="node">浏览器<small>页面、图表、CSV</small></div><span class="arrow">→ HTTPS →</span><div class="node">Nginx :8443<small>登录校验 /traffic/</small></div><span class="arrow">→</span><div class="node">Web :9081<small>普通用户、数据目录只读</small></div><span class="arrow">→ 只读 →</span><div class="node">SQLite<small>历史与最新资源值</small></div></div><div class="flow"><div class="node">采集器<small>vpn-traffic 用户</small></div><span class="arrow">←</span><div class="node">Xray StatsService<small>127.0.0.1:10085</small></div><span class="arrow">＋</span><div class="node">procfs / 根分区<small>CPU、内存、磁盘</small></div><span class="arrow">→</span><div class="node">事务写入<small>增量账本 / 最新指标</small></div></div><p>采集器、Web 和管理器分别由 systemd 管理。流量采集每 30 秒执行一次，资源采集与流量任务共用事件循环，约每 5 秒执行；查询资源无需为每次网页请求阻塞采样。</p><p>已有特权管理器只在本地 Unix 套接字提供限定的连接与额度操作。网页以普通用户运行，管理修改通过限定接口转交，历史与资源读取不经特权操作。</p></section>
    <section id="history"><h2><span>03</span>小时历史与单连接查询</h2><h3>统计窗口</h3><p>“近 1 天”严格使用服务器当前时间往前 86,400 秒的窗口，并按北京时间整点切分。首尾小时可能只有部分时段，因此通常是 25 个区间；恰好在整点查询时为 24 个。所有流量数值均为字节，显示时按 1024 换算。</p><h3>采样到小时的分摊</h3><p>每条采样记录包含结束时间、时间跨度、身份标识和上传/下载增量。跨整点或窗口边缘时，以采样区间内的时间比例分配字节；使用累计比例的整数差值分配，完整区间内保留总字节数。重启后零跨度的首个计数归入其采样时刻所在小时。</p><pre>区间分配字节 = floor(累计字节 × 区间右边界比例)
             − floor(累计字节 × 区间左边界比例)

先裁剪到最近 24 小时窗口，再按北京时间整点切分。</pre><div class="callout">小时分布是基于采样的估计，无法还原采样间隔内每一秒的真实传输节奏。没有任何采样覆盖的整段小时显示为 —；CSV 对应数值留空，不伪装为已确认的零流量。</div><h3>查询与交互</h3><p>历史区域新增独立连接下拉框。连接行的“历史”按钮会选择该身份并滚动到历史区域，不改变上方总览范围；上方范围选择仍可同步历史筛选以兼容原使用方式。“近 7 天”“近 30 天”和自定义日期继续按天查询。CSV 沿用当前连接、时间范围及小时/天粒度。</p><p>小时汇总采用流式读取，不一次性把全部采样装入内存；最多为每个连接构造约 25 个结果区间。图表合并所选连接，表格保留每条连接明细，并分页展示。</p></section>
    <section id="resources"><h2><span>04</span>服务器资源指标</h2>{resources}{snapshot}<p>桌面资源卡位于左侧栏底部，包含百分比与用量进度条；手机布局将同一组指标移到页面底部。浏览器后台标签暂停主动轮询，回到页面后继续读取。指标读取失败显示 —，不会用 0% 掩盖缺失数据。</p></section>
    <section id="api"><h2><span>05</span>接口与访问约束</h2>{api}<p>外部统一经现有受信任 HTTPS 和 Basic 登录访问。Xray 统计接口、Web 后端仅监听回环地址；新增资源和历史接口同样受登录保护。查询使用参数化 SQL，连接标识必须存在，未知视图或无效日期返回 400。CSV 对可能被表格软件视为公式的名称加前缀处理。</p></section>
    <section id="data"><h2><span>06</span>存储、权限与现有功能兼容</h2>{storage}<p>数据库保留 schema 3，本版只增加身份/时间复合索引和最新资源键值。采集增量、累计与每日汇总在同一事务内提交；Web 以只读连接和查询事务读取一致快照。SQLite 使用回滚日志模式，避免只读挂载依赖可写 WAL 共享内存文件。</p><p>基础月额度、临时额度和系统统一重置规则继续由原管理器执行。临时额度仍在系统统一重置时失效；历史查询不会清零已用量，也不会重置配额。所有历史与资源接口均不输出连接凭据。</p></section>
    <section id="operations"><h2><span>07</span>部署、备份与日常维护</h2><p>升级前创建完整备份并记录连接、额度和 Xray PID；安装后检查三项服务、健康接口、小时汇总及网页交互。本次验证确认 Xray PID 未变化，新增查询能力没有重启 VPN 转发进程。</p><pre>sha256sum -c vpn-traffic_{esc(version)}_all.deb.sha256
sudo apt install ./vpn-traffic_{esc(version)}_all.deb

systemctl status vpn-traffic-collector vpn-traffic-manager vpn-traffic-web --no-pager
curl -fsS http://127.0.0.1:9081/api/health
curl -fsS http://127.0.0.1:9081/api/system
sudo vpn-traffic status
sudo /root/xray-deployment/backup.sh</pre><p>现有备份脚本包含一致性的 SQLite 在线备份、应用和服务配置。备份包含管理凭据和服务端材料，只在受保护目录保存。恢复前先备份当前状态；恢复旧数据库会回退用量与配额状态，需核对后再操作。</p><p>服务异常先查看对应 journal；仅资源异常时检查采集器对 <code>/proc/stat</code>、<code>/proc/meminfo</code> 和根分区的可读性。小时数据不足时检查 samples 保留期限与采集状态，不把缺失数据补成零。</p></section>
    <section id="verification"><h2><span>08</span>验证结果与边界</h2><ul><li>37 项单元测试通过，覆盖采集去重、周期与临时额度、跨小时与窗口裁剪、未采集时段、资源口径及过期处理。</li><li>部署后在数据库一致性副本上核对小时总量、单身份隔离及查询不修改账本。</li><li>已核对现有额度、临时额度、统一计划和历史保留；Xray 进程未重启。</li><li>真实浏览器验证默认小时视图、独立筛选、行内历史入口、小时 CSV、按天/自定义切换和无效日期处理。</li><li>资源接口认证、百分比范围、桌面左下角位置和 390×844 手机布局通过检查。</li></ul><p>限制：采样明细保留 30 天，不能重建更早的小时分布；日汇总继续长期保存。资源卡只显示最新状态，未增加资源历史曲线。计数属于 Xray 代理流量，不等于供应商网络账单。已有额度检查仍是 2 秒轮询，恢复或停用连接的既有行为仍可能触发短暂重连。</p><p><a href="vpn-traffic-history-verification.json">服务器验收记录</a> · <a href="历史与资源网页验证记录.json">网页验收记录</a> · <a href="流量统计与查询说明.html">使用与维护说明</a></p></section>
    <div class="footer">生成于 {generated}（北京时间）。本文在部署验证完成后生成，为离线技术说明；其中资源数值和账户数量均为生成时快照。</div>
    </main></div></body></html>'''
    target=Path(out)/'VPN流量监控技术方案.html'
    target.write_text(body,encoding='utf-8')
    return target
