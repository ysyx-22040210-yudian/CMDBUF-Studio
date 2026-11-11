#!/usr/bin/env python3
"""CMDBUF Studio: loopback desktop UI backed exclusively by the Make workflow."""
import argparse
import base64
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
try:
    from http.server import ThreadingHTTPServer
except ImportError:  # EDA distributions still ship Python 3.6.
    from socketserver import ThreadingMixIn
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
from urllib.parse import parse_qs, urlparse
import webbrowser

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import model
import vcd
import formal_model
from model import eda, dut_config, ROOT, STATE

WEB = HERE / 'web'
TOKEN = secrets.token_urlsafe(32)
CURRENT = None
MUTEX = threading.RLock()
HISTORY_CACHE = (0, [])


def identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', str(value)):
        raise ValueError('无效的结果编号')
    return str(value)


def batch_path(batch):
    return model.results_root() / identifier(batch)


def result_path(batch, run):
    summary = eda.read_json(batch_path(batch) / 'summary.json')
    if not any(r['id'] == run for r in summary['results']):
        raise ValueError('用例不在该回归批次中')
    return batch_path(batch) / 'cases' / identifier(run)


def history(refresh=False):
    global HISTORY_CACHE
    if not refresh and time.time() - HISTORY_CACHE[0] < 4:
        return HISTORY_CACHE[1]
    rows = []
    results = model.results_root()
    if results.exists():
        for directory in sorted(results.iterdir(), reverse=True):
            path = directory / 'summary.json'
            if not path.is_file():
                continue
            try:
                s = eda.read_json(path)
                rows.append(dict(id=s['id'], status=s['status'], total=len(s['results']),
                                 passed=s.get('passed',sum(r['status']=='PASS' for r in s['results'])),
                                 coverage=s.get('coverage',{}).get('metrics_percent',{}),
                                 dut=s.get('dut',{}).get('name','历史基准'),
                                 waves=s.get('waves_enabled',True), created=s.get('created_at',''),
                                 seconds=round(sum(r.get('seconds',0) for r in s['results']),2)))
            except (ValueError, KeyError):
                continue
    HISTORY_CACHE = (time.time(), rows[:100])
    return HISTORY_CACHE[1]


def busy():
    return CURRENT is not None and CURRENT['state'] == 'RUNNING'


def require_idle():
    if busy():
        raise ValueError('当前任务正在执行，请待完成后修改配置或启动下一任务')


def log(job, text):
    job['log'] = (job.get('log','') + text)[-300000:]
    with (STATE / 'jobs' / (job['id'] + '.log')).open('a',encoding='utf-8') as f:
        f.write(text)


def invoke(job, arguments, check=True):
    settings = model.state_read()
    env = os.environ.copy()
    env.update(EDA_MODE=settings['mode'],EDA_VM_HOST=settings['host'], EDA_VM_ROOT=settings['vm_root'],
               PYTHONIOENCODING='utf-8', EDA_NO_OPEN='1',EDA_SKIP_SYNC='1')
    cmd = [sys.executable, str(eda.HERE / 'vm_make.py')] + arguments
    log(job, '\n› make ' + ' '.join(arguments) + '\n')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    process = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8', errors='replace', **options)
    for line in process.stdout:
        log(job, line)
        m = re.search(r'BATCH (\S+) TOTAL (\d+)', line)
        if m:
            job.update(batch=m.group(1), total=int(m.group(2)), completed=0)
        m = re.search(r'PROGRESS (\S+) (\d+)/(\d+)', line)
        if m:
            job.update(completed=int(m.group(2)), total=int(m.group(3)))
        m = re.search(r'NPI_PROGRESS (\d+)/(\d+)', line)
        if m:
            job.update(completed=int(m.group(1)), total=int(m.group(2)))
    rc = process.wait()
    job.setdefault('commands', []).append(dict(argv=arguments, returncode=rc))
    if rc:
        found = re.findall(r'(/[A-Za-z0-9_./-]+/compile.log)', job['log'])
        if found:
            try:
                if settings['mode']=='local':
                    log(job,'\n'+Path(found[-1]).read_text(encoding='utf-8',errors='replace')[-12000:])
                else:
                    detail = eda.ssh(settings['host'], 'tail -100 ' + shlex.quote(found[-1]) + '\n', capture=True)
                    log(job, '\n' + detail.stdout.decode('utf-8',errors='replace'))
            except Exception:
                pass
        if check:
            raise RuntimeError('Make 执行失败（退出码 {}），请查看运行日志'.format(rc))
    return rc


def start_job(kind, payload):
    global CURRENT
    require_idle()
    if kind not in ('compile','doctor','regress','coverage','wave','fetch','fsdb_inspect','fsdb_formal'):
        raise ValueError('未知操作')
    if kind in ('fsdb_inspect','fsdb_formal'):
        payload=formal_model.selections(payload)
        if kind=='fsdb_inspect' and len(payload['sources'])!=1:
            raise ValueError('请逐个解析 FSDB，以确认对应实例和端口映射')
    plan = model.check_plan(payload['plan']) if kind == 'regress' else None
    if kind in ('coverage','wave','fetch'):
        identifier(payload['batch'])
    if kind == 'wave':
        path = result_path(payload['batch'], payload['run'])
        result = eda.read_json(path/'result.json')
        if not result.get('waves_enabled',True):
            raise ValueError('此用例运行时未生成波形，请打开波形开关后重新回归')
    job = dict(id=eda.stamp()+'_'+eda.uuid.uuid4().hex[:8], kind=kind, state='RUNNING',
               started=time.time(), completed=0, total=0, stage='准备', log='')
    (STATE/'jobs').mkdir(parents=True,exist_ok=True)
    CURRENT = job

    def work():
        try:
            if kind in ('compile','regress','doctor','fsdb_inspect','fsdb_formal'):
                job['stage']='同步工程'; invoke(job,['sync'])
            if kind in ('fsdb_inspect','fsdb_formal'):
                settings=model.state_read()
                if kind=='fsdb_formal':eda.write_json(STATE/'formal_selection.json',payload)
                def progress(message):
                    job['stage']=message;log(job,message+'\n')
                request=formal_model.stage(payload,settings,job['id'],progress)
                job['stage']='NPI 解析 FSDB' if kind=='fsdb_inspect' else 'NPI 提取与生成 Formal'
                invoke(job,[kind.replace('_','-'),'REQUEST='+request])
                job['stage']='下载生成结果'
                job['formal_id']=formal_model.fetch(settings,job['id'])
            elif kind == 'regress':
                # Save the exact editable plan before sync; each run gets a new ID.
                job['stage']='VCS 编译与回归'
                rc=invoke(job,['regress','PLAN='+payload['plan_file'],'JOBS='+str(plan['jobs']),
                               'TIMEOUT='+str(plan['timeout']),'WAVES='+str(int(plan['waves']))],check=False)
                if job.get('batch'):
                    job['stage']='整理结果'; invoke(job,['fetch','REGRESSION='+job['batch']])
                    summary=eda.read_json(batch_path(job['batch'])/'summary.json')
                    job['verdict']=summary['status']
                if rc:
                    raise RuntimeError('回归含失败用例或执行异常，请查看结果和运行日志')
            elif kind in ('compile','doctor'):
                job['stage']='VCS 编译' if kind=='compile' else '检查工具链'
                invoke(job,[kind])
                if kind=='doctor':
                    settings=model.state_read()
                    eda.write_json(STATE/'connection.json',dict(ok=True,host=settings['host'],mode=settings['mode'],checked=eda.stamp()))
            elif kind == 'fetch':
                job['stage']='下载结果'; invoke(job,['fetch','REGRESSION='+payload['batch']])
            else:
                job['stage']='打开 Verdi'
                args=[kind,'REGRESSION='+payload['batch'],'DISPLAY='+model.state_read()['display']]
                if model.state_read().get('xauthority'):args.append('XAUTHORITY='+model.state_read()['xauthority'])
                if kind=='wave': args.append('RUN_ID='+payload['run'])
                invoke(job,args)
            job.update(state='DONE',stage='已完成')
        except Exception as exc:
            job.update(state='ERROR',stage='执行失败',error=str(exc)); log(job,'\n'+str(exc)+'\n')
        finally:
            job['ended']=time.time()
            eda.write_json(STATE/'jobs'/(job['id']+'.json'),job)
            history(True)
    threading.Thread(target=work,daemon=True).start()
    return job


def upload(payload):
    require_idle()
    files=payload.get('files',[])
    if not files or len(files)>1000:
        raise ValueError('请选择 1..1000 个 RTL / 头文件')
    folder=eda.HERE/'dut_sources'/('import_'+eda.stamp()+'_'+eda.uuid.uuid4().hex[:6])
    decoded=[]; total=0
    for item in files:
        name=item['name'].replace('\\','/')
        path=(folder/name).resolve()
        if folder.resolve() not in path.parents or ':' in name or path.suffix.lower() not in ('.v','.sv','.vh','.svh','.f'):
            raise ValueError('文件路径或类型无效: '+name)
        data=base64.b64decode(item['data'],validate=True); total+=len(data)
        if total>128*1024*1024: raise ValueError('单次导入超过 128 MB')
        decoded.append((path,data))
    for path,data in decoded:
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
    return dict(files=[p.relative_to(ROOT).as_posix() for p,d in decoded if p.suffix.lower() in ('.v','.sv')],
                include_dirs=sorted({p.parent.relative_to(ROOT).as_posix() for p,d in decoded}),
                directory=folder.relative_to(ROOT).as_posix())


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def send(self,value,status=200,content_type='application/json; charset=utf-8'):
        data=json.dumps(value,ensure_ascii=False).encode('utf-8') if isinstance(value,(dict,list)) else value
        self.send_response(status)
        self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.end_headers()
        try:self.wfile.write(data)
        except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError):pass

    def authorized(self):
        host=self.headers.get('Host','')
        if host not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)):
            return False
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)):
            return False
        return hmac.compare_digest(self.headers.get('X-Studio-Token',''),TOKEN)

    def send_file(self,path):
        self.send_response(200)
        self.send_header('Content-Type','application/octet-stream')
        self.send_header('Content-Length',str(path.stat().st_size))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        try:
            with path.open('rb') as source:
                for block in iter(lambda:source.read(1024*1024),b''):self.wfile.write(block)
        except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError):pass

    def do_GET(self):
        try:
            url=urlparse(self.path); q={k:v[0] for k,v in parse_qs(url.query).items()}
            if not url.path.startswith('/api/'):
                path=(WEB/(url.path.lstrip('/') or 'index.html')).resolve()
                if WEB.resolve() not in path.parents or not path.is_file():
                    self.send({'error':'Not found'},404);return
                self.send(path.read_bytes(),content_type=mimetypes.guess_type(str(path))[0] or 'application/octet-stream');return
            if not self.authorized():self.send({'error':'会话已失效，请重新打开 Studio'},403);return
            if url.path=='/api/state':
                connection=eda.read_json(STATE/'connection.json') if (STATE/'connection.json').exists() else {'ok':False}
                self.send(dict(settings=model.state_read(),history=history(),job=CURRENT,connection=connection))
            elif url.path=='/api/cases':self.send(model.cases())
            elif url.path=='/api/formal-state':self.send(formal_model.state())
            elif url.path=='/api/formal-inspection':
                self.send(eda.read_json(formal_model.artifact(q['id'],'inspection.json')))
            elif url.path=='/api/formal-summary':
                self.send(eda.read_json(formal_model.artifact(q['id'],'summary.json')))
            elif url.path=='/api/formal-download':
                self.send_file(formal_model.artifact(q['id'],q.get('file','package.zip')))
            elif url.path=='/api/formal-preview':
                path=formal_model.artifact(q['id'],q['file'])
                if path.suffix=='.zip':raise ValueError('ZIP 请通过下载入口打开')
                with path.open(encoding='utf-8',errors='replace') as source:content=source.read(120000)
                self.send(dict(text=content,truncated=path.stat().st_size>120000))
            elif url.path=='/api/case':self.send(model.case(q['name']))
            elif url.path=='/api/dut':
                self.send(dict(config=dut_config.load(),version=eda.digest(eda.HERE/'dut.json'),ports=dut_config.PORTS,
                               default=eda.read_json(HERE/'default_dut.json')))
            elif url.path=='/api/results':self.send(eda.read_json(batch_path(q['batch'])/'summary.json'))
            elif url.path=='/api/artifact':
                if q['file'] not in ('sim.log','events.csv','assigned.csv','samples.csv','drive_audit.json','case.sv','constraint_failure.json'):
                    raise ValueError('不可查看此文件')
                path=result_path(q['batch'],q['run'])/q['file']
                with path.open(encoding='utf-8',errors='replace') as f:text=f.read(500000)
                self.send(dict(text=text,truncated=path.stat().st_size>500000))
            elif url.path in ('/api/wave','/api/wave-window'):
                path=result_path(q['batch'],q['run'])/'waves.vcd'
                if not path.is_file():raise ValueError('该结果没有本地 VCD。请开启生成波形后重新回归；历史 FSDB 可用 Verdi 打开。')
                database=vcd.index(path,STATE/'wave_cache')
                if url.path=='/api/wave':self.send(vcd.describe(database))
                else:self.send(vcd.window(database,q['signals'].split(','),int(q['start']),int(q['end'])))
            else:self.send({'error':'Not found'},404)
        except Exception as exc:self.send({'error':str(exc)},400)

    def do_POST(self):
        if not self.authorized():self.send({'error':'会话或来源无效'},403);return
        try:
            url=urlparse(self.path)
            if url.path=='/api/fsdb-upload-chunk':
                q={k:v[0] for k,v in parse_qs(url.query).items()}
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=8*1024*1024:raise ValueError('上传分片不能超过 8 MB')
                data=self.rfile.read(size)
                if len(data)!=size:raise ValueError('上传分片不完整')
                with MUTEX:
                    require_idle();result=formal_model.chunk_upload(q['id'],int(q['offset']),data)
                self.send(result);return
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=180*1024*1024:raise ValueError('请求大小无效')
            payload=json.loads(self.rfile.read(size).decode('utf-8'))
            with MUTEX:
                require_idle()
                if self.path=='/api/case':result=model.save_case(payload)
                elif self.path=='/api/fsdb-upload-start':result=formal_model.begin_upload(payload)
                elif self.path=='/api/fsdb-upload-finish':result=formal_model.finish_upload(payload)
                elif self.path=='/api/plan':
                    plan=model.check_plan(payload); settings=model.state_read();settings['plan']=plan
                    eda.write_json(STATE/'settings.json',settings);result=plan
                elif self.path=='/api/settings':
                    result=model.settings_save(payload);history(True)
                elif self.path=='/api/dut':
                    if payload.get('version')!=eda.digest(eda.HERE/'dut.json'):raise ValueError('DUT 配置已变化，请重新加载')
                    config=dut_config.validate(payload['config'])
                    eda.write_json(STATE/'backups'/('dut_'+eda.stamp()+'_'+eda.uuid.uuid4().hex[:6]+'.json'),dut_config.load())
                    eda.write_json(eda.HERE/'dut.json',config);result={'config':config,'version':eda.digest(eda.HERE/'dut.json')}
                elif self.path=='/api/import':result=upload(payload)
                elif self.path=='/api/job':
                    if payload['kind']=='regress':
                        plan=model.check_plan(payload['plan'])
                        settings=model.state_read();settings['plan']=plan;eda.write_json(STATE/'settings.json',settings)
                        path=eda.HERE/'plans'/('studio_'+eda.stamp()+'_'+eda.uuid.uuid4().hex[:6]+'.json')
                        eda.write_json(path,plan);payload['plan_file']=path.relative_to(ROOT).as_posix()
                    result=start_job(payload['kind'],payload)
                elif self.path=='/api/shutdown':
                    threading.Thread(target=self.server.shutdown,daemon=True).start();result={'ok':True}
                else:raise ValueError('未知操作')
            self.send(result)
        except Exception as exc:self.send({'error':str(exc)},400)


def open_window(url):
    if os.name=='nt':
        for root in (os.environ.get('PROGRAMFILES(X86)',''),os.environ.get('PROGRAMFILES','')):
            edge=Path(root)/'Microsoft/Edge/Application/msedge.exe'
            if edge.is_file():
                subprocess.Popen([str(edge),'--app='+url,'--window-size=1500,960',
                                  '--user-data-dir='+str(STATE/'desktop-profile'),'--no-first-run'])
                return
    webbrowser.open(url)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=0);parser.add_argument('--desktop',action='store_true')
    args=parser.parse_args();STATE.mkdir(parents=True,exist_ok=True)
    marker=STATE/'server.json'
    if args.desktop and marker.exists():
        old=eda.read_json(marker)
        try:
            from urllib.request import Request,urlopen
            request=Request(old['base']+'/api/state',headers={'X-Studio-Token':old['token']})
            with urlopen(request,timeout=2) as response:
                if response.status==200:open_window(old['url']);return
        except Exception:pass
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    base='http://127.0.0.1:'+str(server.server_port);url=base+'/#'+TOKEN
    eda.write_json(marker,dict(base=base,url=url,token=TOKEN,pid=os.getpid()))
    if args.desktop:threading.Timer(.4,lambda:open_window(url)).start()
    if sys.stdout:print('CMDBUF Studio listening on '+base,flush=True)
    try:server.serve_forever(poll_interval=.3)
    finally:
        server.server_close()
        if marker.exists() and eda.read_json(marker).get('pid')==os.getpid():marker.unlink()


if __name__=='__main__':
    try:main()
    except Exception:
        import traceback
        STATE.mkdir(parents=True,exist_ok=True)
        (STATE/'startup_error.log').write_text(traceback.format_exc(),encoding='utf-8')
        raise
