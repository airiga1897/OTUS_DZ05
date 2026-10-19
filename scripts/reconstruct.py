"""Reconstruct seed data and export a local fixture, explicitly recording provenance."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
local = ROOT / '.local'
local.mkdir(exist_ok=True)
database = local / 'reconstruction.sqlite3'
fixture = local / 'reconstructed-portal.json'
if database.exists() or fixture.exists():
    raise SystemExit('Existing reconstruction preserved; select a new output before rerunning.')
env = dict(os.environ, DJANGO_SECRET_KEY='local-reconstruction-only',
           DJANGO_SETTINGS_MODULE='config.reconstruction_settings', RECONSTRUCTION_DB=str(database))
manage = [sys.executable, str(ROOT / 'app/manage.py')]
for args in [['migrate', '--noinput'], ['seed_lessons'],
             ['dumpdata', '--natural-foreign', '--exclude', 'contenttypes', '--exclude', 'auth.permission',
              '--exclude', 'sessions', '--indent', '2', '--output', str(fixture)]]:
    subprocess.run(manage + args, env=env, check=True)
rows = json.loads(fixture.read_text(encoding='utf-8'))
report = {
    'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'origin': 'reconstructed_from_DZ04_seed_lessons_source',
    'historical_postgresql_data_migrated': False,
    'mysql_import_performed': False,
    'models': {name: sum(r['model'] == name for r in rows) for name in sorted({r['model'] for r in rows})},
    'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
    'article_pks': [r['pk'] for r in rows if r['model'] == 'knowledge.article'],
    'media_files': 0,
}
(ROOT / 'docs/reconstruction.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, indent=2))
