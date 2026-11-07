"""Create host-specific private delivery files without putting secrets in the deb."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
from html import escape
import hashlib
import json
import re
from pathlib import Path
import shutil
import ssl
import tarfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parent
VERSION='1.3.0'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',required=True)
    args=parser.parse_args()
    project=ROOT.parent
    work=project/'.work'/args.server
    out=project/'deliverables'/args.server/'traffic-monitor'/VERSION
    out.mkdir(parents=True,exist_ok=True)
    access=json.loads((work/'traffic-web-access.json').read_text())
    assert access['url']=='https://'+args.server+':8443/traffic/'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    authorization=base64.b64encode((access['username']+':'+access['password']).encode()).decode()
    with opener.open(urllib.request.Request(access['url']+'api/summary',headers={'Authorization':'Basic '+authorization}),timeout=20) as response:
        summary=json.load(response)
    with opener.open(urllib.request.Request(access['url']+'api/system',headers={'Authorization':'Basic '+authorization}),timeout=20) as response:
        system_metrics=json.load(response)
    assert summary['healthy']
    (out/'网页登录信息.txt').write_text('VPN 流量查询页面\n\n地址：'+access['url']+'\n用户名：'+access['username']+'\n密码：'+access['password']+'\n\n使用浏览器弹出的登录框登录。\n此文件含管理凭据，请私人保存。\n服务器查看密码：sudo vpn-traffic web-password\n重置密码：sudo vpn-traffic web-password --reset\n',encoding='utf-8')
    shutil.copy2(work/'traffic-qa-1.3.0/history-web-qa.json',out/'web页面验证记录.json')
    shutil.copy2(work/'traffic-qa-1.3.0/history-web-qa.json',out/'历史与资源网页验证记录.json')
    shutil.copy2(work/'traffic-qa-1.2.0/global-web-qa.json',out/'网页管理验证记录.json')
    package=out/('vpn-traffic_'+VERSION+'_all.deb')
    digest=hashlib.sha256(package.read_bytes()).hexdigest()
    assert (out/(package.name+'.sha256')).read_text().split()[0]==digest
    archive=out/('vpn-traffic-'+VERSION+'-source.tar.gz')
    excluded_parts={'.git','.venv','venv','node_modules','__pycache__','dist','build','artifacts','.work','deliverables','test-results','playwright-report'}
    with tarfile.open(archive,'w:gz') as bundle:
        for file in sorted(ROOT.rglob('*')):
            if file.is_file() and not excluded_parts.intersection(file.relative_to(ROOT).parts) and file.suffix not in ('.pyc','.deb','.sqlite','.sqlite3','.db','.pem','.key','.p12','.pfx','.ppk') and file.name not in ('traffic-web-access.json','web-access.json','manifest.json','htpasswd','网页登录信息.txt') and not file.name.startswith('.env'):
                assert access['password'].encode() not in file.read_bytes()
                bundle.add(file,arcname=Path('vpn-traffic-'+VERSION)/file.relative_to(ROOT),recursive=False)
    (out/'软件包SHA256.txt').write_text(digest+'  '+package.name+'\n'+hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n',encoding='utf-8')
    timestamp=datetime.fromtimestamp(summary['started'],timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    esc=escape
    html=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>VPN 流量统计与查询说明</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#f5f7f8;color:#173a48;font:15px/1.85 'Segoe UI','Microsoft YaHei',sans-serif}}main{{max-width:920px;margin:40px auto;padding:0 20px}}header,section{{background:#fff;border:1px solid #e2e9ec;border-radius:12px;padding:28px 32px;margin-bottom:20px}}h1{{font-size:29px;line-height:1.4}}h2{{font-size:20px;margin-top:0}}a{{color:#087e78}}p{{margin:10px 0}}.tag{{font-size:11px;color:#087e78;letter-spacing:.1em}}.muted{{color:#77858d;font-size:13px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#153b49;color:#eaf4f5;padding:19px;border-radius:8px;font:12px/1.8 Consolas,monospace}}table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{border-bottom:1px solid #e7ecef;padding:10px;text-align:left;overflow-wrap:anywhere}}code{{font-family:Consolas,monospace;font-size:13px;overflow-wrap:anywhere}}@media(max-width:600px){{main{{margin:18px auto;padding:0 12px}}header,section{{padding:22px 18px}}h1{{font-size:24px}}}}
    </style></head><body><main><header><span class="tag">VPN TRAFFIC / 1.0.0</span><h1>流量统计与查询说明</h1><p>服务器 <strong>{esc(args.server)}</strong> · Android / Windows 分别统计</p><p><a href="{esc(access['url'])}">打开 HTTPS 流量查询页面 ↗</a></p><p class="muted">统计开始于 {timestamp}（北京时间）。网页有独立登录保护。</p></header>
    <section><h2>登录与查询</h2><ol><li>打开上方查询页面。</li><li>使用 <a href="网页登录信息.txt">网页登录信息.txt</a> 中的用户名和密码，在浏览器弹出的登录框登录。</li><li>“统计范围”可选择全部连接、Android 或 Windows。</li><li>页面显示今日、本月、累计上传和下载，以及最近采样间隔的平均速度。</li><li>在“历史记录”中选择日期，点击查询；“导出 CSV”会导出相同日期和设备范围。</li></ol><p>手机上可左右滑动表格查看其余列。页面在可见时每 30 秒自动刷新，也可手动刷新。</p></section>
    <section><h2>安装包与源码</h2><p><a href="{package.name}">{package.name}</a> · <a href="{archive.name}">源码包（含测试）</a> · <a href="软件包SHA256.txt">SHA256 校验记录</a></p><pre>sha256sum -c {package.name}.sha256
sudo apt install ./{package.name}
sudo vpn-traffic web-password</pre><p>适用于已有本项目 Xray 与 HTTPS 订阅部署的 Debian / Ubuntu。安装包不包含服务器连接凭据、登录密码、数据库或 Xray 二进制。初次启用统计会重启 Xray，重装和升级保留已有数据与密码。</p><p class="muted">DEB SHA256：<code>{digest}</code></p></section>
    <section><h2>统计口径与保留时间</h2><ul><li>按独立身份计数。同一身份的 VLESS 链接和在线订阅共用一份累计。</li><li>上传方向为客户端到服务器，下载方向为服务器到客户端。单位按 1024 换算。</li><li>从启用时开始统计，不能补回之前的流量。验收时两套身份各做过一次 256 KiB 下载，该用量会计入记录。</li><li>北京时间每日、每月汇总；跨午夜采样按时间比例分配，精度受 30 秒采样间隔影响。</li><li>每日汇总与累计长期保存；采样明细保留 30 天。</li><li>正常 Xray 重启前保存计数。突发崩溃可能漏记最近一次采样之后的流量。</li><li>统计属于代理流量，与服务商账单可能不同。</li></ul></section>
    <section><h2>维护与备份</h2><pre>sudo vpn-traffic show
sudo vpn-traffic status
sudo vpn-traffic history --from 2026-11-01 --to 2026-11-07 --device android
sudo vpn-traffic export --from 2026-11-01 --to 2026-11-07 --output /root/traffic.csv
sudo vpn-traffic web-password --reset
sudo vpn-traffic backup
sudo /root/xray-deployment/backup.sh
systemctl status vpn-traffic-collector vpn-traffic-web --no-pager</pre><p>完整备份已包含一致性的数据库副本、应用和配置。数据库在 <code>/var/lib/vpn-traffic/traffic.sqlite3</code>，登录凭据在 <code>/etc/vpn-traffic/web-access.json</code>。</p><p>卸载使用 <code>sudo apt remove vpn-traffic</code>。卸载撤除服务、网页入口和本包启用的统计配置；数据库、凭据和备份保留，包括 purge。恢复操作详见源码 README 或服务器 <code>/usr/share/doc/vpn-traffic/README.md</code>。</p></section>
    <section><h2>已完成的验证</h2><ul><li>10 项统计测试：去重、计数器重置、Xray 重启、跨日、筛选、备份和异常状态等。</li><li>两套身份分别代理下载，统计及历史记录均有增长。</li><li>未登录访问页面及 API 返回 401；登录后可查询和导出。</li><li>实际重启 Xray，历史累计保留，采集恢复。</li><li>重新安装保留数据库与密码。</li><li>重新发布在线订阅后，网页路由及原订阅地址保持有效。</li><li>桌面 1440×1080、手机 390×844 页面无整页横向溢出，筛选、空结果、日期校验、CSV 和刷新均通过。</li></ul><p><a href="vpn-traffic-verification.json">服务端验收记录</a> · <a href="vpn-traffic-restart-verification.json">重启验证</a> · <a href="web页面验证记录.json">网页验证</a></p></section>
    <p class="muted">本交付包包含网页登录信息，请私人保存。服务器上的服务端私钥、连接身份和数据库未包含在本交付包中。</p></main></body></html>'''
    html=html.replace('VPN TRAFFIC / 1.0.0','VPN TRAFFIC / '+VERSION)
    html=html.replace('Android / Windows 分别统计','独立连接 · 月度额度管理')
    html=html.replace('<section><h2>安装包与源码</h2>', '''<section><h2>新增连接与月度额度</h2><ol><li>点击网页右上角“新增连接”，设置名称、月度额度、每月重置日和重置时间（北京时间）。</li><li>额度单位为 GiB，0 表示不限额；上传与下载合计计入额度。</li><li>保存后可复制独立的 VLESS 连接或私人在线订阅。列表中“链接”可再次查看，“编辑”可调额、改计划或启停。</li><li>重置日支持 1–31 日；当月没有该日时在月末重置，下月仍沿用原设置日。</li><li>修改重置计划保留本期已用量。立即重置本期用量会保留历史累计，并按条件恢复因额度用尽而停用的连接。</li></ol><p>配额周期按自定义计划；“本月流量”仍按自然月。手工停用的连接不会自动恢复。</p><p>配额每 2 秒检查，可能超出一个检查间隔内的传输量。为确保长连接停止，新增、启停及额度用尽时会短暂重启 Xray，其他连接需要重连。只调整额度或计划且不改变启停状态时无需重启。</p></section><section><h2>安装包与源码</h2>''')
    html=html.replace('10 项统计测试','20 项统计与月度配额测试')
    html=re.sub(r'<section><h2>新增连接与月度额度</h2>.*?</section>', '''<section><h2>系统统一重置与临时流量</h2><ol><li>右上角“系统设置”修改每月统一重置日和时间（北京时间），所有现有及新增连接共用此计划。账户编辑窗口不再单独设置日期。</li><li>每条连接仍有独立基础月额度，单位 GiB，0 表示不限额。修改系统计划保留所有账户已用量和临时额度。</li><li>连接超额后自动停用。在列表点击“临时加额”，可增加本期可用流量：先补足已超用部分，再增加指定可用量；因超额停用的连接会恢复。</li><li>临时加额不修改基础月额度、不清零已用量或历史。系统下次统一重置时，本期用量清零，临时额度清除，回到各自基础月额度。</li><li>手工停用的连接不会仅因加额而自动启用。手动重置单个账户本期用量也不会更改系统日程。</li></ol><p>每月 1–31 日可选，不存在的日期按当月月末处理。修改日程不会马上清零；新的时间从下次统一重置起执行。</p><p>配额每 2 秒检查，可能超出检查间隔内的流量。新增、启停、到额停用或加额恢复连接会使其他连接短暂重连。</p></section>''',html,flags=re.S)
    html=html.replace('20 项统计与月度配额测试','28 项统计、统一周期与临时流量测试')
    html=html.replace('systemctl status vpn-traffic-collector vpn-traffic-web','systemctl status vpn-traffic-collector vpn-traffic-manager vpn-traffic-web')
    html=html.replace('<p><a href="vpn-traffic-verification.json">服务端验收记录</a> · <a href="vpn-traffic-restart-verification.json">重启验证</a> · <a href="web页面验证记录.json">网页验证</a></p>', '<p><a href="vpn-traffic-quota-verification.json">额度与停用验收</a> · <a href="网页管理验证记录.json">网页管理验收</a> · <a href="web页面验证记录.json">查询与布局验收</a></p>')
    html=html.replace('vpn-traffic-quota-verification.json','vpn-traffic-system-verification.json').replace('额度与停用验收','统一周期与临时加额验收')
    html=html.replace('vpn-traffic-system-verification.json','vpn-traffic-history-verification.json').replace('统一周期与临时加额验收','本版服务器验收')
    html=html.replace('28 项统计、统一周期与临时流量测试','37 项统计、小时汇总与系统资源测试')
    html=html.replace('<section><h2>维护与备份</h2>', '<section><h2>小时历史与系统资源</h2><p>历史默认“近 1 天”，显示最近 24 小时每小时的上传、下载与合计；首尾小时可能不完整。历史区域可独立选择某条连接，或点击连接行的“历史”按钮。CSV 沿用当前连接和视图。</p><p>近 7 天、近 30 天与自定义日期继续按天查看。未采集时段显示为 —，CSV 对应数值留空。</p><p>CPU、内存和根分区磁盘占用显示在桌面左下角，约每 5 秒刷新；手机布局移到页面底部。资源超过 20 秒未更新时显示不可用。</p><p><a href="VPN流量监控技术方案.html">打开完整 HTML 技术方案</a></p></section><section><h2>维护与备份</h2>')
    (out/'流量统计与查询说明.html').write_text(html,encoding='utf-8')
    from build_technical_document import build as build_technical
    verification=json.loads((out/'vpn-traffic-history-verification.json').read_text())
    build_technical(out,summary,verification,digest,system_metrics)
    with zipfile.ZipFile(out/('VPN流量监控部署资料-'+VERSION+'.zip'),'w',zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(out.iterdir()):
            if file.is_file() and file.suffix!='.zip':bundle.write(file,file.name)
    print('Private delivery package ready; deb SHA256: '+digest)
    print('Statistics started (Beijing): '+timestamp)


if __name__=='__main__':main()
