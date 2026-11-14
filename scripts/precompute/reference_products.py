"""Publish DR1 references from trusted analysis jobs, without changing team observations.

Needs SKYSHIFT_JOBS and may read NASA's public DR1 HiPS cube on first use.
"""
import json
from pathlib import Path

from skyshift_server.app import jsonable
from skyshift_server.cached_results import build_frames
from skyshift_server.observations import reference


def export(root: Path):
    for field in sorted(root.glob('*/science.json')):
        folder = field.parent
        center = json.loads((folder / 'frames.json').read_text())['center']
        result = reference(center['ra'], center['dec'])
        epochs = []
        for detector in range(1, 7):
            frames = build_frames(**center, fov=5, clock='months', det=detector, view='refdiff')
            for name, blob in frames.blobs.items():
                output = folder / 'views/refdiff' / name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(blob)
            for epoch in frames.meta['epochs']:
                epoch['data_url'] = f"views/refdiff/{epoch['data_url']}"
                epochs.append(epoch)
        frames.meta['epochs'] = sorted(epochs, key=lambda e: (e['mjd_mid'], e['det']))
        (folder / 'refdiff.json').write_text(json.dumps(jsonable(frames.meta), allow_nan=False))
        field.write_text(json.dumps(jsonable(result), ensure_ascii=False, allow_nan=False))
        print(f'{folder.name}: {len(result["reference"]["spectrum"])} reference points, {len(epochs)} difference frames', flush=True)


if __name__ == '__main__':
    export(Path('web/public/science-data'))
