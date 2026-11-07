'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.VPN_TRAFFIC_PLAYWRIGHT||'playwright');
const {createFixture}=require('./dashboard_fixture.cjs');
const output=path.resolve(process.env.VPN_TRAFFIC_QA_OUTPUT||'test-results/dashboard');
fs.mkdirSync(output,{recursive:true});
(async()=>{
  const {server,state}=createFixture();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});
  const base='http://127.0.0.1:'+server.address().port,errors=[];
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1080},acceptDownloads:true});
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('403')&&!m.text().includes('503')&&!m.text().includes('400'))errors.push(m.text());});
    await page.goto(base);await page.waitForFunction(()=>document.getElementById('health').textContent==='采集正常'&&document.querySelectorAll('#devices-body tr').length===3);
    await page.waitForFunction(()=>document.querySelector('.sidebar [data-system-value="cpu"]').textContent==='17.4%');
    const nav=await page.locator('.nav-item.active').evaluate(el=>({shadow:getComputedStyle(el).boxShadow,before:getComputedStyle(el,'::before').content,left:getComputedStyle(el).borderLeftColor,right:getComputedStyle(el).borderRightColor}));
    assert.equal(nav.shadow,'none');assert.equal(nav.before,'none');assert.equal(nav.left,nav.right);
    assert.equal(await page.locator('#date-range-fields').isVisible(),false);
    assert((await page.locator('#chart path[data-series="download"]').count())>=2,'Sampling gap must split the line');
    assert.equal(await page.locator('#chart rect[data-observed="false"]').count(),1);
    const resources=await page.locator('.sidebar .system-card').boundingBox();assert(resources.x<216&&resources.y>500&&resources.y+resources.height<1080);
    await page.screenshot({path:path.join(output,'dashboard-desktop.png'),fullPage:true});
    await page.locator('#chart rect[data-observed="false"]').click();assert.match(await page.locator('#chart-selection').textContent(),/未采集/);
    await page.selectOption('#history-device','windows');await page.waitForFunction(()=>[...document.querySelectorAll('#history-body tr')].every(row=>row.textContent.includes('leon')));
    assert.equal(await page.locator('#device').inputValue(),'');assert.equal(await page.locator('#devices-body tr').count(),3);
    const downloadPromise=page.waitForEvent('download');await page.locator('#export').click();const download=await downloadPromise;const csvFile=path.join(output,'windows-hourly.csv');await download.saveAs(csvFile);const csv=fs.readFileSync(csvFile,'utf8');assert(csv.includes('小时区间(北京时间)')&&csv.includes(',windows,')&&!csv.includes(',android,'));
    await page.locator('[data-days="7"]').click();await page.waitForFunction(()=>document.getElementById('history-view-label').textContent==='按天汇总');assert.equal(await page.locator('#date-range-fields').isVisible(),true);
    await page.locator('[data-days="30"]').click();await page.waitForFunction(()=>document.getElementById('history-count').textContent==='30 条记录');
    await page.locator('#custom-history').click();await page.fill('#from','2026-11-08');await page.fill('#to','2026-11-07');await page.locator('#filters button[type="submit"]').click();assert.match(await page.locator('#error').textContent(),/开始日期不能晚于结束日期/);
    await page.locator('#last-day').click();await page.waitForFunction(()=>document.getElementById('error').hidden);
    const sun=()=>page.locator('#devices-body tr').filter({has:page.locator('.device-name strong',{hasText:'sun'})});
    await sun().getByRole('button',{name:'历史',exact:true}).click();await page.waitForFunction(()=>document.getElementById('history-device').value==='android'&&document.getElementById('history-details').open);
    await page.locator('#next').click();assert.match(await page.locator('#page-label').textContent(),/2 \/ 2/);await page.locator('#prev').click();
    await page.selectOption('#device','android');await page.waitForFunction(()=>document.querySelectorAll('#devices-body tr').length===1);assert.equal(await page.locator('#history-device').inputValue(),'android');
    await page.selectOption('#device','');await page.waitForFunction(()=>document.querySelectorAll('#devices-body tr').length===3);
    await page.locator('#quota-alert-action').click();await page.fill('#bonus-amount','5');assert.equal(await page.locator('#bonus-preview').textContent(),'5.00 GiB');await page.screenshot({path:path.join(output,'bonus-dialog-desktop.png')});await page.locator('#save-bonus').click();await page.waitForFunction(()=>!document.getElementById('bonus-dialog').open&&document.getElementById('quota-alert').hidden);
    assert(Math.abs(state.devices.find(d=>d.id==='link-demo').bonus/1024**3-5.8)<1e-6);
    state.forceCsrfRefresh=true;
    await page.locator('#new-link').click();await page.fill('#link-label','QA 新手机');await page.fill('#link-quota','2');await page.uncheck('#link-enabled');await page.locator('#save-link').click();await page.waitForFunction(()=>document.getElementById('sharing-dialog').open);
    assert.match(await page.locator('#sharing-vless').inputValue(),/^vless:\/\/demo-only-/);assert.match(await page.locator('#sharing-subscription').inputValue(),/^https:\/\/example.invalid\//);
    const creates=state.requests.filter(r=>r.path==='/api/links');assert.equal(creates.length,2);assert.equal(creates[0].payload.request_id,creates[1].payload.request_id);assert.equal(state.devices.length,4);
    await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>window.__copied=value}}));await page.locator('[data-copy="sharing-vless"]').click();assert.equal(await page.evaluate(()=>window.__copied),await page.locator('#sharing-vless').inputValue());await page.locator('[data-close="sharing-dialog"]').click();
    const created=()=>page.locator('#devices-body tr').filter({hasText:'QA 新手机'});await created().getByRole('button',{name:'编辑',exact:true}).click();await page.fill('#link-label','QA 已编辑');await page.fill('#link-quota','3');await page.locator('#save-link').click();await page.waitForFunction(()=>!document.getElementById('link-dialog').open);assert.equal(state.devices.find(d=>d.id==='link-created').quota,3*1024**3);
    await page.locator('#devices-body tr').filter({hasText:'QA 已编辑'}).getByRole('button',{name:'临时加额',exact:true}).click();await page.fill('#bonus-amount','.25');await page.locator('#save-bonus').click();await page.waitForFunction(()=>!document.getElementById('bonus-dialog').open);assert.equal(state.devices.find(d=>d.id==='link-created').enabled,false);
    await sun().getByRole('button',{name:'编辑',exact:true}).click();page.once('dialog',dialog=>dialog.accept());await page.locator('#reset-period').click();await page.waitForFunction(()=>!document.getElementById('link-dialog').open);assert.equal(state.devices.find(d=>d.id==='android').period_up,0);
    await page.locator('[data-open-settings]').click();await page.fill('#schedule-day','15');await page.fill('#schedule-time','09:30');await page.locator('#save-settings').click();await page.waitForFunction(()=>!document.getElementById('settings-dialog').open&&document.getElementById('system-schedule').textContent.includes('15 日 09:30'));
    await page.locator('#new-link').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#link-dialog').evaluate(el=>el.open),false);
    state.resourcesAvailable=false;await page.evaluate(()=>refreshSystem());assert.equal(await page.locator('.sidebar [data-system-value="cpu"]').textContent(),'—');state.resourcesAvailable=true;await page.evaluate(()=>refreshSystem());
    await page.locator('#system-update').click();state.updateFailure='升级清单签名校验失败';await page.locator('#check-updates').click();await page.waitForFunction(()=>document.getElementById('update-error').textContent.includes('签名'));assert(await page.locator('#install-update').isDisabled());
    state.updateFailure='';await page.locator('#check-updates').click();await page.waitForFunction(()=>!document.getElementById('install-update').disabled);assert.equal(await page.locator('#update-available').textContent(),'v1.6.0');assert(await page.locator('#update-trust').isVisible());
    await page.screenshot({path:path.join(output,'signed-update-desktop.png')});page.once('dialog',dialog=>dialog.accept());await page.locator('#install-update').click();await page.waitForFunction(()=>document.getElementById('update-stage').textContent==='安装新版本');
    const reads=state.summaryRequests;await page.evaluate(()=>refresh());assert.equal(state.summaryRequests,reads);
    await page.locator('[data-close="updates-dialog"]').click();state.installedVersion='1.6.0';state.updateJob={...state.updateJob,state:'succeeded',progress:100,message:'升级完成',logs:[...state.updateJob.logs,{time:Date.now()/1000,message:'健康检查通过'}]};await page.evaluate(()=>refreshUpdateStatus());
    await page.locator('#system-update').click();await page.waitForFunction(()=>document.getElementById('update-stage').textContent==='升级成功');assert.match(await page.locator('#update-log').textContent(),/健康检查通过/);assert(await page.locator('#reload-updated-page').isVisible());
    state.updateJob={...state.updateJob,state:'rolled_back',error:'新版本健康检查失败',message:'已恢复上一版本，流量数据保留'};state.installedVersion='1.5.0';await page.evaluate(()=>refreshUpdateStatus());assert.equal(await page.locator('#update-stage').textContent(),'已恢复上一版本');assert.match(await page.locator('#update-error').textContent(),/健康检查失败/);await page.locator('[data-close="updates-dialog"]').click();
    const overflow=[];
    for(const width of [1440,1024,768,390,320]){
      await page.setViewportSize({width,height:900});await page.waitForTimeout(150);
      if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))overflow.push(width);
      if(width===390){await page.locator('.brand').click();await page.evaluate(()=>document.getElementById('history-details').open=false);await page.screenshot({path:path.join(output,'dashboard-mobile.png'),fullPage:true});await page.locator('#new-link').click();await page.screenshot({path:path.join(output,'create-dialog-mobile.png')});assert(await page.locator('#link-dialog').evaluate(el=>el.getBoundingClientRect().right<=innerWidth));await page.keyboard.press('Escape');assert(await page.locator('footer .system-card').isVisible());}
    }
    assert.deepEqual(overflow,[]);
    state.devices[0].label='<img src=x onerror="window.__xss=1">';await page.evaluate(()=>refresh());assert.equal(await page.evaluate(()=>window.__xss),undefined);assert.equal(await page.locator('#devices-body img').count(),0);
    state.devices.splice(0);await page.evaluate(()=>refresh());assert.match(await page.locator('#devices-body').textContent(),/暂无连接/);assert.match(await page.locator('#quota-overview').textContent(),/暂无连接/);
    assert.deepEqual(errors,[]);
    const report={passed:true,signedUpdateCheck:true,invalidSignatureBlocked:true,backgroundUpgradeProgress:true,upgradeLogsAndRollbackResult:true,noBrightNavigationEdge:true,samplingGapsPreserved:true,independentHistoryFilter:true,hourlyCsv:true,dailyAndCustomRanges:true,pagination:true,overviewFilter:true,temporaryAllowanceRestores:true,createAndSharing:true,csrfRefreshKeepsRequestId:true,editAndReset:true,manualDisabledRemainsDisabled:true,globalSchedule:true,mobileDialogs:true,resourcesDesktopAndMobile:true,staleResourcesExplicit:true,safeLabels:true,emptyState:true,viewports:[1440,1024,768,390,320],overflow,errors};fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
