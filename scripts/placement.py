"""Discover matching Selectel pools/segments without allocating resources."""
import re
import urllib.error
from selectel_api import authenticate, request_json
from settings import cloud_config, credentials_from_env

SERVICES = ('compute', 'image', 'volumev3', 'network')


class CandidateUnavailable(RuntimeError):
    pass


def endpoints_for(catalog, region):
    endpoints = {s['type']: e['url'].rstrip('/') for s in catalog for e in s['endpoints']
                 if e.get('interface') == 'public' and e.get('region') == region
                 and e['url'].startswith('https://')}
    if any(kind not in endpoints for kind in SERVICES):
        raise CandidateUnavailable('Required services unavailable in pool ' + region)
    return endpoints


def ordered_pools(config, catalog):
    available = {e['region'] for s in catalog if s['type'] == 'compute' for e in s['endpoints']
                 if e.get('interface') == 'public' and re.fullmatch(r'ru-\d+', e.get('region', ''))}
    order = []
    for requested in config['pools']:
        candidates = sorted(available, key=lambda p: int(p.split('-')[1]), reverse=True) if requested == 'auto' else [requested]
        for pool in candidates:
            if pool in available and pool not in order:
                try:
                    endpoints_for(catalog, pool)
                except CandidateUnavailable:
                    continue
                order.append(pool)
    return order


def candidates(config):
    token, catalog = authenticate(cloud_config({'project_id': credentials_from_env()['project_id']}))
    for pool in ordered_pools(config, catalog):
        endpoints = endpoints_for(catalog, pool)
        try:
            def get(kind, path):
                return request_json(endpoints[kind] + path, token=token)[0]
            images_url = endpoints['image'].removesuffix('/v2') + '/v2/images?status=active'
            images = request_json(images_url, token=token)[0]['images']
            images = [i for i in images if i['name'] == config['image_name']
                      and i.get('visibility') == 'public' and i.get('min_disk', 0) <= config['boot_disk_gb']
                      and i.get('min_ram', 0) <= min(p['ram_mb'] for p in config['profiles'].values())
                      and i.get('architecture', 'x86_64') in ('x86_64', 'amd64')]
            if not images:
                raise CandidateUnavailable('No matching Ubuntu image')
            images.sort(key=lambda i: (i.get('created_at', ''), i['id']), reverse=True)
            flavors = get('compute', '/flavors/detail')['flavors']
            ids = {}
            for role, profile in config['profiles'].items():
                wanted = config['flavor_name_template'].format(**profile)
                matches = [f for f in flavors if f['vcpus'] == profile['vcpus']
                           and f['ram'] == profile['ram_mb'] and f['name'] == wanted]
                if len(matches) != 1:
                    raise CandidateUnavailable('No unique matching flavor for ' + role)
                ids[role] = matches[0]['id']
            types = {t['name'] for t in get('volumev3', '/types')['volume_types']}
            zones = get('compute', '/os-availability-zone')['availabilityZoneInfo']
            segments = sorted(z['zoneName'] for z in zones if z['zoneState']['available']
                              and re.fullmatch(re.escape(pool) + r'[a-z]', z['zoneName']))
            for segment in segments:
                if config['segment'] and config['segment'] != segment:
                    continue
                volume_type = config['disk_class'] + '.' + segment
                if volume_type in types:
                    yield {'region': pool, 'availability_zone': segment, 'volume_type': volume_type,
                           'image_id': images[0]['id'], 'flavor_ids': ids}
        except urllib.error.HTTPError as error:
            if error.code not in (403, 404):
                raise
            print('Pool ' + pool + ': required catalog access unavailable; skipped.', flush=True)
        except CandidateUnavailable as error:
            print('Pool ' + pool + ': ' + str(error) + '; skipped.', flush=True)
