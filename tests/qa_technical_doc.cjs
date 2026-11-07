const assert=require('assert'),fs=require('fs'),path=require('path');
const {pathToFileURL}=require('url');
const {chromium}=require('C:/Users/xishu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=path.resolve(__dirname,'../../deliverables/45.136.178.163/traffic-monitor/1.3.0');
const qa=path.resolve(__dirname,'../../.work/45.136.178.163/traffic-qa-1.3.0');
(async()=>{
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1050}});const external=[];
    await page.route(/^https?:/,route=>{external.push(route.request().url());return route.abort();});
    await page.goto(pathToFileURL(path.join(out,'VPN流量监控技术方案.html')).href);
    const shape=await page.evaluate(()=>({sections:document.querySelectorAll('main section').length,missingAnchors:[...document.querySelectorAll('a[href^="#"]')].filter(a=>!document.getElementById(a.hash.slice(1))).length,overflow:document.documentElement.scrollWidth>innerWidth+1}));
    assert(shape.sections===8&&shape.missingAnchors===0&&!shape.overflow);
    await page.screenshot({path:path.join(qa,'technical-doc-desktop.png')});
    await page.locator('#resources').screenshot({path:path.join(qa,'technical-doc-resources.png')});
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>window.scrollTo(0,0));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(qa,'technical-doc-mobile.png')});assert.strictEqual(external.length,0);
    fs.writeFileSync(path.join(qa,'technical-doc-qa.json'),JSON.stringify({...shape,mobileOverflow:false,externalRequests:external.length},null,2));
    console.log(JSON.stringify({...shape,mobileOverflow:false,externalRequests:external.length}));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
