"""Export FSDB files or a Formal package from a verified coverage set."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile

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


def selected_waves(cases, wave_root, timeout, required):
    env = None
    for pattern, (batch, original, chosen, _) in enumerate(cases):
        wave = original / 'waves.fsdb'
        provenance = dict(batch=batch, run=chosen['id'], seed=chosen['seed'], waveform='archived')
        if not wave.is_file() or not wave.stat().st_size:
            stage('重放缺少 FSDB 的选中用例 · {} / {}'.format(batch, chosen['id']))
            if env is None:
                env = eda.platform_config.tool_env(required=required)
            replay, exact = eda.replay_case(chosen, original, wave_root / ('pattern_' + str(pattern)),
                                            env, timeout, waves=True, convert_vcd=False)
            if not exact:
                raise RuntimeError('历史用例重放与原始结果不一致: ' + batch + '/' + chosen['id'])
            wave = Path(replay['directory']) / 'waves.fsdb'
            if not wave.is_file() or not wave.stat().st_size:
                raise RuntimeError('历史用例重放未生成 FSDB: ' + batch + '/' + chosen['id'])
            provenance.update(waveform='replayed', replay_receipt=str(wave.parent / 'result.json'))
        yield wave, provenance


def export(analysis, identifier, max_edges, timeout, style='cycle'):
    style = core.formal_format(style)
    cases = selected_cases(eda.read_json(result_file(analysis)))
    rtl = archived_dut(cases)
    scope = eda.regression_top(cases[0][3])
    request = dict(id=identifier, coverage_set=analysis, max_edges=max_edges, format=style, sources=[])
    wave_root = eda.OUT / 'coverage_formal_waves' / identifier
    # A regression dumps the canonical interface at the testbench boundary,
    # including when the instance uses custom pin names in the DUT adapter.
    for pattern, (wave, provenance) in enumerate(selected_waves(cases, wave_root, timeout, ('vcs', 'verdi'))):
        request['sources'].append(dict(kind='file', name=provenance['run'], pattern=pattern, path=str(wave),
                                       scope=scope, coverage_case=provenance))
        print('COVERAGE_PROGRESS {}/{}'.format(pattern + 1, len(cases)), flush=True)
    stage('NPI 逐拍生成选集 Formal')
    return engine.run(request, 'export', archived_dut=rtl)


def export_fsdb(analysis, identifier, destination, timeout):
    cases = selected_cases(eda.read_json(result_file(analysis)))
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    exported = destination / ('selected_fsdb_' + identifier)
    if exported.exists() or exported.is_symlink():
        raise FileExistsError('FSDB 导出目录已存在: ' + str(exported))
    wave_root = eda.OUT / 'coverage_fsdb_waves' / identifier
    stage('导出选集 FSDB')
    with tempfile.TemporaryDirectory(prefix='.selected_fsdb.', dir=str(destination)) as temp:
        pending = Path(temp) / exported.name
        pending.mkdir()
        # An archived simv already contains the FSDB dumper. Loading site
        # settings is enough for replay; no NPI or waveform converter is used.
        for pattern, (wave, provenance) in enumerate(selected_waves(cases, wave_root, timeout, ())):
            filename = 'pattern_{:04d}_{}.fsdb'.format(pattern, provenance['run'])
            shutil.copyfile(str(wave), str(pending / filename))
            print('COVERAGE_PROGRESS {}/{}'.format(pattern + 1, len(cases)), flush=True)
        pending.rename(exported)
    print('FSDB_EXPORT_RESULT ' + str(exported), flush=True)
    return exported


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage-set', required=True)
    parser.add_argument('--id')
    parser.add_argument('--max-edges', type=int, default=100000)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--format', choices=core.FORMATS, default='cycle')
    parser.add_argument('--fsdb-out', type=Path, help='Export only the selected FSDB files under this directory')
    args = parser.parse_args(argv)
    execution.install_signal_handlers()
    try:
        identifier = engine.safe_id(args.id) if args.id else eda.stamp() + '_coverage_' + eda.uuid.uuid4().hex[:8]
        if args.timeout < 0:
            raise ValueError('仿真执行超时须为非负数')
        if args.fsdb_out is not None:
            export_fsdb(args.coverage_set, identifier, args.fsdb_out, args.timeout)
            return 0
        core.number(args.max_edges, '采样沿上限', 1, 1000000)
        return export(args.coverage_set, identifier, args.max_edges, args.timeout, args.format)
    except KeyboardInterrupt:
        print('选集 FSDB 导出已取消' if args.fsdb_out is not None else '选集 Formal 生成已取消', file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        label = 'COVERAGE_FSDB_ERROR' if args.fsdb_out is not None else 'COVERAGE_FORMAL_ERROR'
        print(label + ': ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
