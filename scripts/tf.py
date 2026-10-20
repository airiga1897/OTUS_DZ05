"""Terraform uses saved deployment context, never independent pool settings."""
import json
import os
import subprocess
import sys
from settings import ROOT, DEPLOYMENT, INPUTS, cloud_config, load_deployment, render_inputs


def operator_env():
    config = cloud_config()
    env = dict(os.environ)
    # Ignore stale TF_VAR values inherited from another project or shell session.
    for name in list(env):
        if name.startswith('TF_VAR_'):
            del env[name]
    for key, field in {'OS_AUTH_URL': 'auth_url', 'OS_PROJECT_ID': 'project_id',
                       'OS_REGION_NAME': 'region', 'OS_USER_DOMAIN_NAME': 'domain_name',
                       'OS_USERNAME': 'username', 'OS_PASSWORD': 'password'}.items():
        if config.get(field):
            env[key] = config[field]
        else:
            env.pop(key, None)
    if DEPLOYMENT.exists():
        render_inputs(load_deployment())
    env['TF_CLI_CONFIG_FILE'] = str(ROOT / 'terraform-selectel.rc')
    env['TF_IN_AUTOMATION'] = '1'
    return env


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit('Usage: tf.py <terraform command>')
    if args[0] in ('apply', 'destroy', 'import', 'taint', 'untaint', 'workspace') or (args[0] == 'state' and args[1:2] != ['list']):
        raise SystemExit('Use lab.py for lifecycle mutations; preserve own deployment context.')
    if any(a.startswith(('-var', '-target', '-replace')) for a in args):
        raise SystemExit('Manual Terraform parameter overrides are unsupported; use lab-config.json')
    if args[0] == 'plan':
        load_deployment()
        args += ['-var-file=' + str(INPUTS)]
    return subprocess.call([str(ROOT / '.tools/terraform.exe'), '-chdir=' + str(ROOT / 'terraform-selectel')] + args,
                           env=operator_env())


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError):
        raise SystemExit('Check project configuration, saved deployment and credentials; values hidden.') from None
