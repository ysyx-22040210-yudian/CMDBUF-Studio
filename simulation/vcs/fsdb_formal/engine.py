#!/usr/bin/env python3
"""Local Linux Make entry point for direct NPI inspection and Formal package generation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import eda
import dut_config
from fsdb_formal import core


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Invalid export ID')
    return value


def file_hash(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):result.update(block)
    return result.hexdigest()


def checked_run(command, directory, log, env, timeout=600):
    rc, seconds = eda.execute(command, directory, log, env, timeout)
    if rc:
        detail = Path(log).read_text(encoding='utf-8',errors='replace')[-12000:]
        raise RuntimeError('NPI command failed (%d):\n%s' % (rc, detail))
    return seconds


def reader(env):
    inc,lib = eda.platform_config.npi_directories(env)
    key = hashlib.sha256((eda.digest(HERE/'npi_reader.cpp') + eda.digest(inc/'npi_fsdb.h') +
                          str(lib) + str((lib/'libNPI.so').stat().st_mtime_ns)).encode()).hexdigest()[:16]
    directory = eda.OUT/'npi_readers'/key; directory.mkdir(parents=True,exist_ok=True)
    executable = directory/'npi_reader'
    if not executable.is_file():
        checked_run([env.get('CXX','g++'),'-std=c++11','-O2','-I'+str(inc),str(HERE/'npi_reader.cpp'),
                     '-L'+str(lib),'-Wl,-rpath,'+str(lib),'-lNPI','-ldl','-lpthread','-lrt','-o',str(executable)],
                    directory,directory/'compile.log',env)
    env['LD_LIBRARY_PATH'] = str(lib) + ':' + env.get('LD_LIBRARY_PATH','')
    return executable


def regression_wave(source):
    batch, run = safe_id(source['batch']), safe_id(source['run'])
    folder = eda.OUT/'regressions'/batch
    saved = source.get('archive')
    if saved is not None:
        if not isinstance(saved, str) or not Path(saved).is_absolute() or Path(saved).name != batch:
            raise ValueError('回归波形的归档路径无效: ' + batch + '/' + run)
        folder = Path(saved)
    report = folder/'summary.json'
    if not report.is_file():
        raise ValueError('找不到回归归档 {} / {}，请从“结果与波形”重新加入，或导入原 FSDB。\n{}'.format(
            batch, run, saved or report))
    summary = eda.read_json(report)
    row = next((r for r in summary['results'] if r['id'] == run), None)
    if summary.get('id') != batch or row is None:
        raise ValueError('回归归档中没有用例 {} / {}'.format(batch, run))
    path = folder/'cases'/run/'waves.fsdb'
    if not row.get('waves_enabled', True) or not path.is_file():
        raise ValueError('回归用例没有可读取的 FSDB: ' + str(path))
    return path


def fsdb_path(source):
    if source['kind'] == 'regression':
        return regression_wave(source)
    elif source['kind'] == 'import':
        path = eda.OUT/'fsdb_imports'/safe_id(source['id'])/'wave.fsdb'
    elif source['kind'] == 'file':
        # Absolute path supplied explicitly by a local request or coverage export.
        path = Path(source['path']).resolve()
    else:
        raise ValueError('Unknown FSDB source')
    if not path.is_file():
        raise ValueError('找不到 FSDB: '+str(path))
    return path


def inspection(directory, binary, source, env):
    wave = fsdb_path(source)
    raw = directory/'inspection.ndjson'
    scope = source.get('scope','')
    if scope:
        core.mapping(scope)
    checked_run([str(binary),'inspect',str(wave),scope,str(raw),'100000'],directory,directory/'npi_inspect.log',env)
    rows = [json.loads(line) for line in raw.read_text(encoding='utf-8').splitlines()]
    result = dict(status='PASS',meta=rows[0],scopes=[r['name'] for r in rows if r['type']=='scope'],
                  signals=[r for r in rows if r['type']=='signal'],source=source,
                  selected_scope_found=any(r.get('type')=='selection' and r.get('found') for r in rows),
                  fsdb_sha256=file_hash(wave),bytes=wave.stat().st_size)
    eda.write_json(directory/'inspection.json',result)
    return result


def package_rtl(directory, cfg, archived_sources=None):
    sources = archived_sources
    if sources is None:
        sources = {path.relative_to(eda.ROOT).as_posix(): path.read_bytes()
                   for path in dut_config.source_files(cfg)}
    for name, data in sources.items():
        dest = directory/'rtl_snapshot'/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    names = ['rtl_snapshot/'+p for p in cfg['files']]
    includes = ['rtl_snapshot/'+p for p in cfg['include_dirs']]
    lines = ['+incdir+'+d for d in includes] + ['+define+'+d for d in cfg['defines']]
    lines += names + ['trace_properties.sv','formal_top.sv']
    lines = [json.dumps(n,ensure_ascii=False) if re.search(r'\s',n) else n for n in lines]
    (directory/'files.f').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return names, includes


def sby_configs(directory, traces, names, includes, cfg, style='cycle'):
    # Derive both files from the same samples. The SBY form targets a two-state
    # engine and does not depend on parsing the syntax of the native style.
    sby_style = 'table' if style in ('rom', 'generate') else style
    lowered = core.native_sva(traces, sby_style, lowered=True)
    (directory/'sby_properties.sv').write_text(lowered,encoding='utf-8')
    properties = lowered.count(': assume property') + lowered.count(': assert property') - 1
    eda.write_json(directory/'sby_adaptation.json',dict(source='trace_properties.sv',executed='sby_properties.sv',
                   properties=properties, format=style, executed_format=sby_style,
                   rule='Generated from the same trace. The two-state backend uses (!A || B) for guarded comparisons.',
                   sampled_value_expansion='Two $stable selectors replaced by comparisons against explicit previous-posedge registers. First-cycle guards preserved.',
                   source_sha256=eda.digest(directory/'trace_properties.sv'),executed_sha256=eda.digest(directory/'sby_properties.sv')))
    depth = max(s['cycles'] for t in traces for s in t['segments']) + 2
    args = ['-D','SYNTHESIS']
    for inc in includes:args += ['-I',inc]
    for define in cfg['defines']:args += ['-D',define]
    args += names + ['sby_properties.sv','formal_top.sv']
    # read_slang receives Yosys command tokens literally; use its documented
    # command-file parser for quotes/paths instead of quoting command tokens.
    (directory/'slang.f').write_text('\n'.join(json.dumps(a,ensure_ascii=False) for a in args)+'\n',encoding='utf-8')
    script = ('read_slang --top cmdbuf_fsdb_formal_top --no-implicit-memories -f slang.f'+
              '\nprep -top cmdbuf_fsdb_formal_top\nasync2sync\ndffunmap\nchformal -lower\n'
              'sim -clock clk -resetn rst_n -rstlen 1 -n 1 -w -vcd reset_initialization.vcd\n')
    for mode,engine in [('bmc','abc bmc3'),('cover','smtbmc yices')]:
        text = '[options]\nmode %s\ndepth %d\ntimeout 600\n\n[engines]\n%s\n\n[script]\n%s\n[files]\nrtl_snapshot\nsby_properties.sv\nformal_top.sv\nslang.f\n' % (mode,depth,engine,script)
        (directory/('run_'+mode+'.sby')).write_text(text,encoding='utf-8')
    for name in ('proof.py', 'toolchain.py', 'run_python.sh'):
        data = (HERE/name).read_bytes()
        (directory/name).write_bytes(data.replace(b'\r\n', b'\n') if name.endswith('.sh') else data)
    (directory/'Makefile').write_text(
        '.DEFAULT_GOAL := prove\n.PHONY: prove doctor bmc cover\n'
        'PYTHON ?= /bin/sh ./run_python.sh\nFORMAL_TIMEOUT ?=\n'
        'export PYTHONIOENCODING := utf-8\n'
        'prove:\n\t$(PYTHON) proof.py $(if $(strip $(FORMAL_TIMEOUT)),--timeout "$(FORMAL_TIMEOUT)")\n'
        'doctor:\n\t$(PYTHON) toolchain.py\n'
        'bmc cover:\n\t$(PYTHON) toolchain.py --run-sby run_$@.sby\n', encoding='utf-8')
    return depth


def run(request, action, archived_dut=None):
    identifier = safe_id(request['id'])
    style = core.formal_format(request.get('format', 'cycle'))
    directory = eda.OUT/'fsdb_formal'/identifier
    directory.mkdir(parents=True,exist_ok=False)
    eda.write_json(directory/'request.json', dict(request, format=style) if action == 'export' else request)
    env = eda.platform_config.tool_env(required=('verdi',));binary = reader(env)
    if action == 'inspect':
        inspection(directory,binary,request['sources'][0],env)
    else:
        sources = request.get('sources',[])
        if not sources or (archived_dut is None and len(sources) > 100):
            raise ValueError('请选择 1..100 个 FSDB 用例')
        ids = [core.number(s['pattern'],'pattern') for s in sources]
        if len(set(ids)) != len(ids):raise ValueError('每个 FSDB 的 pattern 编号必须唯一')
        limit = core.number(request.get('max_edges',100000),'采样沿上限',1,1000000)
        traces=[]
        print('NPI_FORMAL %s TOTAL %d' % (identifier,len(sources)),flush=True)
        for i,source in enumerate(sources):
            mapping = core.mapping(source['scope'],source.get('mapping'))
            stem = 'pattern_%d' % source['pattern'];wave=fsdb_path(source)
            map_file=directory/(stem+'.tsv');raw=directory/(stem+'.ndjson')
            core.write_mapping(map_file,mapping)
            checked_run([str(binary),'extract',str(wave),str(map_file),str(raw),str(limit)],
                        directory,directory/(stem+'_npi.log'),env)
            trace=core.parse_trace(raw,source)
            trace.update(fsdb_sha256=file_hash(wave),fsdb_bytes=wave.stat().st_size,mapping=mapping)
            traces.append(trace)
            print('NPI_PROGRESS %d/%d pattern=%d' % (i+1,len(sources),source['pattern']),flush=True)
        cfg, archived_sources = archived_dut if archived_dut is not None else (dut_config.load(), None)
        counts=core.export_data(directory,traces,cfg['instance'],style)
        names,includes=package_rtl(directory,cfg,archived_sources)
        depth=sby_configs(directory,traces,names,includes,cfg,style)
        integration=dict(top='cmdbuf_fsdb_formal_top',native_checker='trace_properties.sv',filelist='files.f',
            format=style,
            selectors=['pattern','reset_segment'],sampling='posedge preponed; FSDB time T-1 tick',
            clock='clk',active_low_reset='rst_n',reset_initialization='Initialize DUT with its real reset in the formal tool. disable iff alone does not initialize the DUT.',
            reset_segments='Every complete reset-to-reset interval is independent; all intervals retained. Keep pattern and reset_segment stable within the interval.',
            output_validity='Control outputs compared every cycle. arb_addr compared when recorded req!=0; R payloads compared when recorded rvalid!=0. All raw values retained.',
            unknowns='Reject X/Z inputs or compared outputs; do not silently coerce or wildcard them.',
            native_usage='Load files.f; select cmdbuf_fsdb_formal_top; configure clock/reset initialization; verify assertions and all terminal covers.',
            sby_usage=['In the Studio Linux portable directory: make formal-prove FORMAL_ID=<export-id>',
                       'With the export extracted under the portable directory: make prove'],
            sby_requirements='Yosys with read_slang, SBY, ABC and Yices. SBY performs an explicit real-reset initialization; no reset-sequence assume or zinit.',
            depth=depth,scope='Recorded finite traces only; not an unbounded proof of the interface protocol.',
            dut=cfg)
        summary=dict(status='GENERATED',id=identifier,counts=counts,format=style,formal_status='NOT_RUN',dut_name=cfg['name'],
            sampling='posedge 前一个 FSDB 时间刻度',
            patterns=[dict(pattern=t['pattern'],name=t['name'],fsdb_sha256=t['fsdb_sha256'],
                           cycles=sum(s['cycles'] for s in t['segments']),
                           segments=[{k:v for k,v in s.items() if k!='rows'} for s in t['segments']]) for t in traces],
            files=['trace_properties.sv','formal_top.sv','files.f','samples.csv','transactions.json','trace.json',
                   'integration.json','Makefile','run_bmc.sby','run_cover.sby','slang.f','sby_properties.sv','sby_adaptation.json',
                   'proof.py','toolchain.py','run_python.sh'])
        if archived_dut is not None:
            summary['coverage_set'] = integration['coverage_set'] = request['coverage_set']
            integration['rtl_source'] = 'Verified archived DUT sources and instance wiring'
            for pattern, trace in zip(summary['patterns'], traces):
                pattern['coverage_case'] = trace['source']['coverage_case']
        eda.write_json(directory/'integration.json',integration)
        eda.write_json(directory/'summary.json',summary)
    manifest={p.relative_to(directory).as_posix():eda.digest(p) for p in directory.rglob('*') if p.is_file()}
    eda.write_json(directory/'SHA256SUMS.json',manifest)
    with zipfile.ZipFile(str(directory/'package.zip'),'w',zipfile.ZIP_DEFLATED) as archive:
        for path in directory.rglob('*'):
            if path.is_file() and path.name!='package.zip':archive.write(str(path),path.relative_to(directory).as_posix())
    print('NPI_PACKAGE '+str(directory/'package.zip'),flush=True)
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--action',choices=['inspect','export'],required=True)
    parser.add_argument('--format', choices=core.FORMATS)
    args=parser.parse_args()
    try:
        request = eda.read_json(args.request)
        if args.format:
            request['format'] = args.format
        sys.exit(run(request,args.action))
    except Exception as exc:
        print('FSDB_FORMAL_ERROR: '+str(exc),file=sys.stderr);sys.exit(2)
