import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../api/science-client', async original => ({...await original<typeof import('../api/science-client')>(), loadScience:vi.fn()}));
import { loadScience } from '../api/science-client';
import { mountWorkbench } from './workbench';
import { workspaceState } from './workspace-state';
import { targetBySlug, viewForTarget } from '../app/state';

afterEach(()=>{document.body.replaceChildren();vi.unstubAllGlobals();});
it('retries a failed extended-science load on returning to a section and caches success',async()=>{
  const data=JSON.parse(readFileSync(join(__dirname,'../../public/science-data/barnard/science.json'),'utf8'));
  vi.mocked(loadScience).mockRejectedValueOnce(new Error('Temporary network failure')).mockResolvedValue(data);
  const host=document.createElement('div'),cardElement=document.createElement('section');document.body.append(host);
  const wb=mountWorkbench(host,{card:{show:vi.fn(),status:vi.fn()},cardElement,layer:vi.fn(),locate:vi.fn(),reference:vi.fn(),rebuilt:vi.fn()});
  wb.active(false);wb.select(viewForTarget(targetBySlug('barnard')!),'raw');
  wb.mode(workspaceState('analyze/spectrum'));wb.active(true);
  // Re-selecting an equivalent view does not cancel its in-flight request.
  wb.select({...viewForTarget(targetBySlug('barnard')!)},'raw');
  await vi.waitFor(()=>expect(host.textContent).toContain('Extended analysis could not be loaded'));
  expect(loadScience).toHaveBeenCalledTimes(1);
  wb.mode(workspaceState('analyze/motion'));wb.mode(workspaceState('analyze/spectrum'));
  await vi.waitFor(()=>expect(host.textContent).toContain('Analysis footprint:'));
  expect(loadScience).toHaveBeenCalledTimes(2);
  expect(host.textContent).not.toContain('Extended analysis could not be loaded');
  expect(host.textContent).toContain('Analysis footprint:');
  wb.mode(workspaceState('analyze/motion'));wb.mode(workspaceState('analyze/spectrum'));
  expect(loadScience).toHaveBeenCalledTimes(2);
});
