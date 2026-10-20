"""Resolve configured resources and generate deployment inputs without allocation."""
import argparse
import copy
import ipaddress
import secrets
import uuid
import urllib.request
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from placement import candidates
from settings import (ROOT, LOCAL, CONFIG, DEPLOYMENT, STATE, load_config, read_json,
                      write_json, key_path, render_inputs, state_resources)


def ensure_local(config):
    key = key_path(config)
    public = key.with_suffix('.pub')
    if key.exists() != public.exists():
        raise ValueError('Incomplete SSH key pair; existing key preserved')
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        private = Ed25519PrivateKey.generate()
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                             serialization.NoEncryption()))
        public.write_bytes(private.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                                            serialization.PublicFormat.OpenSSH) + b'\n')
    route_path = LOCAL / 'route-secret.json'
    if not route_path.exists():
        write_json(route_path, {'route_probe_key': secrets.token_hex(32)})
    if not config['admin_cidrs']:
        ip = urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode().strip()
        address = ipaddress.IPv4Address(ip)
        if not address.is_global:
            raise ValueError('Public IPv4 operator address required')
        config['admin_cidrs'] = [str(address) + '/32']
        write_json(CONFIG, config)


def context_for(config, placement):
    variables = {k: copy.deepcopy(config[k]) for k in (
        'name_prefix', 'subnet_cidr', 'nodes', 'boot_disk_gb', 'external_network_name',
        'dns_nameservers', 'admin_ssh_port', 'admin_cidrs')}
    variables.update(placement)
    variables['ssh_public_key_path'] = str(key_path(config).with_suffix('.pub'))
    variables['route_probe_key'] = read_json(LOCAL / 'route-secret.json')['route_probe_key']
    return {'schema_version': 1, 'deployment_id': uuid.uuid4().hex, 'phase': 'prepared',
            'project_id': config['project_id'], **placement, 'config': copy.deepcopy(config),
            'terraform': variables, 'state_lineage': read_json(STATE).get('lineage') if STATE.exists() else None}


def save_context(context):
    write_json(DEPLOYMENT, context)
    render_inputs(context)


def reject_legacy_inputs():
    for path in [ROOT / 'terraform-selectel/terraform.tfvars', ROOT / 'terraform-selectel/terraform.tfvars.json']:
        if path.exists():
            raise ValueError('Legacy tfvars found; migrate settings to .local/lab-config.json and archive tfvars outside Terraform first')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--admin-cidr', action='append', default=[])
    args = parser.parse_args()
    if state_resources():
        raise ValueError('Existing state: preserve deployment context and use verify/destroy')
    reject_legacy_inputs()
    config = load_config()
    for cidr in args.admin_cidr:
        network = ipaddress.IPv4Network(cidr, strict=True)
        if network.prefixlen != 32 or not network.network_address.is_global:
            raise ValueError('Operator addresses must be public IPv4 /32')
        if cidr not in config['admin_cidrs']:
            config['admin_cidrs'].append(cidr)
    write_json(CONFIG, config)
    ensure_local(config)
    placement = next(candidates(config), None)
    if placement is None:
        raise ValueError('No matching pool catalogs available')
    save_context(context_for(config, placement))
    print('Private deployment inputs prepared; no cloud resources created.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if isinstance(error, ValueError):
            raise SystemExit(str(error)) from None
        raise SystemExit('Preparation failed; check local configuration and catalog access; credentials hidden.') from None
