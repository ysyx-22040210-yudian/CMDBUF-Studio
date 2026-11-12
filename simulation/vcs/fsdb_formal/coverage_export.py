"""Generate a Formal package from the exact cases of a verified coverage set."""
import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eda
import execution
from code_coverage.engine import design_identity, read_build, result_file, stage
from fsdb_formal import core, engine


def selected_cases(record):
    selected = record.get('selected', [])
    if not record.get('verified') or not selected:
        raise ValueError('请选择含用例且已通过覆盖率复核的选集')
    if len(selected) != record['solver']['upper_bound']:
        raise ValueError('覆盖率选集的用例数量不一致')
    batches = {}
    seen = set()
    cases = []
    for row in selected:
        batch, run_id = engine.safe_id(row['batch']), engine.safe_id(row['id'])
        if (batch, run_id) in seen or batch not in record['batches']:
            raise ValueError('覆盖率选集包含重复或未知回归用例')
        seen.add((batch, run_id))
        if batch not in batches:
            folder = eda.select_regression(batch)
            summary = eda.read_json(folder / 'summary.json')
            rows = {case['id']: case for case in summary['results']
                    if case['status'] == 'PASS' and not case.get('injection')}
            included = set(summary.get('coverage', {}).get('included', rows))
            batches[batch] = (folder, summary, rows, included)
        folder, summary, rows, included = batches[batch]
        chosen = rows.get(run_id)
        if not chosen or run_id not in included or chosen['seed'] != row['seed']:
            raise ValueError('选中的历史用例记录已变化: ' + batch + '/' + run_id)
        original = folder / 'cases' / run_id
        cases.append((batch, original, chosen, summary))
    return cases


def archived_dut(cases):
    origins = {}
    for _, _, chosen, summary in cases:
        build = chosen.get('build') or summary.get('build')
        if build not in origins:
            origins[build] = dict(summary, build=build)
    first = next(iter(origins.values()))
    identity = design_identity(first)
    if any(design_identity(summary) != identity for summary in origins.values()):
        raise ValueError('选集包含不同的 DUT 定义，无法生成同一个 Formal 顶层')
    cfg, hashes, _ = identity
    builds = [read_build(summary)[0] for summary in origins.values()]
    sources = {}
    for relative, expected in hashes.items():
        name = Path(relative)
        if name.is_absolute() or '..' in name.parts:
            raise ValueError('无效的历史 RTL 路径: ' + relative)
        candidates = [build / 'rtl_sources' / name for build in builds if build] + [eda.ROOT / name]
        for path in candidates:
            if path.is_file():
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() == expected:
                    sources[relative] = data
                    break
        if relative not in sources:
            raise ValueError('缺少可校验的历史 DUT 源码: ' + relative)
    return cfg, sources


def export(analysis, identifier, max_edges, timeout):
    record = eda.read_json(result_file(analysis))
    cases = selected_cases(record)
    rtl = archived_dut(cases)
    scope = eda.PROJECT['top']
    request = dict(id=identifier, coverage_set=analysis, max_edges=max_edges, sources=[])
    wave_root = eda.OUT / 'coverage_formal_waves' / identifier
    env = None
    # A regression dumps the canonical interface at the testbench boundary,
    # including when the instance uses custom pin names in the DUT adapter.
    for pattern, (batch, original, chosen, _) in enumerate(cases):
        wave = original / 'waves.fsdb'
        provenance = dict(batch=batch, run=chosen['id'], seed=chosen['seed'], waveform='archived')
        if not wave.is_file() or not wave.stat().st_size:
            stage('重放缺少 FSDB 的选中用例 · {} / {}'.format(batch, chosen['id']))
            if env is None:
                env = eda.platform_config.tool_env(required=('vcs', 'verdi'))
            replay, exact = eda.replay_case(chosen, original, wave_root / ('pattern_' + str(pattern)),
                                            env, timeout, waves=True, convert_vcd=False)
            if not exact:
                raise RuntimeError('历史用例重放与原始结果不一致: ' + batch + '/' + chosen['id'])
            wave = Path(replay['directory']) / 'waves.fsdb'
            provenance.update(waveform='replayed', replay_receipt=str(wave.parent / 'result.json'))
        request['sources'].append(dict(kind='file', name=chosen['id'], pattern=pattern, path=str(wave),
                                       scope=scope, coverage_case=provenance))
        print('COVERAGE_PROGRESS {}/{}'.format(pattern + 1, len(cases)), flush=True)
    stage('NPI 逐拍生成选集 Formal')
    return engine.run(request, 'export', archived_dut=rtl)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage-set', required=True)
    parser.add_argument('--id')
    parser.add_argument('--max-edges', type=int, default=100000)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args(argv)
    execution.install_signal_handlers()
    try:
        identifier = engine.safe_id(args.id) if args.id else eda.stamp() + '_coverage_' + eda.uuid.uuid4().hex[:8]
        core.number(args.max_edges, '采样沿上限', 1, 1000000)
        if args.timeout < 0:
            raise ValueError('仿真执行超时须为非负数')
        return export(args.coverage_set, identifier, args.max_edges, args.timeout)
    except KeyboardInterrupt:
        print('选集 Formal 生成已取消', file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print('COVERAGE_FORMAL_ERROR: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
