#!/usr/bin/env bash
set -euo pipefail
# Only the Linux control environment is bootstrapped outside Ansible.
test "$(uname -s)" = Linux
. /etc/os-release
test "$ID" = ubuntu
test "$VERSION_ID" = 24.04
python3 -c 'import sys; assert sys.version_info[:2] == (3, 12)'
set +e
cloud-init status --wait
cloud_status=$?
set -e
test "$cloud_status" = 0 || test "$cloud_status" = 2
python3 -c "import json; d=json.load(open('/run/cloud-init/result.json')); assert not d.get('v1',{}).get('errors',[])"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv openssh-client
install -d -m 700 /home/otus/.ansible-dz05
python3 -m venv /home/otus/.ansible-dz05/venv
/home/otus/.ansible-dz05/venv/bin/pip install --disable-pip-version-check 'ansible-core==2.20.10'
/home/otus/.ansible-dz05/venv/bin/ansible-playbook --version
