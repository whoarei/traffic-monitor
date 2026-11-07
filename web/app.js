'use strict';
const $ = id => document.getElementById(id);
let latest = null, lastHistory = null, historyRows = [], historyPage = 0, requestId = 0, refreshing = false;
let historyMode='hour';
const pageSize = 20;
const dateTime = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
const dateOnly = new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});
function bytes(value) { const n = Number(value)||0; if(n===0) return '0 B'; const units=['B','KiB','MiB','GiB','TiB','PiB']; const i=Math.min(units.length-1,Math.floor(Math.log(Math.max(1,n))/Math.log(1024))); return `${(n/1024**i).toLocaleString('en-US',{minimumFractionDigits:i?2:0,maximumFractionDigits:i?2:0})} ${units[i]}`; }
function text(tag, value, className) { const el=document.createElement(tag); el.textContent=value; if(className)el.className=className; return el; }
function showError(message) { $('error').textContent=message; $('error').hidden=!message; }
async function get(path) { const response=await fetch(path,{cache:'no-store',credentials:'same-origin'}); if(response.status===401)throw new Error('登录已失效，请刷新页面重新登录。'); const result=await response.json(); if(!response.ok)throw new Error(result.error||'数据加载失败'); return result; }
function selectedDevices() { return latest.devices.filter(d=>!$('device').value||d.id===$('device').value); }
function showSummary() {
  const devices=selectedDevices();
  const sum=key=>devices.reduce((n,d)=>n+Number(d[key]),0);
  for(const [prefix,up,down] of [['today',sum('today_up'),sum('today_down')],['month',sum('month_up'),sum('month_down')],['all',sum('up'),sum('down')]]) {
    $(prefix+'-total').textContent=bytes(up+down); $(prefix+'-detail').textContent=`上传 ${bytes(up)} · 下载 ${bytes(down)}`;
  }
  $('rate-total').textContent=bytes(sum('up_rate')+sum('down_rate'))+'/s';
  $('rate-detail').textContent=`↑ ${bytes(sum('up_rate'))}/s · ↓ ${bytes(sum('down_rate'))}/s`;
  $('server-side').textContent=latest.server;
  $('health').textContent=latest.healthy?'采集正常':'采集延迟'; $('health').className='health '+(latest.healthy?'good':'bad');
  $('updated').textContent=latest.last_sample?'最近采集 '+dateTime.format(new Date(latest.last_sample*1000)):'等待首次采集';
  $('started').textContent='开始记录于 '+dateTime.format(new Date(latest.started*1000));
  $('device-count').textContent=devices.length+' 条连接';
  const plan=latest.schedule;
  $('system-schedule').textContent=`系统统一重置：每月 ${plan.reset_day} 日 ${plan.reset_time}（北京时间） · 下次 ${dateTime.format(new Date(plan.next_reset*1000))}`;
  $('devices-body').replaceChildren();
  for(const d of devices) {
    const tr=document.createElement('tr'); const name=document.createElement('td'); const box=text('div','','device-name');
    box.append(text('span',d.id==='android'?'▯':'▱','device-icon'));
    const label=document.createElement('div'); label.append(text('strong',d.label),text('small',d.id+(d.blocked==='quota'?' · 额度已用尽':!d.enabled?' · 已停用':d.active?' · 有效身份':' · 待生效'))); box.append(label); name.append(box); tr.append(name);
    const speed=document.createElement('td'); speed.append(text('span','↑ '+bytes(d.up_rate)+'/s','speed-up'),text('span','↓ '+bytes(d.down_rate)+'/s','speed-down')); tr.append(speed);
    for(const n of [d.today_up+d.today_down,d.month_up+d.month_down,d.up,d.down,d.total])tr.append(text('td',bytes(n)));
    tr.lastElementChild.className='row-total';
    const quota=text('td','','quota-cell'+(d.blocked==='quota'?' quota-exhausted':''));quota.append(text('span',d.quota?`${bytes(d.period_used)} / ${bytes(d.quota_effective)}`:'不限额'),text('small',d.quota?`月额度 ${bytes(d.quota)} · 临时 +${bytes(d.bonus)}`:'跟随系统统一重置'));tr.append(quota);
    const actions=document.createElement('td'),rowActions=text('div','','row-actions');for(const [label,handler]of [['历史',()=>openHistory(d.id)],['链接',()=>sharing(d.id)],['编辑',()=>editLink(d)],...(d.quota?[['临时加额',()=>openBonus(d)]]:[])]){const button=text('button',label,'button');button.type='button';button.addEventListener('click',handler);rowActions.append(button);}actions.append(rowActions);tr.insertBefore(actions,tr.children[1]);$('devices-body').append(tr);
  }
  if(!devices.length) { const tr=document.createElement('tr');const td=text('td','暂无连接','empty');td.colSpan=9;tr.append(td);$('devices-body').append(tr); }
}
function showHistoryTable() {
  const pages=Math.max(1,Math.ceil(historyRows.length/pageSize)); historyPage=Math.min(historyPage,pages-1);
  $('history-body').replaceChildren();
  for(const row of historyRows.slice(historyPage*pageSize,(historyPage+1)*pageSize)) {
    const tr=document.createElement('tr'); for(const v of [row.day,row.label,...(row.observed===false?['—','—','—']:[bytes(row.up),bytes(row.down),bytes(row.total)])])tr.append(text('td',v));
    tr.lastElementChild.className='row-total'; $('history-body').append(tr);
  }
  if(!historyRows.length) { const tr=document.createElement('tr');const td=text('td','所选日期暂无记录','empty');td.colSpan=5;tr.append(td);$('history-body').append(tr); }
  $('history-count').textContent=`${historyRows.length} 条记录`;
  $('page-label').textContent=`${historyPage+1} / ${pages}`; $('prev').disabled=historyPage===0; $('next').disabled=historyPage>=pages-1;
}
function chart(result) {
  lastHistory=result;
  const svg=$('chart'); svg.replaceChildren();
  const make=(tag,attrs,content)=>{const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))el.setAttribute(k,String(v));if(content!==undefined)el.textContent=content;return el;};
  const hourly=result.granularity==='hour',days=new Map();
  if(hourly){for(const row of result.rows){const key=String(row.bucket_start);if(!days.has(key))days.set(key,{up:0,down:0});const point=days.get(key);point.up+=row.up;point.down+=row.down;}}
  else{const first=new Date(result.from+'T00:00:00+08:00'), last=new Date(result.to+'T00:00:00+08:00');for(let t=first.getTime();t<=last.getTime();t+=86400000)days.set(dateOnly.format(new Date(t)),{up:0,down:0});for(const row of result.rows){const day=days.get(row.day);if(day){day.up+=row.up;day.down+=row.down;}}}
  let points=[...days.entries()];
  if(hourly)points.sort((a,b)=>Number(a[0])-Number(b[0]));
  // Keep long ranges readable by grouping neighbouring days into at most 90 bars.
  const bucket=Math.max(1,Math.ceil(points.length/90));
  if(bucket>1){const groups=[];for(let i=0;i<points.length;i+=bucket){const group=points.slice(i,i+bucket);groups.push([group[0][0],{up:group.reduce((n,x)=>n+x[1].up,0),down:group.reduce((n,x)=>n+x[1].down,0)}]);}points=groups;}
  const canvasWidth=Math.max(280,svg.clientWidth||960),canvasHeight=svg.clientHeight||220;
  svg.setAttribute('viewBox',`0 0 ${canvasWidth} ${canvasHeight}`);
  const max=Math.max(1024,...points.map(([,p])=>p.up+p.down));const left=59,top=14,width=canvasWidth-left-12,height=canvasHeight-top-30;
  for(let i=0;i<=3;i++){const y=top+height*i/3;svg.append(make('line',{x1:left,y1:y,x2:left+width,y2:y,class:'chart-grid'}),make('text',{x:left-10,y:y+4,'text-anchor':'end',class:'chart-axis'},bytes(max*(1-i/3))));}
  const gap=width/Math.max(1,points.length), bar=Math.min(56,gap*.52);
  points.forEach(([day,p],i)=>{const x=left+gap*(i+.5)-bar/2;const down=p.down/max*height,up=p.up/max*height;const label=hourly?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(Number(day)*1000)):day.slice(5);const full=hourly?dateTime.format(new Date(Number(day)*1000)):day;const group=make('g',{});group.append(make('title',{},`${full}${bucket>1?' 起 '+bucket+' 天':''} · 上传 ${bytes(p.up)} · 下载 ${bytes(p.down)}`));group.append(make('rect',{x,y:top+height-down,width:bar,height:down,rx:2,class:'chart-down'}),make('rect',{x,y:top+height-down-up,width:bar,height:up,rx:2,class:'chart-up'}));svg.append(group);if(i%Math.max(1,Math.ceil(points.length/Math.max(2,Math.floor(width/90))))===0||i===points.length-1)svg.append(make('text',{x:x+bar/2,y:top+height+22,'text-anchor':'middle',class:'chart-axis'},label));});
  $('chart-empty').hidden=result.up+result.down>0;
}
async function loadHistory(resetPage=false) {
  const id=++requestId;
  if(historyMode==='day'&&(!$('from').value||!$('to').value))throw new Error('请选择完整的开始和结束日期');
  if(historyMode==='day'&&$('from').value>$('to').value)throw new Error('开始日期不能晚于结束日期');
  const query=new URLSearchParams({from:$('from').value,to:$('to').value,device:$('history-device').value,view:historyMode==='hour'?'hourly':'daily'});
  $('export').href='api/export.csv?'+query;
  const result=await get('api/history?'+query);
  if(id!==requestId)return;
  historyRows=result.rows; if(resetPage)historyPage=0;
  $('history-view-label').textContent=result.granularity==='hour'?'近 1 天 · 按小时':'按天汇总';
  $('history-description').textContent=result.granularity==='hour'?'最近 24 小时，按北京时间整点汇总，首尾小时可能不完整。':'按北京时间汇总，选择连接和日期查看历史记录。';
  $('history-period-heading').textContent=result.granularity==='hour'?'小时区间 · 北京时间':'日期 · 北京时间';
  $('history-coverage').hidden=result.granularity!=='hour';
  $('chart').setAttribute('aria-label',result.granularity==='hour'?'每小时上传和下载流量图':'每日上传和下载流量图');
  $('period-total').textContent=bytes(result.up+result.down);
  showHistoryTable();chart(result);
}
async function refresh() {
  if(refreshing)return; refreshing=true;$('refresh').disabled=true;
  try {
    latest=await get('api/summary'); const selected=$('device').value;
    const options=[text('option','全部连接')];options[0].value='';
    for(const d of latest.devices){const option=text('option',d.label);option.value=d.id;options.push(option);}
    const historySelected=$('history-device').value;
    $('device').replaceChildren(...options);if(options.some(o=>o.value===selected))$('device').value=selected;
    $('history-device').replaceChildren(...options.map(option=>option.cloneNode(true)));if(options.some(o=>o.value===historySelected))$('history-device').value=historySelected;
    showSummary();await loadHistory();showError(latest.manager_error?'连接管理暂时异常，配额可能延迟生效，请联系管理员。':latest.healthy?'':(latest.error?'采集暂时异常，当前显示已保存的数据。':'最近采集已超过 90 秒，当前显示已保存的数据。'));
  }catch(error){showError(error.message);$('health').textContent='连接异常';$('health').className='health bad';}
  finally{refreshing=false;$('refresh').disabled=false;}
}
function historyModeSet(mode){historyMode=mode;$('from').disabled=mode==='hour';$('to').disabled=mode==='hour';$('last-day').classList.toggle('selected',mode==='hour');}
function range(days) { historyModeSet('day');const now=new Date();$('to').value=dateOnly.format(now);$('from').value=dateOnly.format(new Date(now.getTime()-(days-1)*86400000));for(const button of document.querySelectorAll('[data-days]'))button.classList.toggle('selected',Number(button.dataset.days)===days); }
function lastDay(){historyModeSet('hour');const now=new Date();$('to').value=dateOnly.format(now);$('from').value=dateOnly.format(new Date(now.getTime()-86400000));for(const button of document.querySelectorAll('[data-days]'))button.classList.remove('selected');}
async function openHistory(id){$('history-device').value=id;try{await loadHistory(true);showError('');$('history').scrollIntoView({behavior:'smooth',block:'start'});}catch(error){showError(error.message);}}
$('filters').addEventListener('submit',async event=>{event.preventDefault();try{await loadHistory(true);showError('');}catch(error){showError(error.message);}});
$('device').addEventListener('change',async()=>{if(latest)showSummary();$('history-device').value=$('device').value;try{await loadHistory(true);showError('');}catch(error){showError(error.message);}});
$('history-device').addEventListener('change',()=>openHistory($('history-device').value));
$('last-day').addEventListener('click',async()=>{lastDay();try{await loadHistory(true);showError('');}catch(error){showError(error.message);}});
$('custom-history').addEventListener('click',()=>{historyModeSet('day');for(const button of document.querySelectorAll('[data-days]'))button.classList.remove('selected');$('from').focus();});
$('refresh').addEventListener('click',refresh);
for(const button of document.querySelectorAll('[data-days]'))button.addEventListener('click',async()=>{range(Number(button.dataset.days));try{await loadHistory(true);showError('');}catch(error){showError(error.message);}});
for(const id of ['from','to'])$(id).addEventListener('change',()=>{for(const button of document.querySelectorAll('[data-days]'))button.classList.remove('selected');});
$('prev').addEventListener('click',()=>{historyPage--;showHistoryTable();});$('next').addEventListener('click',()=>{historyPage++;showHistoryTable();});
for(const link of document.querySelectorAll('.nav-item'))link.addEventListener('click',()=>{for(const other of document.querySelectorAll('.nav-item'))other.classList.toggle('active',other===link);});
lastDay();refresh();setInterval(()=>{if(!document.hidden)refresh();},30000);
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(lastHistory)chart(lastHistory);},100);});
let csrfToken='',editingId='',formRequestId='';
async function mutate(path,payload){
  for(let attempt=0;attempt<2;attempt++){
    if(!csrfToken)csrfToken=(await get('api/session')).token;
    let response;
    try{response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-VPN-CSRF':csrfToken},body:JSON.stringify(payload)});}catch(error){throw new Error('连接暂时中断。操作可能已生效，请刷新列表确认后再重试。');}
    if(response.status===403&&attempt===0){csrfToken='';continue;}
    const result=await response.json();if(!response.ok)throw new Error(result.error||'管理操作失败');return result;
  }
  throw new Error('登录状态已更新，请刷新页面重试。');
}
function editLink(device=null){
  editingId=device?.id||'';formRequestId=crypto.randomUUID();$('link-title').textContent=device?'编辑连接':'新增连接';
  $('link-label').value=device?.label||'';$('link-quota').value=device?String(device.quota/1024**3):'0';
  $('link-enabled').checked=device?device.enabled:true;
  const plan=latest.schedule;$('link-next-reset').textContent=`系统统一：每月 ${plan.reset_day} 日 ${plan.reset_time}，下次 ${dateTime.format(new Date(plan.next_reset*1000))}`;
  $('reset-period').hidden=!device;$('link-error').hidden=true;$('save-link').disabled=false;$('link-dialog').showModal();
}
async function sharing(id){
  try{const result=await get('api/links/'+encodeURIComponent(id)+'/sharing');$('sharing-title').textContent=result.label;$('sharing-vless').value=result.vless;$('sharing-subscription').value=result.subscription;$('sharing-state').textContent=result.active?'请私人保存以下链接。分享同一链接的设备会合并计算用量。':'此连接当前未启用。恢复后可继续使用原链接；额度用尽时订阅地址可能暂时返回 404。';$('copy-status').textContent='';$('sharing-dialog').showModal();}catch(error){showError(error.message);}
}
$('new-link').addEventListener('click',()=>editLink());
for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>$(button.dataset.close).close());
$('link-form').addEventListener('submit',async event=>{
  event.preventDefault();$('save-link').disabled=true;$('link-error').hidden=true;
  try{
    const amount=Number($('link-quota').value);if(!Number.isFinite(amount)||amount<0)throw new Error('请输入有效额度');
    const payload={label:$('link-label').value.trim(),quota:Math.round(amount*1024**3),enabled:$('link-enabled').checked,request_id:formRequestId};
    const result=await mutate(editingId?'api/links/'+encodeURIComponent(editingId)+'/update':'api/links',payload);
    const created=!editingId;$('link-dialog').close();await refresh();if(created)await sharing(result.id);
  }catch(error){$('link-error').textContent=error.message;$('link-error').hidden=false;}
  finally{$('save-link').disabled=false;}
});
$('reset-period').addEventListener('click',async()=>{
  if(!editingId||!confirm('将本期配额用量清零，历史累计保留。若连接因额度用尽而停用，将恢复连接并使其他连接短暂重连。继续吗？'))return;
  $('reset-period').disabled=true;
  try{await mutate('api/links/'+encodeURIComponent(editingId)+'/reset',{});$('link-dialog').close();await refresh();}catch(error){$('link-error').textContent=error.message;$('link-error').hidden=false;}finally{$('reset-period').disabled=false;}
});
for(const button of document.querySelectorAll('[data-copy]'))button.addEventListener('click',async()=>{try{await navigator.clipboard.writeText($(button.dataset.copy).value);$('copy-status').textContent='已复制。';}catch(error){$(button.dataset.copy).select();$('copy-status').textContent='请手动复制选中的链接。';}});
let bonusId='',bonusRequestId='';
$('system-settings').addEventListener('click',()=>{if(!latest)return;$('schedule-day').value=latest.schedule.reset_day;$('schedule-time').value=latest.schedule.reset_time;$('settings-error').hidden=true;$('settings-dialog').showModal();});
$('settings-form').addEventListener('submit',async event=>{event.preventDefault();$('save-settings').disabled=true;$('settings-error').hidden=true;try{await mutate('api/settings',{reset_day:Number($('schedule-day').value),reset_time:$('schedule-time').value});$('settings-dialog').close();await refresh();}catch(error){$('settings-error').textContent=error.message;$('settings-error').hidden=false;}finally{$('save-settings').disabled=false;}});
function openBonus(device){bonusId=device.id;bonusRequestId=crypto.randomUUID();$('bonus-description').textContent=`${device.label} · 本期已用 ${bytes(device.period_used)} · 基础月额度 ${bytes(device.quota)} · 已有临时额度 ${bytes(device.bonus)}`;$('bonus-amount').value='1';$('bonus-error').hidden=true;$('bonus-dialog').showModal();}
$('bonus-form').addEventListener('submit',async event=>{event.preventDefault();$('save-bonus').disabled=true;$('bonus-error').hidden=true;try{const amount=Math.round(Number($('bonus-amount').value)*1024**3);if(!Number.isSafeInteger(amount)||amount<=0)throw new Error('请输入大于 0 的有效临时流量');await mutate('api/links/'+encodeURIComponent(bonusId)+'/bonus',{amount,request_id:bonusRequestId});$('bonus-dialog').close();await refresh();}catch(error){$('bonus-error').textContent=error.message;$('bonus-error').hidden=false;}finally{$('save-bonus').disabled=false;}});
async function refreshSystem(){
  let metrics;
  try{metrics=await get('api/system');}catch(error){metrics={available:false};}
  for(const key of ['cpu','memory','disk']){
    const value=metrics.available?metrics[key+'_percent']:null;
    const valid=Number.isFinite(value);
    for(const element of document.querySelectorAll('[data-system-value="'+key+'"]'))element.textContent=valid?value.toFixed(1)+'%':'—';
    for(const element of document.querySelectorAll('[data-system-bar="'+key+'"]'))element.value=valid?value:0;
  }
  for(const element of document.querySelectorAll('[data-system-status]'))element.textContent=metrics.available?'实时':'暂不可用';
  const details=metrics.available?`内存 ${bytes(metrics.memory_used)} / ${bytes(metrics.memory_total)} · 磁盘 ${bytes(metrics.disk_used)} / ${bytes(metrics.disk_total)}`:'系统指标暂时不可用';
  for(const element of document.querySelectorAll('[data-system-details]'))element.textContent=details;
}
refreshSystem();setInterval(()=>{if(!document.hidden)refreshSystem();},5000);
