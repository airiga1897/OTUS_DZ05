"""Run only DZ05 Terraform with local operator credentials kept out of argv."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def operator_env():
    config = json.loads((ROOT / '.local/operator.json').read_text(encoding='utf-8-sig'))
    if config['project_id'] != 'fdce7ecd9cf742a6a4eda91e927a09fb' or config['region'] != 'ru-9':
        raise ValueError('Unexpected Selectel project or region')
    env = dict(os.environ)
    for key, field in {'OS_AUTH_URL':'auth_url', 'OS_PROJECT_ID':'project_id',
                       'OS_REGION_NAME':'region', 'OS_USER_DOMAIN_NAME':'domain_name',
                       'OS_USERNAME':'username', 'OS_PASSWORD':'password'}.items():
        env[key] = config[field]
    env['TF_CLI_CONFIG_FILE'] = str(ROOT / 'terraform-selectel.rc')
    env['TF_IN_AUTOMATION'] = '1'
    return env

def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit('Usage: tf.py <terraform command>')
    if args[0] in ('apply','destroy'):
        raise SystemExit('Use lab.py deploy/destroy: it validates own plans and quotas.')
    binary = ROOT / '.tools/terraform.exe'
    if not binary.exists():
        binary = ROOT.parent / 'OTUS_DZ04/.tools/terraform.exe'
    return subprocess.call([str(binary), '-chdir=' + str(ROOT / 'terraform-selectel')] + args,
                           env=operator_env())

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError):
        raise SystemExit('Check local operator config and Terraform installation; credentials hidden.') from None
