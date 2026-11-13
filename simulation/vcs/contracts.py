"""Versioned, fail-closed inventory of this project's environment obligations."""
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASELINE_REVISION = '20261021'
INTERFACE_REVISION = '20261112'
PENDING_REVISION = '20261112.2'
REVISION = '20261113'
REGISTERED_PENDING_REVISIONS = (PENDING_REVISION, REVISION)
PARITY_RESPONSE_CODES = {BASELINE_REVISION: 1, INTERFACE_REVISION: 1, PENDING_REVISION: 1, REVISION: 2}
REVISIONS = tuple(PARITY_RESPONSE_CODES)
SUPPLEMENT = 'docs/timing/interface_constraints_20261112.json'
PENDING_SPEC = 'docs/timing/pending_register_20261112.json'
RESPONSE_SPEC = 'docs/timing/parity_response_20261113.json'
PORT_CHECKER = 'formal/protocol_20261021/cmdbuf_rd_port_sva.sv'
IMPLEMENTED = {'E03_SRC', 'E04_ARRESP', 'E05_ZERO_READY', 'E06_SUCCESS_BUSY',
               'E07_SRC_READY', 'E08_AR_GRANT_REQ',
               'T_MEM4', 'T_ASSOCIATION', 'T_CLOCK_RESET', 'P_KNOWN', 'P_OWNERSHIP',
               'P_QUEUE', 'DUT_CHECKS'}


def audit(root=None, catalog=None):
    root = Path(root or ROOT)
    catalog = catalog or json.loads((HERE / 'contracts.json').read_text(encoding='utf-8'))
    failures = []
    if catalog.get('version') != 1 or catalog.get('revision') != REVISION:
        failures.append('unsupported catalog version')
    rules = catalog.get('rules', [])
    ids = [r['id'] for r in rules]
    if len(set(ids)) != len(ids) or set(ids) != IMPLEMENTED:
        failures.append('unmapped/missing rule: ' + repr(sorted(set(ids) ^ IMPLEMENTED)))
    expected_sources = catalog.get('sources', {})
    checker = {}
    for name, expected in expected_sources.items():
        path = root / name
        if not path.is_file():
            failures.append('source missing: ' + name)
            continue
        source = path.read_bytes()
        actual = hashlib.sha256(source).hexdigest()
        if name == PORT_CHECKER:
            from sva_source import mask_sv
            code = mask_sv(source.decode('utf-8'))
            labels = set(re.findall(r'(\w+)\s*:\s*assume\s+property', code))
            registered = {r['id'] for r in rules if r['kind'] == 'environment_assumption'}
            checker = dict(source=name, baseline_sha256=expected, sha256=actual,
                           customized=actual != expected,
                           added_assumptions=sorted(labels-registered),
                           missing_assumptions=sorted(registered-labels))
            if not checker['customized'] and labels != registered:
                failures.append('baseline SVA assumption mapping mismatch')
        elif actual != expected:
            failures.append('source changed; review contract mapping: ' + name)
    required = {'rtl/files.f', 'rtl/cmdbuf_rd.sv', 'rtl/cmdbuf_rd_cmd.sv', 'rtl/cmdbuf_rd_otb.sv',
                PORT_CHECKER,
                'formal/protocol_20261021/cmdbuf_rd_reference_model.svh',
                'formal/protocol_20261021/cmdbuf_rd_xau_order_model.svh',
                'docs/timing/protocol_corrections_20261021.json', SUPPLEMENT, PENDING_SPEC, RESPONSE_SPEC}
    if set(expected_sources) != required:
        failures.append('incomplete source inventory')
    for r in rules:
        if r.get('instances') != (8 if r['id']=='E06_SUCCESS_BUSY' else 145 if r['id']=='DUT_CHECKS' else 1):
            failures.append('instance expansion mismatch: '+r['id'])
        if r.get('status') != 'implemented' or not all(r.get(k) for k in
                ('source', 'trigger', 'reads', 'writes', 'driver', 'monitor', 'validation')):
            failures.append('incomplete adapter entry: ' + r['id'])
    if failures:
        raise ValueError('CONTRACT_UNSUPPORTED: ' + '; '.join(failures))
    from check_policy import catalog as check_profiles
    assumptions = [r for r in rules if r['kind'] == 'environment_assumption']
    sources = dict(expected_sources, **{PORT_CHECKER: checker['sha256']})
    return {'status': 'PASS', 'version': 1, 'revision': REVISION, 'assumption_families': len(assumptions),
            'simulation_profiles': check_profiles(),
            'assumption_instances': sum(r['instances'] for r in assumptions), 'rules': rules, 'sources': sources,
            'checker': checker,
            'scope': 'Registered stimulus contracts and active checker source identity. Edited properties are validated by the simulator.'}


def expected_rresp(arresp, captured_pc_err, revision=REVISION):
    """Use the response encoding of the build that captured this transaction."""
    if arresp == 3:
        return 3
    return PARITY_RESPONSE_CODES[revision] if captured_pc_err else 0


def check_interface(inputs, outputs):
    """Check the 20261112 rules on one known, non-reset sampled frame."""
    source = inputs['src']
    if source < 3 and not (outputs['arready'] & (1 << source)):
        raise ValueError('E07_SRC_READY src={} arready={:03b}'.format(source, outputs['arready']))
    unmatched = inputs['ar_rd_gnt'] & ~outputs['req']
    if unmatched:
        raise ValueError('E08_AR_GRANT_REQ unmatched grants=0x{:02x}'.format(unmatched))
    if outputs['rvalid'] & ~inputs['rready']:
        raise ValueError('A_R_READY rvalid={:03b} rready={:03b}'.format(outputs['rvalid'], inputs['rready']))
