const assert=require('assert'),fs=require('fs'),path=require('path');
const {chromium}=require('C:/Users/xishu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const work=path.resolve(__dirname,'../../.work/45.136.178.163');
const access=JSON.parse(fs.readFileSync(path.join(work,'traffic-web-access.json'),'utf8'));
const output=path.join(work,'traffic-qa-1.2.0');fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1080},httpCredentials:{username:access.username,password:access.password}});
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(access.url);await page.waitForFunction(()=>document.getElementById('health').textContent==='采集正常');
    const first=await (await context.request.get(access.url+'api/summary')).json();const plan=first.schedule;
    assert(first.devices.every(d=>!('reset_day' in d)&&!('next_reset' in d)));
    await page.locator('#system-settings').click();assert.strictEqual(await page.locator('#schedule-day').inputValue(),String(plan.reset_day));
    assert.strictEqual(await page.locator('#schedule-time').inputValue(),plan.reset_time);
    await page.screenshot({path:path.join(output,'system-settings-desktop.png')});
    await page.locator('#save-settings').click();await page.waitForFunction(()=>!document.getElementById('settings-dialog').open);
    await page.locator('#new-link').click();assert.strictEqual(await page.locator('#link-day').count(),0);assert.strictEqual(await page.locator('#link-time').count(),0);
    assert((await page.locator('#link-next-reset').innerText()).includes('系统统一'));
    const label='QA 系统临时 UI '+Date.now();await page.fill('#link-label',label);await page.fill('#link-quota','1');await page.uncheck('#link-enabled');await page.locator('#save-link').click();
    await page.waitForFunction(()=>document.getElementById('sharing-dialog').open);
    await page.locator('[data-close="sharing-dialog"]').click();
    let summary=await (await context.request.get(access.url+'api/summary')).json();let device=summary.devices.find(d=>d.label===label);assert(device&&!device.enabled&&device.quota===1024**3);
    fs.writeFileSync(path.join(output,'fixture.json'),JSON.stringify({id:device.id,label}));
    await page.locator('#devices-body tr').filter({hasText:label}).getByRole('button',{name:'临时加额',exact:true}).click();
    await page.fill('#bonus-amount','0.25');await page.screenshot({path:path.join(output,'temporary-allowance-desktop.png')});
    await page.locator('#save-bonus').click();await page.waitForFunction(()=>!document.getElementById('bonus-dialog').open);
    summary=await (await context.request.get(access.url+'api/summary')).json();device=summary.devices.find(d=>d.id===device.id);
    assert(device.bonus===.25*1024**3&&device.quota===1024**3&&device.quota_effective===1.25*1024**3&&!device.enabled&&!device.active);
    const token=(await (await context.request.get(access.url+'api/session')).json()).token,origin=new URL(access.url).origin;
    const rejected=await context.request.post(access.url+'api/links/'+device.id+'/update',{headers:{Origin:origin,'X-VPN-CSRF':token},data:{reset_day:15}});assert.strictEqual(rejected.status(),400);
    const forbidden=await context.request.post(access.url+'api/settings',{headers:{Origin:'https://foreign.invalid','X-VPN-CSRF':token},data:{reset_day:1,reset_time:'00:00'}});assert.strictEqual(forbidden.status(),403);
    for(const d of first.devices){const current=summary.devices.find(x=>x.id===d.id);assert(current.quota===d.quota&&current.total>=d.total&&current.period_used>=d.period_used);}
    assert.deepStrictEqual(summary.schedule,plan);
    await page.locator('#system-settings').click();await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,'system-settings-mobile.png')});await page.locator('[data-close="settings-dialog"]').first().click();
    await page.locator('#devices-body tr').filter({hasText:label}).getByRole('button',{name:'临时加额',exact:true}).click();await page.screenshot({path:path.join(output,'temporary-allowance-mobile.png')});
    assert.strictEqual(errors.length,0,errors.join('\n'));
    const report={systemScheduleEditor:true,individualScheduleInputsRemoved:true,individualScheduleApiRejected:true,newIdentityInheritsGlobalPlan:true,temporaryAllowanceSaved:true,baseQuotaUnchanged:true,manualDisabledIdentityRemainsDisabled:true,existingUserQuotasAndUsagePreserved:true,settingsCsrfProtected:true,mobileDialogsFit:true,javascriptErrors:errors};
    fs.writeFileSync(path.join(output,'global-web-qa.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
