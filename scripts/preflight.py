"""Read-only Selectel quota discovery. Never creates cloud resources."""
import argparse
import datetime
import json
import sys
from pathlib import Path
import urllib.error
import time
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from selectel_api import authenticate, config_from_env, request_json

def retry_read(operation):
    for attempt in range(3):
        try:return operation()
        except urllib.error.HTTPError as error:
            if error.code not in (408,429,500,502,503,504) or attempt==2:raise
        except (urllib.error.URLError,TimeoutError,ConnectionError):
            if attempt==2:raise
        print('Temporary read-only API error; retry '+str(attempt+2),flush=True)
        time.sleep(2**attempt)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--auth-yaml', type=Path,
                        help='Existing local credentials: read only; never copied or printed')
    parser.add_argument('--auth-json', type=Path, help='Local operator config; excluded from Git')
    args = parser.parse_args()
    config = json.loads(args.auth_json.read_text(encoding='utf-8-sig')) if args.auth_json else (yaml.safe_load(args.auth_yaml.read_text(encoding='utf-8'))['selectel_fence_auth']
              if args.auth_yaml else config_from_env())
    if config['project_id'] != 'fdce7ecd9cf742a6a4eda91e927a09fb' or config['region'] != 'ru-9':
        raise ValueError('Unexpected Selectel project/region')
    token, catalog = retry_read(lambda:authenticate(config))
    endpoints = {}
    for service in catalog:
        for e in service['endpoints']:
            if e.get('interface') == 'public' and e.get('region') == config['region'] and e['url'].startswith('https://'):
                endpoints[service['type']] = e['url'].rstrip('/')
    project = config['project_id']
    queries = {
        'compute': ('compute', '/os-quota-sets/' + project + '/detail'),
        'volumes': ('volumev3', '/os-quota-sets/' + project + '?usage=True'),
        'network': ('network', '/v2.0/quotas/' + project + '/details.json'),
        'servers': ('compute', '/servers/detail'),
        'flavors': ('compute', '/flavors/detail'),
        'subnets': ('network', '/v2.0/subnets'),
    }
    raw = {}
    report = {'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'region': config['region'], 'paid_resources_created': False, 'quotas': {}, 'errors': {}}
    for name, (kind, suffix) in queries.items():
        try:
            raw[name], _ = retry_read(lambda:request_json(endpoints[kind] + suffix, token=token))
            if name in ('compute', 'volumes', 'network'):
                report['quotas'][name] = json.loads(json.dumps(raw[name]))
                report['quotas'][name].get('quota_set', {}).pop('id', None)
            print(name + ': OK')
        except urllib.error.HTTPError as e:
            report['errors'][name] = 'HTTP ' + str(e.code)
            print(name + ': HTTP ' + str(e.code))
    local = ROOT / '.local'
    local.mkdir(exist_ok=True)
    (local / 'discovery.json').write_text(json.dumps(raw, indent=2), encoding='utf-8')
    (ROOT / 'docs/preflight.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Read-only discovery saved; credentials and token not saved.')

if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as e:
        sys.exit('Selectel authentication HTTP ' + str(e.code) + '; credentials hidden')
    except (OSError, ValueError, KeyError):
        sys.exit('Preflight unavailable; check local credentials/network; values hidden')
