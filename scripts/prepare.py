"""Resolve current catalogs and create local DZ05 key/tfvars, without paid resources."""
import json
import argparse
import secrets
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from selectel_api import authenticate, request_json

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admin-cidr',action='append',default=[],help='Additional confirmed operator IPv4 /32; repeat as needed')
    args=parser.parse_args()
    config = json.loads((ROOT / '.local/operator.json').read_text(encoding='utf-8-sig'))
    if config['project_id'] != 'fdce7ecd9cf742a6a4eda91e927a09fb' or config['region'] != 'ru-9':
        raise ValueError('Unexpected project or region')
    token, catalog = authenticate(config)
    endpoints = {s['type']: e['url'].rstrip('/') for s in catalog for e in s['endpoints']
                 if e.get('interface') == 'public' and e.get('region') == 'ru-9' and e['url'].startswith('https://')}
    image_base = endpoints['image'].removesuffix('/v2')
    images, _ = request_json(image_base + '/v2/images?name=Ubuntu%2024.04%20LTS%2064-bit&status=active', token=token)
    candidates = [i for i in images['images'] if i.get('visibility') == 'public' and i.get('min_disk', 0) <= 20]
    if len(candidates) != 1:
        raise ValueError('Expected exactly one public Ubuntu 24.04 image')
    flavors, _ = request_json(endpoints['compute'] + '/flavors/detail', token=token)
    ids = {}
    for role, name in {'app':'SL1.1-2048', 'db':'SL1.2-4096'}.items():
        matches = [f for f in flavors['flavors'] if f['name'] == name]
        if len(matches) != 1:
            raise ValueError('Flavor unavailable: ' + name)
        ids[role] = matches[0]['id']
    types, _ = request_json(endpoints['volumev3'] + '/types', token=token)
    if not any(t['name'] == 'basicssd.ru-9a' for t in types['volume_types']):
        raise ValueError('SSD type unavailable')
    # Public IP lookup sends no cloud token.
    import urllib.request
    import ipaddress
    ip = urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode().strip()
    if ipaddress.ip_address(ip).version != 4:
        raise ValueError('IPv4 operator address required')
    local = ROOT / '.local'
    key = local / 'otus_dz05'
    public = local / 'otus_dz05.pub'
    if key.exists() != public.exists():
        raise ValueError('Incomplete SSH key pair; existing key preserved')
    if not key.exists():
        private = Ed25519PrivateKey.generate()
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                             serialization.NoEncryption()))
        public.write_bytes(private.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                                           serialization.PublicFormat.OpenSSH) + b' otus-dz05\n')
    path = ROOT / 'terraform-selectel/terraform.tfvars.json'
    if path.exists():
        raise ValueError('Existing tfvars preserved; review manually')
    cidrs=[ip+'/32']
    for value in args.admin_cidr:
        network=ipaddress.IPv4Network(value,strict=True)
        if network.prefixlen!=32 or not network.network_address.is_global:
            raise ValueError('Additional operator addresses must be public IPv4 /32')
        if value not in cidrs:cidrs.append(value)
    values = {'region':'ru-9', 'availability_zone':'ru-9a', 'image_id':candidates[0]['id'],
              'volume_type':'basicssd.ru-9a', 'flavor_ids':ids, 'admin_cidrs':cidrs,
              'route_probe_key':secrets.token_hex(32)}
    path.write_text(json.dumps(values, indent=2) + '\n', encoding='utf-8')
    print('Current image/flavors/SSD resolved; independent key and tfvars prepared. No paid resources created.')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import urllib.error
        if isinstance(error, urllib.error.HTTPError):
            raise SystemExit('Selectel catalog HTTP ' + str(error.code)) from None
        if isinstance(error, ValueError):
            raise SystemExit(str(error)) from None
        raise SystemExit('Preparation failed; local files preserved; credentials hidden') from None
