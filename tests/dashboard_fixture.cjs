'use strict';
// Isolated browser fixtures. These routes never access a VPN server or database.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../web'),GiB=1024**3;
const bytes=n=>Math.round(n*GiB),clone=x=>JSON.parse(JSON.stringify(x));
const CSP="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'";
function createFixture(){
  const now=Math.floor(Date.now()/1000),plan={reset_day:7,reset_time:'00:00',cycle_start:now-3600,next_reset:now+86400*28};
  const devices=[['android','sun',50,17.8,2.48,.92],['windows','leon',300,126.4,8.32,3.15],['link-demo','linmy',50,50.8,2.04,0]].map(([id,label,quota,used,today,rate])=>({id,label,quota:bytes(quota),bonus:0,period_up:bytes(used*.12),period_down:bytes(used*.88),up:bytes(used*.3),down:bytes(used*3),today_up:bytes(today*.12),today_down:bytes(today*.88),month_up:bytes(used*.12),month_down:bytes(used*.88),up_rate:rate*1024**2*.12,down_rate:rate*1024**2*.88,enabled:true}));
  const state={devices,schedule:plan,requests:[],csrf:'fixture-csrf-1',forceCsrfRefresh:false,resourcesAvailable:true,failHistory:false,installedVersion:'1.5.0',updateChecked:false,updateFailure:'',updateJob:null,summaryRequests:0};
  function updateStatus(){return {configured:true,installed_version:state.installedVersion,available:state.updateChecked&&state.installedVersion!=='1.6.0'&&!state.updateFailure,checked_at:state.updateChecked?now:0,error:state.updateFailure,candidate:state.updateChecked?{version:'1.6.0',notes:'## 1.6.0\nSigned fixture release',expires_at:now+86400,size:64000}:null,maintenance:state.updateJob?.state==='installing',job:clone(state.updateJob)};}
  function summary(){return {version:state.installedVersion,server:'preview.local',timezone:'Asia/Shanghai',started:now-86400*60,last_sample:Math.floor(Date.now()/1000),healthy:true,manager_error:'',manager_healthy:true,schedule:clone(state.schedule),devices:state.devices.map(d=>{const used=d.period_up+d.period_down,effective=d.quota+d.bonus,blocked=!d.enabled?'disabled':d.quota&&used>=effective?'quota':'';return {...d,period_used:used,quota_effective:d.quota?effective:0,available:d.quota?Math.max(0,effective-used):null,total:d.up+d.down,active:!blocked,blocked};})};}
  function history(query){
    const selected=summary().devices.filter(d=>!query.get('device')||d.id===query.get('device')),hourly=query.get('view')==='hourly',rows=[];
    if(hourly){const end=now,start=end-86400,first=Math.floor(start/3600)*3600;for(let at=first,i=0;at<end;at+=3600,i++)for(const d of selected){const observed=i!==6,weight=(.3+Math.sin(i*.6)**2+(i>14?.5:0))/25,up=observed?Math.round(d.today_up*weight):0,down=observed?Math.round(d.today_down*weight):0;rows.push({bucket_start:at,day:new Date(at*1000+8*3600000).toISOString().slice(0,16).replace('T',' '),email:d.id,label:d.label,up,down,total:up+down,observed});}return {granularity:'hour',window_start:start,window_end:end,rows,up:rows.reduce((n,r)=>n+r.up,0),down:rows.reduce((n,r)=>n+r.down,0)};}
    const from=query.get('from'),to=query.get('to');for(let at=Date.parse(from+'T00:00:00+08:00'),i=0;at<=Date.parse(to+'T00:00:00+08:00');at+=86400000,i++)for(const d of selected){const up=Math.round(d.today_up*(.4+i*.03)),down=Math.round(d.today_down*(.4+i*.03));rows.push({day:new Date(at+8*3600000).toISOString().slice(0,10),email:d.id,label:d.label,up,down,total:up+down});}return {from,to,granularity:'day',rows,up:rows.reduce((n,r)=>n+r.up,0),down:rows.reduce((n,r)=>n+r.down,0)};
  }
  const server=http.createServer(async(req,res)=>{
    res.setHeader('Content-Security-Policy',CSP);res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
    const url=new URL(req.url,'http://localhost'),route=url.pathname,send=(status,data,type='application/json; charset=utf-8')=>{res.writeHead(status,{'Content-Type':type});res.end(type.startsWith('application/json')?JSON.stringify(data):data);};
    try{
      if(req.method==='POST'){
        let raw='';for await(const chunk of req)raw+=chunk;const payload=JSON.parse(raw);state.requests.push({path:route,payload,csrf:req.headers['x-vpn-csrf']});
        if(state.forceCsrfRefresh){state.forceCsrfRefresh=false;state.csrf='fixture-csrf-2';return send(403,{error:'Expired token'});}
        if(req.headers['x-vpn-csrf']!==state.csrf)return send(403,{error:'Invalid token'});
        if(route==='/api/updates/check'){state.updateChecked=true;return state.updateFailure?send(400,{error:state.updateFailure}):send(200,updateStatus());}
        if(route==='/api/updates/start'){state.updateJob={id:payload.request_id,target_version:payload.version,state:'installing',progress:60,message:'正在安装，独立服务继续记录',logs:[{time:now,message:'签名与文件校验通过'},{time:now,message:'正在安装'}]};return send(200,updateStatus());}
        if(route==='/api/settings'){state.schedule.reset_day=payload.reset_day;state.schedule.reset_time=payload.reset_time;return send(200,{schedule:state.schedule});}
        if(route==='/api/links'){const d={id:'link-created',label:payload.label,quota:payload.quota,bonus:0,period_up:0,period_down:0,up:0,down:0,today_up:0,today_down:0,month_up:0,month_down:0,up_rate:0,down_rate:0,enabled:payload.enabled};state.devices.push(d);return send(200,{id:d.id});}
        const [,id,action]=route.match(/^\/api\/links\/([^/]+)\/(update|bonus|reset)$/)||[];const d=state.devices.find(d=>d.id===id);if(!d)return send(404,{error:'Unknown fixture link'});
        if(action==='update'){for(const key of ['label','quota','enabled'])if(key in payload)d[key]=payload[key];}
        if(action==='reset'){d.period_up=0;d.period_down=0;}
        if(action==='bonus')d.bonus=Math.max(d.bonus,d.period_up+d.period_down-d.quota)+payload.amount;
        return send(200,{id});
      }
      if(route==='/api/summary'){state.summaryRequests++;return send(200,summary());}
      if(route==='/api/updates/status')return send(200,updateStatus());
      if(route==='/api/session')return send(200,{token:state.csrf});
      if(route==='/api/system')return send(200,{available:state.resourcesAvailable,cpu_percent:17.4,memory_percent:44.6,disk_percent:25.5,memory_used:bytes(.3),memory_total:bytes(.7),disk_used:bytes(2.2),disk_total:bytes(8.6)});
      if(route==='/api/history')return state.failHistory?send(503,{error:'统计数据暂时不可用，请稍后重试'}):send(200,history(url.searchParams));
      if(route==='/api/export.csv'){const result=history(url.searchParams),csv='\uFEFF'+(result.granularity==='hour'?'小时区间(北京时间)':'日期(北京时间)')+',设备标识,设备名称,上传字节,下载字节,合计字节\n'+result.rows.map(r=>[r.day,r.email,r.label,...(r.observed===false?['','','']:[r.up,r.down,r.total])].join(',')).join('\n');res.setHeader('Content-Disposition','attachment; filename="vpn-traffic-fixture.csv"');return send(200,csv,'text/csv; charset=utf-8');}
      const sharing=route.match(/^\/api\/links\/([^/]+)\/sharing$/);if(sharing){const d=summary().devices.find(d=>d.id===sharing[1]);return send(200,{label:d.label,active:d.active,vless:'vless://demo-only-'+d.id+'@example.invalid:443',subscription:'https://example.invalid/s/demo-only-'+d.id+'/subscription.yaml'});}
      const file={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[route];if(!file)return send(404,{error:'Not found'});
      return send(200,fs.readFileSync(path.join(root,file)),file.endsWith('.html')?'text/html; charset=utf-8':file.endsWith('.js')?'text/javascript; charset=utf-8':'text/css; charset=utf-8');
    }catch(error){send(500,{error:error.message});}
  });
  return {server,state,summary};
}
module.exports={createFixture};
if(require.main===module){const {server}=createFixture();server.listen(8765,'127.0.0.1',()=>console.log('Dashboard preview (fixture data only): http://127.0.0.1:8765'));}
