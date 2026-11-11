"""Machine-local settings and portable tool discovery. No site defaults or dependencies."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'eda.local.json'
FIELDS = ('mode', 'host', 'vm_root', 'display', 'xauthority', 'tool_setup', 'vcs_home', 'verdi_home')
ENV = dict(mode='EDA_MODE', host='EDA_VM_HOST', vm_root='EDA_VM_ROOT', display='EDA_DISPLAY',
           xauthority='EDA_XAUTHORITY', tool_setup='EDA_TOOL_SETUP', vcs_home='VCS_HOME', verdi_home='VERDI_HOME')


def simple_path(path):
    return bool(re.fullmatch(r'/[A-Za-z0-9_./-]+',str(path)))


def cache_directory():
    # Old VCS shell wrappers split spaces even in correctly quoted argv. Keep
    # compilation/results in a private ASCII directory while leaving user files in place.
    import hashlib
    base=Path('/tmp')/('cmdbuf-studio-'+str(os.getuid()))
    base.mkdir(mode=0o700,exist_ok=True)
    if base.is_symlink() or base.stat().st_uid!=os.getuid():
        raise RuntimeError('Unsafe local tool cache: '+str(base))
    key=hashlib.sha256(str(ROOT).encode('utf-8')).hexdigest()[:20]
    return base,key


def result_root():
    if os.environ.get('EDA_RESULT_ROOT'):
        path=Path(os.environ['EDA_RESULT_ROOT']).resolve()
        if os.name!='nt' and not simple_path(path):
            raise ValueError('EDA_RESULT_ROOT must be an ASCII path without spaces for VCS compatibility')
        return path
    if os.name=='nt' or simple_path(ROOT):return ROOT/'build/vcs'
    base,key=cache_directory()
    return base/('results-'+key)


def tool_path(value):
    text=str(value)
    if os.name=='nt' or simple_path(ROOT) or str(ROOT) not in text:return text
    base,key=cache_directory();link=base/('source-'+key)
    if not link.exists() and not link.is_symlink():
        try:link.symlink_to(ROOT,target_is_directory=True)
        except FileExistsError:pass
    if not link.is_symlink() or link.resolve()!=ROOT:
        raise RuntimeError('Tool source alias points elsewhere: '+str(link))
    return text.replace(str(ROOT),str(link))


def load():
    value = dict(mode='auto', host='', vm_root='', display=os.environ.get('DISPLAY', ''),
                 xauthority='', tool_setup='', vcs_home='', verdi_home='')
    if CONFIG.is_file():
        value.update({k:v for k,v in json.loads(CONFIG.read_text(encoding='utf-8-sig')).items() if k in FIELDS})
    for key, name in ENV.items():
        if name in os.environ:
            value[key] = os.environ[name]
    value['mode'] = effective_mode(value)
    return value


def effective_mode(value):
    mode = value.get('mode', 'auto')
    if mode == 'auto':
        return 'ssh' if os.name == 'nt' else 'local'
    if mode not in ('local', 'ssh'):
        raise ValueError('运行方式须为 local、ssh 或 auto')
    return mode


def validate(value):
    result = {k:str(value.get(k, '')).strip() for k in FIELDS}
    result['mode'] = effective_mode(value)
    if any('\n' in v or '\r' in v or '\0' in v for v in result.values()):
        raise ValueError('设备配置不能含换行或空字节')
    if result['mode'] == 'local' and os.name == 'nt':
        raise ValueError('VCS 需要 Linux；Windows 请使用 SSH 模式')
    if result['mode'] == 'ssh':
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', result['host']):
            raise ValueError('请配置 SSH 地址 user@host 或 ~/.ssh/config 中的别名')
        if (not re.fullmatch(r'/[A-Za-z0-9_./-]+', result['vm_root']) or
                '..' in Path(result['vm_root']).parts or result['vm_root'] in ('/', '/tmp', '/home', '/root')):
            raise ValueError('远端工作目录须为独立的绝对路径，例如 /home/user/cmdbuf_studio（ASCII，无空格）')
    if result['display'] and not re.fullmatch(r'[A-Za-z0-9_.-]*:\d+(?:\.\d+)?', result['display']):
        raise ValueError('DISPLAY 例如 :0 或 localhost:10.0；命令行运行可留空')
    for key in ('tool_setup', 'vcs_home', 'verdi_home', 'xauthority'):
        if result[key] and not result[key].startswith('/'):
            raise ValueError(key + ' 须为 EDA 设备上的 Linux 绝对路径')
    return result


def save(value):
    result = validate(value)
    tmp = CONFIG.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    tmp.replace(CONFIG)
    if os.name != 'nt':
        CONFIG.chmod(0o600)
    return result


def shell_exports(value):
    # All values are quoted as shell data, never concatenated unquoted.
    return ''.join('export '+ENV[k]+'='+shlex.quote(value[k])+'\n'
                   for k in ('tool_setup', 'vcs_home', 'verdi_home', 'display', 'xauthority') if value.get(k))


def tool_env(required=('vcs', 'verdi', 'urg')):
    if os.name == 'nt':
        raise RuntimeError('EDA 工具运行于 Linux；请在连接设置中选择 SSH 模式')
    value = load()
    env = os.environ.copy()
    for key in ('tool_setup', 'vcs_home', 'verdi_home', 'display', 'xauthority'):
        if value.get(key):
            env[ENV[key]] = value[key]
    # Site scripts may print banners: preserve only the NUL-delimited environment after a marker.
    marker = b'__CMDBUF_ENV_BEGIN__\0'
    script = 'source "$1" || exit $?\nprintf "__CMDBUF_ENV_BEGIN__\\0"\nenv -0'
    result = subprocess.run(['bash', '-c', script, 'cmdbuf', str(ROOT/'simulation/vcs/toolchain.sh')],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode or marker not in result.stdout:
        raise RuntimeError('EDA 工具环境加载失败: '+result.stderr.decode('utf-8', errors='replace')[-3000:])
    data = result.stdout.split(marker, 1)[1]
    env = dict(item.decode('utf-8', errors='surrogateescape').split('=', 1)
               for item in data.split(b'\0') if b'=' in item)
    for tool, key in [('vcs','VCS_HOME'), ('verdi','VERDI_HOME')]:
        path = shutil.which(tool, path=env.get('PATH'))
        if path and not env.get(key):
            resolved = Path(path).resolve()
            if resolved.parent.name == 'bin':
                env[key] = str(resolved.parent.parent)
            else:
                # Wrapper/symlink locations may be outside the installation; inspect ancestors.
                candidates = [p for p in resolved.parents if (p/'bin'/tool).is_file()]
                if candidates:env[key] = str(candidates[0])
        if env.get(key):
            env['PATH'] = str(Path(env[key])/'bin')+os.pathsep+env.get('PATH','')
    missing = [name for name in required if not shutil.which(name, path=env.get('PATH'))]
    if missing:
        raise RuntimeError('缺少工具 '+', '.join(missing)+'。请先加载设备 EDA 环境，或设置 EDA_TOOL_SETUP / VCS_HOME / VERDI_HOME')
    return env


def pli_directory(env):
    candidates = [Path(env['EDA_FSDB_PLI'])] if env.get('EDA_FSDB_PLI') else []
    if env.get('VERDI_HOME'):
        candidates += [Path(env['VERDI_HOME'])/'share/PLI/VCS'/p for p in ('LINUX64','linux64')]
    for path in candidates:
        if (path/'novas.tab').is_file() and (path/'pli.a').is_file():return path
    raise RuntimeError('缺少 FSDB PLI（novas.tab / pli.a）；设置 VERDI_HOME 或 EDA_FSDB_PLI')


def npi_directories(env):
    home = Path(env.get('VERDI_HOME', '/nonexistent'))
    inc = Path(env.get('EDA_NPI_INC', str(home/'share/NPI/inc')))
    lib = Path(env.get('EDA_NPI_LIB', str(home/'share/NPI/lib'/env.get('NPI_PLATFORM','linux64'))))
    if not (inc/'npi.h').is_file() or not (inc/'npi_fsdb.h').is_file() or not (lib/'libNPI.so').is_file():
        raise RuntimeError('缺少 NPI 头文件或 libNPI.so；设置 VERDI_HOME 或 EDA_NPI_INC / EDA_NPI_LIB')
    return inc, lib


def doctor(env, output):
    checks=[]
    def check(name, test):
        try:checks.append(dict(name=name,status='PASS',detail=str(test())))
        except Exception as exc:checks.append(dict(name=name,status='FAIL',detail=str(exc)))
    check('Python', lambda: sys.version.split()[0])
    for name in ('bash','make','vcs','verdi','urg','fsdb2vcd',env.get('CXX','g++')):
        def find(tool=name):
            path=shutil.which(tool,path=env.get('PATH'))
            if not path:raise RuntimeError('未找到 '+tool)
            return path
        check(name,find)
    check('FSDB PLI',lambda: pli_directory(env))
    check('NPI',lambda: ' / '.join(str(x) for x in npi_directories(env)))
    def writable():
        output.mkdir(parents=True,exist_ok=True)
        probe=output/('.write-probe-'+str(os.getpid()))
        probe.write_text('ok');probe.unlink();return output
    check('Results writable',writable)
    # Probe supported banners without starting a GUI. A smoke run validates licensing.
    for command in (['vcs','-ID'],['verdi','-help'],['urg','-version']):
        def version(cmd=command):
            r=subprocess.run(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
            if r.returncode:raise RuntimeError('工具退出码 '+str(r.returncode))
            return '\n'.join(r.stdout.decode('utf-8',errors='replace').splitlines()[:5])
        check(command[0]+' launch',version)
    result=dict(status='PASS' if all(c['status']=='PASS' for c in checks) else 'FAIL', checks=checks,
                license_check='Use make smoke for an actual compile/run/license check.',
                desktop=env.get('DISPLAY',''), formal_optional='SBY/Yosys are required only for make bmc/cover in generated packages.')
    output.mkdir(parents=True,exist_ok=True)
    (output/'doctor.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for c in checks:print(c['status']+' '+c['name']+': '+c['detail'])
    print('License: make smoke performs a real VCS compile, simulation and URG merge.')
    return 0 if result['status']=='PASS' else 2
