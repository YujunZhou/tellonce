#!/usr/bin/env python3
"""Verify shipped versions and immutable Copilot bootstrap checksums offline."""
import hashlib
import json
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
version = json.loads((root / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))['version']
if not re.fullmatch(r'[0-9]+[.][0-9]+[.][0-9]+', version):
    raise SystemExit('invalid release version')
for relative in ('.claude-plugin/plugin.json', 'codex/.codex-plugin/plugin.json',
                 'copilot/.claude-plugin/plugin.json', '.claude-plugin/marketplace.json',
                 '.agents/plugins/marketplace.json', '.github/plugin/marketplace.json'):
    data = json.loads((root / relative).read_text(encoding='utf-8'))
    values = [data['version']] if 'version' in data else [
        data['metadata']['version'], *[item['version'] for item in data['plugins']]]
    if any(value != version for value in values):
        raise SystemExit('inconsistent version in ' + relative)
if 'Tellonce install — version ' + version not in (root / 'install.sh').read_text(encoding='utf-8'):
    raise SystemExit('installer version differs')
for name, declaration in [('bootstrap.sh', 'REF="v' + version + '"'),
                          ('bootstrap.ps1', "$REF    = 'v" + version + "'")]:
    script = root / 'copilot' / name
    if declaration not in script.read_text(encoding='utf-8'):
        raise SystemExit('bootstrap ref differs: ' + name)
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    for relative in ('copilot/README.md', 'copilot/README.zh.md'):
        text = (root / relative).read_text(encoding='utf-8')
        match = re.search(r'\| `' + re.escape(name) + r'`\s*\| `([a-f0-9]{64})`', text)
        if match is None or match[1] != digest:
            raise SystemExit('bootstrap checksum differs in ' + relative)
for relative in ('README.md', 'README.zh.md', 'copilot/README.md', 'copilot/README.zh.md',
                 'copilot/bootstrap.sh', 'copilot/bootstrap.ps1', 'copilot/uninstall.sh',
                 'copilot/uninstall.ps1'):
    refs = re.findall(r'tellonce/(v[0-9]+[.][0-9]+[.][0-9]+)/', (root / relative).read_text(encoding='utf-8'))
    if not refs or any(ref != 'v' + version for ref in refs):
        raise SystemExit('missing or stale install ref in ' + relative)
if not (root / 'docs/releases' / (version + '.md')).is_file():
    raise SystemExit('release notes missing')
print('release metadata, pinned refs and bootstrap checksums agree: ' + version)
