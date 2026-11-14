"""Export five real cached fields to contract-shaped static twins. No network.

PYTHONPATH=core/src:server/src python scripts/precompute/local_results.py
"""
import argparse
import json
from pathlib import Path

from skyshift_server.app import jsonable
from skyshift_server.blink import build_frames, build_object
from skyshift_server.analysis import build_analysis
from skyshift_server.observations import science_result
from skyshift_server.local_results import jobs, target_for


def export(output, n_slides=100):
    manifest = []
    for job in jobs():
        target = target_for(job)
        if not target or target['slug'] == '3i-atlas':
            continue
        dest = output / target['slug']; dest.mkdir(parents=True, exist_ok=True)
        ra, dec = job['ra'], job['dec']
        epochs = []
        frame_meta = None
        for det in range(1, 7):
            frames = build_frames(ra, dec, 5, 'months', det)
            frame_meta = frames.meta
            epochs.extend(frames.meta['epochs'])
            for name, blob in frames.blobs.items():
                path = dest / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(blob)
        frame_meta['epochs'] = sorted(epochs, key=lambda p: (p['mjd_mid'], p['det']))
        for view in ('resid','model'):
            view_epochs=[]
            for det in range(1,7):
                variant=build_frames(ra,dec,5,'months',det,view=view)
                for name,blob in variant.blobs.items():
                    path=dest/'views'/view/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(blob)
                for epoch in variant.meta['epochs']:
                    epoch['data_url']=f"views/{view}/{epoch['data_url']}"
                view_epochs.extend(variant.meta['epochs'])
            variant.meta['epochs']=sorted(view_epochs,key=lambda e:(e['mjd_mid'],e['det']))
            (dest/f'frames_{view}.json').write_text(json.dumps(jsonable(variant.meta),allow_nan=False))
        obj = build_object(ra, dec)
        default_det = 6 if target['slug'] == 'wise0855' else 2
        analysis = build_analysis(ra, dec, default_det, n_slides=n_slides)
        for name, content in [('frames.json', frame_meta), ('object.json', obj), ('analysis.json', analysis), ('science.json', science_result(ra,dec))]:
            (dest / name).write_text(json.dumps(jsonable(content), ensure_ascii=False, allow_nan=False))
        manifest.append(dict(slug=target['slug'], name=target['name'], ra=ra, dec=dec,
                             default_det=default_det, n_frames=len(job['res']['meta']),
                             n_visits=analysis['source']['n_visits']))
        print(target['slug'], len(frame_meta['epochs']), 'epochs;', len(analysis['cold_candidates']), 'cold groups', flush=True)
    order = {slug: i for i, slug in enumerate(['barnard', 'wise0855', 'epsindib', 'rwaur', 'rdra'])}
    manifest.sort(key=lambda t: order.get(t['slug'], 99))
    (output / 'targets.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('web/public/data'))
    parser.add_argument('--slides', type=int, default=100)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    export(args.output, args.slides)
