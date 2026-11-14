import fixtureFrames from '../../../fixtures/api/frames_barnard.json?url';
import fixtureHours from '../../../fixtures/api/frames_comet.json?url';
import fixtureObject from '../../../fixtures/api/object_barnard.json?url';
import fixtureGaia from '../../../fixtures/api/gaia_barnard.json?url';
import fixtureKnown from '../../../fixtures/api/known_comet.json?url';
import fixtureResolve from '../../../fixtures/api/resolve_barnard.json?url';
import { TARGETS, readView } from '../app/state';

type Entry = { url: string; status: number; ms: number; bytes: number; body: string };
const endpoints = ['health', 'resolve', 'frames', 'object', 'gaia', 'known', 'science', 'analysis'] as const;
export type Endpoint = typeof endpoints[number];
export function requestUrl(endpoint: Endpoint, q: {ra:number; dec:number; clock:string; det:number; fov:number; r:number; mjd:number; name:string}) {
  if (!Number.isFinite(q.ra) || q.ra < 0 || q.ra >= 360 || !Number.isFinite(q.dec) || Math.abs(q.dec) > 90) throw new Error('Use RA 0–360° and Dec −90–90°.');
  const p = new URLSearchParams();
  if (endpoint === 'resolve') p.set('q', q.name);
  else if (endpoint !== 'health') {
    p.set('ra', String(q.ra)); p.set('dec', String(q.dec));
    if (endpoint === 'frames') for (const k of ['clock', 'det', 'fov'] as const) p.set(k, String(q[k]));
    if (endpoint === 'analysis') p.set('det', String(q.det));
    if (endpoint === 'gaia' || endpoint === 'known') {
      if (!(q.r > 0 && q.r <= 60)) throw new Error('Radius must be greater than 0 and at most 60 arcmin.');
      p.set('r', String(q.r));
    }
    if (endpoint === 'known') {
      if (!Number.isFinite(q.mjd)) throw new Error('Enter a finite MJD (UTC).');
      p.set('mjd', String(q.mjd));
    }
  }
  return `/api/${endpoint}${p.size ? `?${p}` : ''}`;
}

export function mountInspector(host: HTMLElement) {
  host.innerHTML = `<h2 id="tl-inspector-h" tabindex="-1">API inspector</h2>
    <p>Inspect requests, response times, byte counts and raw JSON. Live calls use the local API. Test fixtures are explicitly synthetic and stay in this inspector.</p>
    <form class="tl-inspector-form">
      <label>Source <select class="input" name="source"><option value="live">Live API</option><option value="static">Published static data</option><option value="fixture">Synthetic test fixtures</option></select></label>
      <label>Target <select class="input" name="target">${TARGETS.map(t => `<option value="${t.slug}">${t.name}</option>`).join('')}<option value="custom">Custom coordinates</option></select></label>
      <label>Endpoint <select class="input" name="endpoint">${endpoints.map(e => `<option>${e}</option>`).join('')}</select></label>
      <label>RA · degrees <input class="input" name="ra" type="number" step="any" value="269.4462" required></label>
      <label>Dec · degrees <input class="input" name="dec" type="number" step="any" value="4.76883" required></label>
      <label>Clock <select class="input" name="clock"><option>months</option><option>hours</option></select></label>
      <label>Detector <select class="input" name="det">${[1,2,3,4,5,6].map(d=>`<option ${d===2?'selected':''}>${d}</option>`).join('')}</select></label>
      <label>Field · arcmin <select class="input" name="fov"><option>5</option><option>15</option></select></label>
      <label>Radius · arcmin <input class="input" name="r" type="number" min="0.01" max="60" step="any" value="5" required></label>
      <label>MJD · UTC <input class="input" name="mjd" type="number" step="any" value="60938.62" required></label>
      <button class="btn" type="submit">Send request</button>
    </form>
    <div class="tl-actions"><button class="btn" data-inspect="checks">Run API checks</button><button class="btn" data-inspect="copy">Copy response JSON</button><button class="btn" data-inspect="export">Export session</button></div>
    <p class="tl-inspector-status" role="status"></p><div class="tl-inspector-history" aria-label="Request history"></div>
    <pre class="tl-inspector-json" tabindex="0" aria-label="Response JSON">Select a source and send a request.</pre>`;
  const form = host.querySelector('form')!;
  const field = (name: string) => form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement;
  const status = host.querySelector<HTMLElement>('.tl-inspector-status')!;
  const history = host.querySelector<HTMLElement>('.tl-inspector-history')!;
  const output = host.querySelector<HTMLElement>('pre')!;
  const records: Entry[] = [];
  let ctl = new AbortController();
  function clock() { field('fov').innerHTML = (field('clock').value === 'hours' ? [20,60] : [5,15]).map(n=>`<option>${n}</option>`).join(''); }
  field('clock').onchange = clock;
  field('target').onchange = () => {
    const t = TARGETS.find(t=>t.slug === field('target').value);
    if (t) { field('ra').value=String(t.ra); field('dec').value=String(t.dec); field('clock').value=t.clock; clock(); field('fov').value=String(t.fov); field('det').value=String(t.det); }
  };
  for (const key of ['ra','dec']) field(key).oninput=()=>{field('target').value='custom'};
  function show(record: Entry) {
    let body = record.body;
    try { body=JSON.stringify(JSON.parse(body),null,2); } catch { /* keep plain-text errors */ }
    output.textContent=body;
    status.textContent=`HTTP ${record.status} · ${record.ms} ms · ${record.bytes.toLocaleString()} bytes · ${record.url}`;
  }
  async function request(url: string, signal: AbortSignal): Promise<Entry> {
    const start = performance.now(), response = await fetch(url,{signal}), body = await response.text();
    if (signal.aborted) throw new DOMException('Aborted','AbortError');
    const record={url,status:response.status,ms:Math.round(performance.now()-start),bytes:new TextEncoder().encode(body).length,body};
    records.push(record); if (records.length > 50) { records.shift(); history.firstElementChild?.remove(); }
    const button=document.createElement('button');button.type='button';button.className='btn';button.textContent=`${record.status} · ${url}`;button.onclick=()=>show(record);history.append(button);
    show(record); return record;
  }
  const begin=()=>{ctl.abort();ctl=new AbortController();status.textContent='Requesting…';return ctl.signal};
  form.onsubmit=async e=>{
    e.preventDefault();const signal=begin();
    try {
      const endpoint=field('endpoint').value as Endpoint;
      const q={ra:+field('ra').value,dec:+field('dec').value,clock:field('clock').value,det:+field('det').value,fov:+field('fov').value,r:+field('r').value,mjd:+field('mjd').value,name:TARGETS.find(t=>t.slug===field('target').value)?.name ?? `${field('ra').value} ${field('dec').value}`};
      let url=requestUrl(endpoint,q);
      if (field('source').value==='fixture') {
        const fixtures:Partial<Record<Endpoint,string>>={frames:q.clock==='hours'?fixtureHours:fixtureFrames,object:fixtureObject,gaia:fixtureGaia,known:fixtureKnown,resolve:fixtureResolve};
        if (!fixtures[endpoint]) throw new Error('No synthetic fixture for this endpoint. Choose frames, object, gaia, known or resolve.');
        url=fixtures[endpoint]!;
      } else if (field('source').value==='static') {
        const slug=field('target').value;
        if (slug==='custom') throw new Error('Choose a featured target for published static data.');
        if (!['frames','object','science','analysis'].includes(endpoint)) throw new Error('Published static files cover frames, object, science and analysis.');
        if (endpoint==='frames') {
          const index=await request(`/data/${slug}/index.json`,signal);
          if(index.status!==200) throw new Error('This target has no published frame index.');
          const data=JSON.parse(index.body);
          const found=data.frames.find((f:{clock:string;fov:number;dets:number[]})=>f.clock===q.clock&&f.fov===q.fov&&f.dets.includes(q.det));
          if(!found) throw new Error('No published frames for this clock, field and detector.');
          url=`/data/${slug}/${found.file}`;
        } else url=`/${endpoint==='object'?'data':'science-data'}/${slug}/${endpoint}.json`;
      }
      await request(url,signal);
    } catch(e) { if(!signal.aborted)status.textContent=(e as Error).message; }
  };
  host.querySelector<HTMLButtonElement>('[data-inspect="checks"]')!.onclick=async()=>{
    const signal=begin();
    try {
      const health=await request('/api/health',signal), resolve=await request('/api/resolve?q=Barnard',signal), invalid=await request('/api/frames?ra=400&dec=95',signal);
      const results=[health.status===200,resolve.status===200,invalid.status===400||invalid.status===422];
      status.textContent=`${results.filter(Boolean).length}/3 API checks passed: health, built-in name resolution, invalid-coordinate rejection. These check API behavior, not scientific accuracy.`;
    } catch(e) {if(!signal.aborted)status.textContent=(e as Error).message;}
  };
  host.querySelector<HTMLButtonElement>('[data-inspect="copy"]')!.onclick=async()=>{try{await navigator.clipboard.writeText(output.textContent??'');status.textContent='Response copied.';}catch{status.textContent='Select the response text and copy it.'}};
  host.querySelector<HTMLButtonElement>('[data-inspect="export"]')!.onclick=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify({exported:new Date().toISOString(),requests:records},null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='skyshift-api-session.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
  };
  return {hide(){ctl.abort()},destroy(){ctl.abort();host.replaceChildren()},show(){const v=readView();if(v&&records.length===0){field('target').value=v.slug??'custom';field('ra').value=String(v.ra);field('dec').value=String(v.dec);field('clock').value=v.clock;clock();field('fov').value=String(v.fov);field('det').value=String(v.det)}}};
}
