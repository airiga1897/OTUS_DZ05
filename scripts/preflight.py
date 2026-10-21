"""Read-only catalog/quota discovery; detailed results stay in .local."""
import datetime
import json
import time
import urllib.error
from selectel_api import authenticate, request_json
from placement import endpoints_for, CandidateUnavailable
from settings import LOCAL, cloud_config, load_deployment, write_json


def retry_read(operation):
    for attempt in range(3):
        try:
            return operation()
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def collect(context):
    config = cloud_config(context)
    token, catalog = retry_read(lambda: authenticate(config))
    endpoints = endpoints_for(catalog, context['region'])
    project = context['project_id']
    queries = {
        'compute': ('compute', '/os-quota-sets/' + project + '/detail'),
        'volumes': ('volumev3', '/os-quota-sets/' + project + '?usage=True'),
        'network': ('network', '/v2.0/quotas/' + project + '/details.json'),
        'servers': ('compute', '/servers/detail'),
        'subnets': ('network', '/v2.0/subnets'),
        'networks': ('network', '/v2.0/networks'),
    }
    raw = {}
    for name, (service, path) in queries.items():
        try:
            raw[name] = retry_read(lambda: request_json(endpoints[service] + path, token=token))[0]
        except urllib.error.HTTPError as error:
            if error.code in (403, 404):
                raise CandidateUnavailable('Preflight API access incomplete for ' + name) from None
            raise
    write_json(LOCAL / 'discovery.json', raw)
    return raw


def sufficient(context, raw, incremental=False):
    config = context['config']
    extra = config['additional_reservation']
    def capacity(item, need, used='in_use'):
        return item['limit'] < 0 or item['limit'] - item.get(used, 0) - item.get('reserved', 0) >= need
    totals = {'cores': 0, 'ram': 0, 'instances': len(config['nodes'])}
    for node in config['nodes'].values():
        profile = config['profiles'][node['role']]
        totals['cores'] += profile['vcpus']
        totals['ram'] += profile['ram_mb']
    quotas = raw['compute']['quota_set']
    az = quotas.get('az_quotas', {}).get(context['availability_zone'], quotas)
    for key, need in totals.items():
        if key not in az or not capacity(az[key], need + extra.get(key, 0)):
            raise CandidateUnavailable('Insufficient compute capacity')
    volume = raw['volumes']['quota_set']
    key = 'gigabytes_' + context['volume_type']
    # Check the regional total as well as the selected type when present.
    required_disk = config['boot_disk_gb'] * totals['instances'] + extra['disk_gb']
    for item in [volume.get('gigabytes'), volume.get(key)]:
        if item is not None and not capacity(item, required_disk):
            raise CandidateUnavailable('Insufficient disk capacity')
    if key not in volume:
        raise CandidateUnavailable('Selected disk type quota unavailable')
    need_network = {'port': totals['instances']} if incremental else {
        'floatingip': 1, 'network': 1, 'subnet': 1, 'router': 1,
        'port': totals['instances'] + 2, 'security_group': 1}
    for key, need in need_network.items():
        if not capacity(raw['network']['quota'][key], need + extra.get(key, 0), 'used'):
            raise CandidateUnavailable('Insufficient network capacity')


if __name__ == '__main__':
    try:
        context = load_deployment()
        sufficient(context, collect(context))
        print('Preflight PASS; detailed data kept in .local only.')
    except (OSError, ValueError, KeyError, CandidateUnavailable):
        raise SystemExit('Preflight failed; check saved deployment, credentials and API access.') from None
