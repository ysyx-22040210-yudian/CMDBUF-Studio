"""Machine-local settings and portable tool discovery. No site defaults or dependencies."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'eda.local.json'
FIELDS = ('display', 'xauthority', 'tool_setup', 'vcs_home', 'verdi_home')
ENV = dict(display='EDA_DISPLAY',
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
    value = dict(display=os.environ.get('DISPLAY', ''),
                 xauthority=os.environ.get('XAUTHORITY', ''), tool_setup='', vcs_home='', verdi_home='', profile='auto')
    if CONFIG.is_file():
        saved = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
        if saved.get('schema') == 2 and saved.get('profile') == 'manual' and saved.get('machine') == machine_id():
            value.update({k:v for k,v in saved.items() if k in FIELDS})
            value['profile'] = 'manual'
        elif saved.get('schema') != 2 or saved.get('profile') == 'manual':
            value['legacy_config_ignored'] = True
    for key, name in ENV.items():
        if name in os.environ:
            value[key] = os.environ[name]
    # Old installations may contain SSH settings. They are deliberately ignored:
    # this application always operates on the machine where it is started.
    value['mode'] = 'local'
    return value


def machine_id():
    # Scope explicit advanced overrides to this workstation, never a copied VM profile.
    return sys.platform + ':' + socket.gethostname()


def effective_mode(value):
    if value.get('mode', 'local') not in ('local', 'auto'):
        raise ValueError('Studio 仅在当前设备本机运行，请在 Linux EDA 设备上启动')
    return 'local'


def validate(value):
    result = {k:str(value.get(k, '')).strip() for k in FIELDS}
    result['mode'] = effective_mode(value)
    if any('\n' in v or '\r' in v or '\0' in v for v in result.values()):
        raise ValueError('设备配置不能含换行或空字节')
    if result['display'] and not re.fullmatch(r'[A-Za-z0-9_.-]*:\d+(?:\.\d+)?', result['display']):
        raise ValueError('DISPLAY 例如 :0 或 localhost:10.0；命令行运行可留空')
    for key in ('tool_setup', 'vcs_home', 'verdi_home', 'xauthority'):
        if result[key] and not result[key].startswith('/'):
            raise ValueError(key + ' 须为 EDA 设备上的 Linux 绝对路径')
    profile = value.get('profile', 'auto')
    if profile not in ('auto', 'manual'):
        raise ValueError('环境选择须为 auto 或 manual')
    if profile == 'auto':
        result.update({key:'' for key in FIELDS})
    result.update(schema=2, profile=profile, machine=machine_id())
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
        raise RuntimeError('请在装有 EDA 工具的 Linux 设备上运行此命令；Windows 窗口支持编辑与查看已有 VCD')
    value = load()
    env = os.environ.copy()
    for key in ('tool_setup', 'vcs_home', 'verdi_home', 'display', 'xauthority'):
        if value.get(key):
            env[ENV[key]] = value[key]
    # Site scripts may print banners: preserve only the NUL-delimited environment after a marker.
    marker = b'__CMDBUF_ENV_BEGIN__\0'
    script = 'source "$1" || exit $?\nprintf "__CMDBUF_ENV_BEGIN__\\0"\nenv -0'
    result = subprocess.run(['bash', '-c', script, 'cmdbuf', str(ROOT/'simulation/vcs/toolchain.sh')],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if result.returncode or marker not in result.stdout:
        raise RuntimeError('EDA 工具环境加载失败: '+result.stderr.decode('utf-8', errors='replace')[-3000:])
    data = result.stdout.split(marker, 1)[1]
    env = dict(item.decode('utf-8', errors='surrogateescape').split('=', 1)
               for item in data.split(b'\0') if b'=' in item)
    # Desktop launchers may not inherit the user's login PATH. Try that normal
    # login environment once, without asking for an environment script path.
    if not value.get('tool_setup') and any(not shutil.which(t, path=env.get('PATH')) for t in ('vcs', 'verdi', 'urg')):
        try:
            login = subprocess.run(['bash', '-lc', 'printf "__CMDBUF_ENV_BEGIN__\\0"; env -0'], env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if login.returncode == 0 and marker in login.stdout:
                login_env = dict(item.decode('utf-8', errors='surrogateescape').split('=', 1)
                                 for item in login.stdout.split(marker, 1)[1].split(b'\0') if b'=' in item)
                for key in ('DISPLAY', 'XAUTHORITY'):
                    if key in env: login_env[key] = env[key]
                env = login_env
        except (OSError, subprocess.TimeoutExpired):
            pass
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
        raise RuntimeError('当前设备未发现 '+', '.join(missing)+'。请从可直接运行这些命令的终端启动 Studio；特殊安装可使用高级选项。')
    if value.get('display'): env['DISPLAY'] = value['display']
    if value.get('xauthority'): env['XAUTHORITY'] = value['xauthority']
    return env


def describe():
    """Report locations only, never dump the environment or license credentials."""
    settings = load()
    rows = [dict(name='运行设备', status='本机', detail=sys.platform),
            dict(name='工程目录', status='自动定位', detail=str(ROOT)),
            dict(name='启动目录', status='自动读取', detail=os.getcwd()),
            dict(name='Python', status='当前进程', detail=sys.executable),
            dict(name='图形会话', status='自动继承', detail=os.environ.get('DISPLAY', 'Windows 桌面' if os.name == 'nt' else '未设置 DISPLAY'))]
    result = dict(profile=settings['profile'], rows=rows, legacy_config_ignored=settings.get('legacy_config_ignored', False))
    if os.name == 'nt':
        rows.append(dict(name='EDA 执行', status='Linux 运行', detail='当前窗口用于工程编辑；在 Linux 设备上自动识别 VCS / Verdi'))
        return result
    try:
        env = tool_env(required=())
        for name in ('vcs', 'verdi', 'urg', 'fsdb2vcd', 'make', env.get('CXX', 'g++')):
            path = shutil.which(name, path=env.get('PATH'))
            rows.append(dict(name=name, status='已发现' if path else '未发现', detail=str(Path(path).resolve()) if path else '当前终端与登录环境均未找到'))
        for name, get in [('VCS 安装目录', lambda: env.get('VCS_HOME') or '无法从命令路径推导'),
                          ('Verdi 安装目录', lambda: env.get('VERDI_HOME') or '无法从命令路径推导'),
                          ('FSDB PLI', lambda: pli_directory(env)), ('NPI', lambda: ' / '.join(map(str, npi_directories(env))))]:
            try: rows.append(dict(name=name, status='自动定位', detail=str(get())))
            except RuntimeError as exc: rows.append(dict(name=name, status='未就绪', detail=str(exc)))
    except Exception as exc:
        rows.append(dict(name='工具环境', status='未就绪', detail=str(exc)))
    return result


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
    def tk_version():
        import tkinter
        if tkinter.TkVersion < 8.5:
            raise RuntimeError('Tk 需要 8.5 或更新版本')
        return str(tkinter.TkVersion)+' (make gui requires a graphical DISPLAY)'
    check('Tk desktop', tk_version)
    for name in ('bash','make','vcs','verdi','urg','fsdb2vcd',env.get('CXX','g++')):
        def find(tool=name):
            path=shutil.which(tool,path=env.get('PATH'))
            if not path:raise RuntimeError('未找到 '+tool)
            return path
        check(name,find)
    check('FSDB PLI',lambda: pli_directory(env))
    def vpi_header():
        header=Path(env.get('VCS_HOME',''))/'include'/'vpi_user.h'
        if not header.is_file():raise RuntimeError('缺少 VCS include/vpi_user.h，无法编译仿真启动回调')
        return header
    check('VCS VPI header',vpi_header)
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


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Describe automatically detected local EDA tools')
    parser.add_argument('--output')
    args = parser.parse_args()
    result = describe()
    data = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data, encoding='utf-8')
    print(data)
