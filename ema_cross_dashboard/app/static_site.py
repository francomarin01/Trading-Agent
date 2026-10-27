from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS = ('dashboard.json', 'asset_rotation.json')


def prepare_static_site(source: Path | None = None, target: Path | None = None) -> Path:
    """Create a safe, read-only site bundle for static hosting."""
    source = source or ROOT / 'web'
    target = target or ROOT / 'dist'
    if target.exists():
        shutil.rmtree(target)
    (target / 'data').mkdir(parents=True)
    for name in ('app.js', 'styles.css'):
        candidate = source / name
        if candidate.exists():
            shutil.copy2(candidate, target / name)
    index = (source / 'index.html').read_text(encoding='utf-8')
    index = index.replace('data-deployment="local"', 'data-deployment="static"', 1)
    index = index.replace('<html lang="es">', '<html lang="es" data-deployment="static">', 1)
    (target / 'index.html').write_text(index, encoding='utf-8')
    for name in DEFAULT_ARTIFACTS:
        artifact = source / 'data' / name
        if not artifact.exists():
            raise FileNotFoundError(f'Missing generated artifact: {artifact}')
        shutil.copy2(artifact, target / 'data' / name)
    return target


if __name__ == '__main__':
    print(f'Static site prepared: {prepare_static_site()}')
