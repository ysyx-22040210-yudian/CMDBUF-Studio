"""Versioned, fail-closed inventory of this project's environment obligations."""
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IMPLEMENTED = {'E03_SRC', 'E04_ARRESP', 'E05_ZERO_READY', 'E06_SUCCESS_BUSY',
               'T_MEM4', 'T_ASSOCIATION', 'T_CLOCK_RESET', 'P_KNOWN', 'P_OWNERSHIP',
               'P_QUEUE', 'DUT_CHECKS'}


def audit(root=None, catalog=None):
    root = Path(root or ROOT)
    catalog = catalog or json.loads((HERE / 'contracts.json').read_text(encoding='utf-8'))
    failures = []
    if catalog.get('version') != 1:
        failures.append('unsupported catalog version')
    rules = catalog.get('rules', [])
    ids = [r['id'] for r in rules]
    if len(set(ids)) != len(ids) or set(ids) != IMPLEMENTED:
        failures.append('unmapped/missing rule: ' + repr(sorted(set(ids) ^ IMPLEMENTED)))
    for name, expected in catalog.get('sources', {}).items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            failures.append('source changed; review contract mapping: ' + name)
    required = {'rtl/files.f', 'rtl/cmdbuf_rd.sv', 'rtl/cmdbuf_rd_cmd.sv', 'rtl/cmdbuf_rd_otb.sv',
                'formal/protocol_20261021/cmdbuf_rd_port_sva.sv',
                'formal/protocol_20261021/cmdbuf_rd_reference_model.svh',
                'formal/protocol_20261021/cmdbuf_rd_xau_order_model.svh',
                'docs/timing/protocol_corrections_20261021.json'}
    if set(catalog.get('sources', {})) != required:
        failures.append('incomplete source inventory')
    sva = root / 'formal/protocol_20261021/cmdbuf_rd_port_sva.sv'
    if sva.is_file():
        code = re.sub(r'//[^\n]*', '', sva.read_text(encoding='utf-8'))
        labels = set(re.findall(r'(\w+)\s*:\s*assume\s+property', code))
        if labels != {r['id'] for r in rules if r['kind'] == 'environment_assumption'}:
            failures.append('SVA assumption mapping mismatch')
    for r in rules:
        if r.get('instances') != (8 if r['id']=='E06_SUCCESS_BUSY' else 145 if r['id']=='DUT_CHECKS' else 1):
            failures.append('instance expansion mismatch: '+r['id'])
        if r.get('status') != 'implemented' or not all(r.get(k) for k in
                ('source', 'trigger', 'reads', 'writes', 'driver', 'monitor', 'validation')):
            failures.append('incomplete adapter entry: ' + r['id'])
    if failures:
        raise ValueError('CONTRACT_UNSUPPORTED: ' + '; '.join(failures))
    from check_policy import catalog as check_profiles
    return {'status': 'PASS', 'version': 1, 'assumption_families': 4,
            'simulation_profiles': check_profiles(),
            'assumption_instances': 11, 'rules': rules, 'sources': catalog['sources'],
            'scope': 'Current frozen input contracts and listed environment policies; no universal SVA-to-driver synthesis or exhaustive RTL proof.'}
