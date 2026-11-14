import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_PACKAGE||'playwright');
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const base=process.env.SKYSHIFT_URL||'http://127.0.0.1:8082';
async function ready(){await page.waitForSelector('#status.ready');await page.waitForFunction(()=>{const c=document.querySelector('.bk-canvas');return c&&c.width>20});if(await page.locator('.bk-play').getAttribute('aria-pressed')==='true')await page.locator('.bk-play').click()}
async function target(slug){await page.locator(`[data-slug="${slug}"]`).click();await ready()}
try{
 await page.goto(base);await ready();
 const targets=await page.locator('[data-slug][data-published="true"]').evaluateAll(bs=>bs.map(b=>b.dataset.slug));const report=[];
 for(const slug of targets){
  const start=Date.now();await target(slug);
  if(['barnard','wise0855','epsindib','rwaur','rdra'].includes(slug))await page.waitForFunction(()=>document.querySelector('#analysis-status')?.textContent==='');
  const pixels=await page.locator('.bk-canvas').evaluate(c=>{const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;return new Set(Array.from(a).filter((_,i)=>i%4===0)).size});assert.ok(pixels>10,slug+' has real dynamic range');
  const before=Number(await page.locator('.bk-axis').getAttribute('aria-valuenow'));
  const max=Number(await page.locator('.bk-axis').getAttribute('aria-valuemax'));
  await page.locator('.bk-next').click();assert.equal(Number(await page.locator('.bk-axis').getAttribute('aria-valuenow')),before%max+1);
  await page.locator('button[data-mode="subtract"]').click();assert.equal(await page.locator('button[data-mode="subtract"]').getAttribute('aria-pressed'),'true');
  await page.locator('button[data-mode="add"]').click();assert.equal(await page.locator('button[data-mode="add"]').getAttribute('aria-pressed'),'true');
  await page.locator('button[data-mode="blink"]').click();
  report.push({slug,ms:Date.now()-start,pixels,status:await page.locator('#status').innerText()});
 }
 await target('wise0855');await page.waitForFunction(()=>document.querySelectorAll('#depth tbody tr').length>0);
 assert.equal(await page.locator('.bk-canvas').count(),1);assert.equal(await page.locator('#spectrum svg').count(),1);
 await page.locator('[data-det="2"]').click();await ready();await page.locator('[data-fov="15"]').click();await ready();assert.match(await page.locator('#sampling').innerText(),/3.075/);
 await page.locator('[data-fov="5"]').click();await ready();await page.locator('[data-det="6"]').click();await ready();
 await page.locator('.bk-play').click();assert.equal(await page.locator('.bk-play').getAttribute('aria-pressed'),'true');await page.locator('.bk-play').click();
 await page.locator('[data-clock="hours"]').click();await ready();assert.match(await page.locator('#status').innerText(),/逐次曝光/);
 await page.locator('[data-clock="months"]').click();await ready();
 for(const value of ['resid','model','refdiff','raw']){await page.selectOption('#view',value);await ready()}
 await page.locator('#guxing-panel').evaluate(e=>e.open=true);await page.waitForFunction(()=>document.querySelector('guxing-card')?.shadowRoot?.querySelector('.zh'));
 await page.locator('#guxing-history').evaluate(e=>e.open=true);await page.waitForSelector('#guest-gallery img');assert.equal(await page.locator('#guest-gallery img').count(),6);
 await page.locator('#guxing-planets').evaluate(e=>e.open=true);await page.waitForSelector('#planet-events table');
 await page.locator('#score-panel').evaluate(e=>e.open=true);await page.waitForSelector('#score-content .sc');
 await page.locator('.bk-canvas').click({position:{x:50,y:50}});assert.equal(await page.locator('#vote-panel').getAttribute('open'),'');
 // Upstream UI seams are deterministic mocks; no remote vote is submitted.
 await page.route('**/api/gaia?*',route=>route.fulfill({json:{gaia:[{id:'9007199254740993',ra:133.7599,dec:-7.2422,pmra:0,pmdec:0,parallax:null,g:16}]}}));
 await page.locator('[data-service="gaia"]').click();await page.waitForFunction(()=>document.querySelector('#enrichment-status')?.textContent.includes('1 条记录'));
 await page.route('**/api/enrichment?*',route=>route.fulfill({status:502,json:{error:{code:'upstream',message:'mock outage'}}}));
 await page.locator('[data-service="catwise"]').click();await page.waitForFunction(()=>document.querySelector('#enrichment-status')?.textContent.includes('mock outage'));assert.ok(await page.locator('[data-service="catwise"]').isEnabled());
 for(const host of ['cdn.jsdelivr.net','unpkg.com'])await page.route(`https://${host}/**`,r=>r.abort());
 await page.locator('#sky-panel').evaluate(e=>e.open=true);await page.locator('#sky-content').scrollIntoViewIfNeeded();
 await page.waitForFunction(()=>document.querySelector('#sky-content')?.textContent?.includes('unavailable'),{},{timeout:20000});
 await page.locator('.bk-next').click();assert.ok(await page.locator('#status.ready').count());
 const gif=await page.request.get(base+'/api/export?ra=133.7599&dec=-7.2422&det=6&format=gif');assert.equal(gif.status(),200);assert.ok((await gif.body()).subarray(0,3).toString()==='GIF');
 // A pasted coordinate inside a prepared field uses its static target data.
 await page.locator('#query').fill('08h55m10.831s -7d14m42.53s');await page.locator('#search button').click();await ready();assert.match(await page.locator('#target-name').innerText(),/WISE/);
 // The background-job UI is mocked; browser checks never start a real sky scan.
 await page.route('**/api/frames?ra=0&dec=0&*',r=>r.fulfill({status:404,json:{error:{code:'not_found',message:'No precomputed field fixture'}}}));
 await page.locator('#query').fill('0 0');await page.locator('#search button').click();await page.waitForSelector('#status.error');
 await page.route('**/api/jobs?*',r=>r.fulfill({json:{id:'mock-job',state:'queued',progress:[],error:null}}));
 await page.route('**/api/jobs/mock-job',r=>r.fulfill({json:{id:'mock-job',state:'done',progress:['fixture complete'],error:null}}));
 const field=await (await page.request.get(base+'/data/wise0855/frames.json')).json();field.epochs=field.epochs.filter(e=>e.det===2).map(e=>({...e,data_url:'/data/wise0855/'+e.data_url}));field.center={ra:0,dec:0};
 await page.route('**/api/frames?ra=0&dec=0&*',r=>r.fulfill({json:field}));await page.locator('#start-analysis').click();await ready();
 await page.goto(base);await ready();await page.waitForFunction(()=>document.querySelectorAll('#depth tbody tr').length>0);
 await mkdir('../docs/previews',{recursive:true});await page.screenshot({path:'../docs/previews/desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.goto(base);await ready();await page.evaluate(()=>window.scrollTo(0,0));
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no phone overflow');await page.screenshot({path:'../docs/previews/mobile.png'});await page.locator('.bk-canvas').scrollIntoViewIfNeeded();await page.screenshot({path:'../docs/previews/mobile-player.png'});
 assert.deepEqual(errors,[]);
 const result={report,browserErrors:errors,mobile:'390px: no overflow',checks:['eight published real targets','one upstream player and one spectrum','subtract/add/play/clock/fov/detector','formal object card','native sky offline fallback (mocked CDN)','validation scoreboard','local vote pick only','model/residual/reference views','Guxing/history/planets','mocked Gaia and upstream recovery','real GIF export','pasted coordinates use featured static field','mocked background-job UI']};
 await writeFile('../docs/previews/browser-check.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{await browser.close()}
