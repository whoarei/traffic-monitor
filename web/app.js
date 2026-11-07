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
function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS(svg.namespaceURI,'use');svg.setAttribute('class','icon');svg.setAttribute('aria-hidden','true');use.setAttribute('href','#icon-'+name);svg.append(use);return svg;}
function metricValue(id,value,rate=false){const formatted=bytes(value),split=formatted.lastIndexOf(' ');$(id).replaceChildren(text('span',formatted.slice(0,split)),text('small',formatted.slice(split+1)+(rate?'/s':'')));}
function quotaMeter(device){const track=text('div','','quota-track'),fill=document.createElement('span');const percent=device.quota?Math.min(100,Math.max(0,device.period_used/device.quota_effective*100)):0;fill.style.width=percent+'%';track.append(fill);track.setAttribute('role','progressbar');track.setAttribute('aria-label','本期额度使用率');track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');track.setAttribute('aria-valuenow',String(percent));return track;}
function showQuotaOverview(devices){
  const ordered=[...devices].sort((a,b)=>Number(b.blocked==='quota')-Number(a.blocked==='quota')+(a.blocked===b.blocked?((b.quota?b.period_used/b.quota_effective:0)-(a.quota?a.period_used/a.quota_effective:0)):0));
  $('quota-overview').replaceChildren();
  for(const d of ordered.slice(0,4)){const item=text('article','','quota-item'+(d.blocked==='quota'?' exhausted':'')),heading=text('div','','quota-heading'),name=text('span',d.label);name.title=d.label;heading.append(name,text('strong',d.quota?(d.period_used/d.quota_effective*100).toFixed(1)+'%':'不限额'));item.append(heading,quotaMeter(d),text('p',bytes(d.period_used)+' / '+(d.quota?bytes(d.quota_effective):'不限额')+(d.bonus?' · 含临时额度':'')));$('quota-overview').append(item);}
  if(!devices.length)$('quota-overview').append(text('p','暂无连接','empty'));
  const exhausted=devices.filter(d=>d.enabled&&d.blocked==='quota');$('quota-alert').hidden=!exhausted.length;
  if(exhausted.length){$('quota-alert-text').textContent=exhausted[0].label+(exhausted.length>1?' 等 '+exhausted.length+' 条连接':'')+'已达到本期额度，连接已停用。';$('quota-alert-action').onclick=()=>openBonus(exhausted[0]);}
}
function showSummary() {
  const devices=selectedDevices();
  const sum=key=>devices.reduce((n,d)=>n+Number(d[key]),0);
  for(const [prefix,up,down] of [['today',sum('today_up'),sum('today_down')],['month',sum('month_up'),sum('month_down')],['all',sum('up'),sum('down')]]) {
    metricValue(prefix+'-total',up+down); $(prefix+'-detail').textContent=`上传 ${bytes(up)} · 下载 ${bytes(down)}`;
  }
  metricValue('rate-total',sum('up_rate')+sum('down_rate'),true);
  $('rate-detail').textContent=`↑ ${bytes(sum('up_rate'))}/s · ↓ ${bytes(sum('down_rate'))}/s`;
  $('server-side').textContent=latest.server;
  $('health').textContent=latest.healthy?'采集正常':'采集延迟'; $('health').className='health '+(latest.healthy?'good':'bad');
  $('updated').textContent=latest.last_sample?'最近采集 '+dateTime.format(new Date(latest.last_sample*1000)):'等待首次采集';
  $('started').textContent='开始记录于 '+dateTime.format(new Date(latest.started*1000));
  $('device-count').textContent=devices.length+' 条连接';
  $('nav-count').textContent=latest.devices.length;$('app-version').textContent=latest.version;
  $('overview-state').replaceChildren(text('span',devices.length+' 条连接 · '),text('strong',devices.filter(d=>d.active).length+' 条可用'),text('span',' · '+devices.filter(d=>d.enabled&&d.blocked==='quota').length+' 条已到额'));
  const plan=latest.schedule;
  $('system-schedule').textContent=`系统统一重置：每月 ${plan.reset_day} 日 ${plan.reset_time}（北京时间） · 下次 ${dateTime.format(new Date(plan.next_reset*1000))}`;
  showQuotaOverview(devices);
  $('devices-body').replaceChildren();
  for(const d of devices) {
    const tr=document.createElement('tr'); const name=document.createElement('td'); const box=text('div','','device-name');
    const deviceIcon=text('span','','device-icon');deviceIcon.append(icon(d.id==='android'?'phone':'device'));box.append(deviceIcon);
    const label=document.createElement('div');label.append(text('strong',d.label),text('small',d.id),text('span',!d.enabled?'已停用':d.blocked==='quota'?'额度用尽':d.active?'可用':'待生效','device-status'+(!d.enabled?' disabled':d.blocked==='quota'?' exhausted':'')));box.append(label);name.append(box);tr.append(name);
    const quota=text('td','','quota-cell'+(d.blocked==='quota'?' quota-exhausted':''));quota.dataset.label='本期用量 / 额度';quota.append(text('span',d.quota?`${bytes(d.period_used)} / ${bytes(d.quota_effective)}`:'不限额'),quotaMeter(d),text('small',d.quota?`月额度 ${bytes(d.quota)} · 临时 +${bytes(d.bonus)}`:'跟随系统统一重置'));tr.append(quota);
    const speed=document.createElement('td');speed.dataset.label='当前速度';speed.append(text('span','↑ '+bytes(d.up_rate)+'/s','speed-up'),text('span','↓ '+bytes(d.down_rate)+'/s','speed-down'));tr.append(speed);
    const period=document.createElement('td');period.dataset.label='今日 / 本月';for(const [title,amount]of [['今日',d.today_up+d.today_down],['本月',d.month_up+d.month_down]]){const line=text('span','','cell-line');line.append(text('small',title),text('span',bytes(amount)));period.append(line);}tr.append(period);
    const cumulative=document.createElement('td');cumulative.dataset.label='累计流量';cumulative.append(text('span','↑ '+bytes(d.up),'cell-line'),text('span','↓ '+bytes(d.down),'cell-line'),text('span','合计 '+bytes(d.total),'row-total'));tr.append(cumulative);
    const actions=document.createElement('td'),rowActions=text('div','','row-actions');actions.dataset.label='管理';for(const [label,handler]of [['历史',()=>openHistory(d.id)],['链接',()=>sharing(d.id)],['编辑',()=>editLink(d)],...(d.quota?[['临时加额',()=>openBonus(d)]]:[])]){const button=text('button',label,'button'+(label==='临时加额'?' bonus-action':''));button.type='button';button.addEventListener('click',handler);rowActions.append(button);}actions.append(rowActions);tr.append(actions);$('devices-body').append(tr);
  }
  if(!devices.length) { const tr=document.createElement('tr');const td=text('td','暂无连接','empty');td.colSpan=6;tr.append(td);$('devices-body').append(tr); }
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
  $('history-row-count').textContent=`${historyRows.length} 条记录`;
  $('page-label').textContent=`${historyPage+1} / ${pages}`; $('prev').disabled=historyPage===0; $('next').disabled=historyPage>=pages-1;
}
function chart(result) {
  lastHistory=result;
  const svg=$('chart');svg.replaceChildren();$('chart-selection').hidden=true;
  const make=(tag,attrs,content)=>{const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))el.setAttribute(k,String(v));if(content!==undefined)el.textContent=content;return el;};
  const hourly=result.granularity==='hour',days=new Map();
  if(hourly){
    for(const row of result.rows){const key=String(row.bucket_start);if(!days.has(key))days.set(key,{up:0,down:0,observed:false});const p=days.get(key);p.up+=row.up;p.down+=row.down;p.observed=p.observed||row.observed!==false;}
  }else{
    const first=new Date(result.from+'T00:00:00+08:00'),last=new Date(result.to+'T00:00:00+08:00');
    for(let t=first.getTime();t<=last.getTime();t+=86400000)days.set(dateOnly.format(new Date(t)),{up:0,down:0,observed:true});
    for(const row of result.rows){const p=days.get(row.day);if(p){p.up+=row.up;p.down+=row.down;}}
  }
  let points=[...days.entries()];if(hourly)points.sort((a,b)=>Number(a[0])-Number(b[0]));
  const bucket=Math.max(1,Math.ceil(points.length/90));
  if(bucket>1){const groups=[];for(let i=0;i<points.length;i+=bucket){const group=points.slice(i,i+bucket);groups.push([group[0][0],{up:group.reduce((n,x)=>n+x[1].up,0),down:group.reduce((n,x)=>n+x[1].down,0),observed:group.some(x=>x[1].observed)}]);}points=groups;}
  const canvasWidth=Math.max(280,svg.clientWidth||960),canvasHeight=svg.clientHeight||220;
  svg.setAttribute('viewBox',`0 0 ${canvasWidth} ${canvasHeight}`);
  const max=Math.max(1024,...points.map(([,p])=>Math.max(p.up,p.down)))*1.12,left=75,top=17,width=canvasWidth-left-15,height=canvasHeight-top-34;
  const timestamp=key=>hourly?Number(key)*1000:new Date(key+'T00:00:00+08:00').getTime(),first=points.length?timestamp(points[0][0]):0,last=points.length?timestamp(points.at(-1)[0]):0;
  const x=i=>left+(last===first ? .5 : (timestamp(points[i][0])-first)/(last-first))*width,y=n=>top+height-n/max*height;
  const defs=make('defs',{}),gradient=make('linearGradient',{id:'download-fill',x1:0,y1:0,x2:0,y2:1});gradient.append(make('stop',{offset:'0%','stop-color':'var(--accent)','stop-opacity':'.17'}),make('stop',{offset:'100%','stop-color':'var(--accent)','stop-opacity':'0'}));defs.append(gradient);svg.append(defs);
  for(let i=0;i<=3;i++){const py=top+height*i/3;svg.append(make('line',{x1:left,y1:py,x2:left+width,y2:py,class:'chart-grid'}),make('text',{x:left-9,y:py+4,'text-anchor':'end',class:'chart-axis'},bytes(max*(1-i/3))));}
  const segments=[];let current=[];
  for(let i=0;i<points.length;i++){if(points[i][1].observed)current.push(i);else if(current.length){segments.push(current);current=[];}}
  if(current.length)segments.push(current);
  for(const segment of segments){
    const path=key=>segment.map((index,j)=>(j?'L':'M')+x(index).toFixed(2)+' '+y(points[index][1][key]).toFixed(2)).join(' '),down=path('down');
    if(segment.length>1){svg.append(make('path',{d:down+` L${x(segment.at(-1))} ${top+height} L${x(segment[0])} ${top+height} Z`,fill:'url(#download-fill)','pointer-events':'none'}),make('path',{d:down,class:'chart-glow','pointer-events':'none'}),make('path',{d:down,class:'chart-line chart-down','data-series':'download'}),make('path',{d:path('up'),class:'chart-line chart-up','data-series':'upload'}));}
    else{const index=segment[0];svg.append(make('circle',{cx:x(index),cy:y(points[index][1].down),r:2.5,class:'chart-point'}),make('circle',{cx:x(index),cy:y(points[index][1].up),r:2.5,class:'chart-point chart-point-up'}));}
  }
  const timeLabel=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false});
  const tickCount=Math.min(points.length,Math.max(2,Math.floor(width/95)+1));
  const tickIndices=new Set(Array.from({length:tickCount},(_,i)=>tickCount===1?0:Math.round(i*(points.length-1)/(tickCount-1))));
  points.forEach(([key,p],i)=>{
    const label=hourly?timeLabel.format(new Date(Number(key)*1000)):key.slice(5),full=hourly?dateTime.format(new Date(Number(key)*1000)):key;
    const description=full+(bucket>1?' 起 '+bucket+' 天':'')+(p.observed?` · 上传 ${bytes(p.up)} · 下载 ${bytes(p.down)}`:' · 未采集');
    const start=i?(x(i-1)+x(i))/2:left,end=i===points.length-1?left+width:(x(i)+x(i+1))/2;
    const hit=make('rect',{x:start,y:top,width:Math.max(0,end-start),height,class:'chart-hit',tabindex:0,role:'img','aria-label':description,'data-observed':String(p.observed)});hit.append(make('title',{},description));
    const select=()=>{$('chart-selection').textContent=description;$('chart-selection').hidden=false;};hit.addEventListener('click',select);hit.addEventListener('focus',select);svg.append(hit);
    if(tickIndices.has(i))svg.append(make('text',{x:x(i),y:top+height+24,'text-anchor':i===0?'start':i===points.length-1?'end':'middle',class:'chart-axis'},label));
  });
  $('chart-empty').hidden=result.up+result.down>0;
  $('chart-empty').textContent=points.some(([,p])=>p.observed)?'所选时段流量为 0':'所选时段尚无采集记录';
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
function historyModeSet(mode){historyMode=mode;$('from').disabled=mode==='hour';$('to').disabled=mode==='hour';$('date-range-fields').hidden=mode==='hour';$('last-day').classList.toggle('selected',mode==='hour');$('custom-history').classList.remove('selected');}
function range(days) { historyModeSet('day');const now=new Date();$('to').value=dateOnly.format(now);$('from').value=dateOnly.format(new Date(now.getTime()-(days-1)*86400000));for(const button of document.querySelectorAll('[data-days]'))button.classList.toggle('selected',Number(button.dataset.days)===days); }
function lastDay(){historyModeSet('hour');const now=new Date();$('to').value=dateOnly.format(now);$('from').value=dateOnly.format(new Date(now.getTime()-86400000));for(const button of document.querySelectorAll('[data-days]'))button.classList.remove('selected');}
async function openHistory(id){$('history-device').value=id;try{await loadHistory(true);showError('');$('history-details').open=true;$('history').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}catch(error){showError(error.message);}}
$('filters').addEventListener('submit',async event=>{event.preventDefault();try{await loadHistory(true);showError('');}catch(error){showError(error.message);}});
$('device').addEventListener('change',async()=>{if(latest)showSummary();$('history-device').value=$('device').value;try{await loadHistory(true);showError('');}catch(error){showError(error.message);}});
$('history-device').addEventListener('change',()=>openHistory($('history-device').value));
$('last-day').addEventListener('click',async()=>{lastDay();try{await loadHistory(true);showError('');}catch(error){showError(error.message);}});
$('custom-history').addEventListener('click',()=>{historyModeSet('day');$('custom-history').classList.add('selected');for(const button of document.querySelectorAll('[data-days]'))button.classList.remove('selected');$('from').focus();});
$('refresh').addEventListener('click',refresh);
for(const button of document.querySelectorAll('[data-days]'))button.addEventListener('click',async()=>{range(Number(button.dataset.days));try{await loadHistory(true);showError('');}catch(error){showError(error.message);}});
for(const id of ['from','to'])$(id).addEventListener('change',()=>{for(const button of document.querySelectorAll('[data-days]'))button.classList.remove('selected');});
$('prev').addEventListener('click',()=>{historyPage--;showHistoryTable();});$('next').addEventListener('click',()=>{historyPage++;showHistoryTable();});
for(const link of document.querySelectorAll('a.nav-item'))link.addEventListener('click',()=>{for(const other of document.querySelectorAll('a.nav-item')){other.classList.toggle('active',other===link);if(other===link)other.setAttribute('aria-current','location');else other.removeAttribute('aria-current');}if(link.hash==='#history')$('history-details').open=true;});
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
  if(!latest){showError('请等待连接数据加载后再操作。');return;}
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
for(const button of document.querySelectorAll('[data-open-settings]'))button.addEventListener('click',()=>$('system-settings').click());
$('settings-form').addEventListener('submit',async event=>{event.preventDefault();$('save-settings').disabled=true;$('settings-error').hidden=true;try{await mutate('api/settings',{reset_day:Number($('schedule-day').value),reset_time:$('schedule-time').value});$('settings-dialog').close();await refresh();}catch(error){$('settings-error').textContent=error.message;$('settings-error').hidden=false;}finally{$('save-settings').disabled=false;}});
function bonusPreview(){const device=latest?.devices.find(d=>d.id===bonusId),amount=Number($('bonus-amount').value)*1024**3;$('bonus-preview').textContent=device&&Number.isFinite(amount)&&amount>0?bytes(Math.max(0,device.quota_effective-device.period_used)+amount):'—';}
function openBonus(device){bonusId=device.id;bonusRequestId=crypto.randomUUID();$('bonus-description').textContent=`${device.label} · 本期已用 ${bytes(device.period_used)} · 基础月额度 ${bytes(device.quota)} · 已有临时额度 ${bytes(device.bonus)}`;$('bonus-amount').value='1';bonusPreview();$('bonus-error').hidden=true;$('bonus-dialog').showModal();}
$('bonus-amount').addEventListener('input',bonusPreview);
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
