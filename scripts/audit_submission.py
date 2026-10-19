"""Check Git candidates against actual local secret values without printing them."""
import json
from pathlib import Path
import subprocess
import re

ROOT=Path(__file__).resolve().parents[1]
files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
values=[]
for path,keys in [(ROOT/'.local/operator.json',['password']),
                  (ROOT/'.local/app-secrets.json',['cluster_password','db_password','django_secret_key']),
                  (ROOT/'terraform-selectel/terraform.tfvars.json',['route_probe_key'])]:
    if path.exists():
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        values.extend(str(data[k]).encode() for k in keys if data.get(k))
failures=[]
for name in files:
    path=ROOT/name
    if not path.is_file():continue
    parts=Path(name).parts
    if '.local' in parts or '.terraform' in parts or '.tools' in parts or name.endswith(('.tfstate','.tfstate.backup','.tfvars','.tfvars.json','.tfplan','.sqlite3')):
        failures.append(name);continue
    raw=path.read_bytes()
    if re.search(rb'(?m)^-----BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY-----\r?$',raw) or any(v and v in raw for v in values):
        failures.append(name)
if failures:raise SystemExit('Submission audit failed in: '+', '.join(sorted(set(failures))))
print('Submission audit PASS: '+str(len(files))+' Git candidate files checked; actual local secret values absent.')
