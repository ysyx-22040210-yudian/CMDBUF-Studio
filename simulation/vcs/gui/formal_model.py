"""Local FSDB imports and Make/NPI job transport for Studio."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import zipfile
import shutil
import model

from model import eda, dut_config, ROOT, STATE
sys.path.insert(0,str(eda.HERE/'fsdb_formal'))
import core

IMPORTS=STATE/'fsdb_imports'
EXPORTS=STATE/'formal_exports'


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):
        raise ValueError('无效的波形或导出编号')
    return value


def begin_upload(payload):
    name=str(payload['name']).replace('\\','/').split('/')[-1]
    if not name.lower().endswith('.fsdb') or len(name)>240:
        raise ValueError('请选择 .fsdb 文件')
    size=core.number(payload['size'],'FSDB 文件大小',1,64*1024**3)
    key=eda.stamp()+'_'+eda.uuid.uuid4().hex[:10]
    folder=IMPORTS/key;folder.mkdir(parents=True,exist_ok=False)
    result=dict(id=key,name=name,size=size,received=0,ready=False)
    eda.write_json(folder/'metadata.json',result)
    (folder/'upload.part').touch()
    return result


def chunk_upload(key,offset,data):
    folder=IMPORTS/identifier(key);meta=eda.read_json(folder/'metadata.json')
    if meta['ready'] or offset!=meta['received'] or not 0<len(data)<=8*1024**2 or offset+len(data)>meta['size']:
        raise ValueError('上传分片顺序、大小或文件状态无效')
    if (folder/'upload.part').stat().st_size!=offset:
        raise ValueError('上传文件长度不一致，请重新导入')
    with (folder/'upload.part').open('ab') as f:f.write(data)
    meta['received']+=len(data);eda.write_json(folder/'metadata.json',meta)
    return dict(received=meta['received'],size=meta['size'])


def finish_upload(payload):
    folder=IMPORTS/identifier(payload['id']);meta=eda.read_json(folder/'metadata.json')
    if meta['ready'] or meta['received']!=meta['size'] or (folder/'upload.part').stat().st_size!=meta['size']:
        raise ValueError('FSDB 尚未完整上传')
    meta.update(sha256=file_hash(folder/'upload.part'),ready=True)
    (folder/'upload.part').replace(folder/'wave.fsdb')
    eda.write_json(folder/'metadata.json',meta)
    return meta


def file_hash(path):
    h=eda.hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(4*1024**2),b''):h.update(data)
    return h.hexdigest()


def state():
    imports=[];exports=[]
    if IMPORTS.exists():
        for p in sorted(IMPORTS.glob('*/metadata.json'),reverse=True):
            m=eda.read_json(p)
            if m['ready']:imports.append(m)
    if EXPORTS.exists():
        for p in sorted(EXPORTS.glob('*/summary.json'),reverse=True):
            exports.append(eda.read_json(p))
    saved=eda.read_json(STATE/'formal_selection.json') if (STATE/'formal_selection.json').exists() else None
    return dict(imports=imports,exports=exports[:100],signals=core.SIGNALS,selection=saved,dut_name=dut_config.load()['name'])


def selections(payload):
    sources=payload.get('sources',[])
    if not isinstance(sources,list) or not 1<=len(sources)<=100:
        raise ValueError('请选择 1..100 个 FSDB')
    normalized=[];patterns=set()
    for item in sources:
        pattern=core.number(item['pattern'],'pattern')
        if pattern in patterns:raise ValueError('所选 FSDB 的 pattern 编号不能重复')
        patterns.add(pattern)
        name=str(item.get('name','')).strip()
        if not name or len(name)>240:raise ValueError('波形名称为空或过长')
        source=dict(kind=item['kind'],name=name,pattern=pattern,scope=item.get('scope','tb_cmdbuf_rd.dut'),mapping=item.get('mapping',{}))
        core.mapping(source['scope'],source['mapping'])
        if source['kind']=='import':
            source['id']=identifier(item['id']);meta=eda.read_json(IMPORTS/source['id']/'metadata.json')
            if not meta['ready']:raise ValueError('FSDB 尚未完整上传')
        elif source['kind']=='regression':
            source.update(batch=identifier(item['batch']),run=identifier(item['run']))
            folder=model.results_root()/source['batch']
            rows=eda.read_json(folder/'summary.json')['results']
            row=next((r for r in rows if r['id']==source['run']),None)
            if row is None or row.get('waves_enabled',True) is False:
                raise ValueError('所选运行没有 FSDB 波形')
        else:raise ValueError('请选择导入波形或已知回归结果')
        normalized.append(source)
    return dict(sources=normalized,max_edges=core.number(payload.get('max_edges',100000),'采样沿上限',1,1000000))


def stage(payload,settings,job_id,progress):
    request=selections(payload);request['id']=identifier(job_id)
    if settings['mode']=='local':
        for source in request['sources']:
            if source['kind']=='import':
                folder=IMPORTS/source['id'];target=eda.OUT/'fsdb_imports'/source['id']
                target.mkdir(parents=True,exist_ok=True)
                if not (target/'wave.fsdb').exists():shutil.copyfile(str(folder/'wave.fsdb'),str(target/'wave.fsdb'))
                if file_hash(target/'wave.fsdb')!=eda.read_json(folder/'metadata.json')['sha256']:
                    raise ValueError('本地 FSDB 副本哈希不一致')
        local=STATE/'formal_requests'/(job_id+'.json');eda.write_json(local,request)
        return str(local)
    host,root=settings['host'],settings['vm_root'];q=shlex.quote
    for source in request['sources']:
        if source['kind']!='import':continue
        folder=IMPORTS/source['id'];remote=root+'/results/fsdb_imports/'+source['id']
        meta=eda.read_json(folder/'metadata.json')
        progress('上传 FSDB: '+source['name'])
        eda.ssh(host,'set -e\nmkdir -p '+q(remote)+'\n',capture=True)
        existing=eda.ssh(host,'if test -f '+q(remote+'/sha256')+'; then cat '+q(remote+'/sha256')+'; fi\n',capture=True).stdout.decode().strip()
        if existing==meta['sha256']:continue
        subprocess.check_call(['scp','-q','-o','BatchMode=yes',str(folder/'wave.fsdb'),host+':'+remote+'/wave.part'])
        verify='set -e\ncd '+q(remote)+'\nprintf "%s  wave.part\\n" '+q(meta['sha256'])+' | sha256sum -c - >/dev/null\nmv wave.part wave.fsdb\nprintf "%s" '+q(meta['sha256'])+' > sha256\n'
        eda.ssh(host,verify,capture=True)
    local=STATE/'formal_requests'/(job_id+'.json');eda.write_json(local,request)
    remote=root+'/results/fsdb_requests/'+job_id+'.json'
    eda.ssh(host,'mkdir -p '+q(root+'/results/fsdb_requests')+'\n',capture=True)
    subprocess.check_call(['scp','-q','-o','BatchMode=yes',str(local),host+':'+remote])
    return remote


def fetch(settings,key):
    key=identifier(key);folder=EXPORTS/key;folder.mkdir(parents=True,exist_ok=True)
    if settings['mode']=='local':
        shutil.copyfile(str(eda.OUT/'fsdb_formal'/key/'package.zip'),str(folder/'package.zip'))
    else:
        remote=settings['vm_root']+'/results/fsdb_formal/'+key+'/package.zip'
        subprocess.check_call(['scp','-q','-o','BatchMode=yes',settings['host']+':'+remote,str(folder/'package.zip')])
    with zipfile.ZipFile(str(folder/'package.zip')) as archive:
        if sum(i.file_size for i in archive.infolist())>16*1024**3:raise ValueError('导出包解压后超过 16 GB')
        for item in archive.infolist():
            target=(folder/item.filename).resolve()
            if folder.resolve() not in target.parents or (item.external_attr>>16)&0o170000==0o120000:
                raise ValueError('导出包含不安全路径')
        archive.extractall(str(folder))
    manifest=eda.read_json(folder/'SHA256SUMS.json')
    for rel,digest in manifest.items():
        path=(folder/rel).resolve()
        if folder.resolve() not in path.parents or file_hash(path)!=digest:raise ValueError('导出文件校验失败: '+rel)
    return key


def artifact(key,name):
    allowed={'package.zip','summary.json','inspection.json','trace_properties.sv','formal_top.sv','files.f',
             'samples.csv','transactions.json','trace.json','integration.json','run_bmc.sby','run_cover.sby','sby_adaptation.json'}
    if name not in allowed:raise ValueError('不能打开此导出文件')
    path=EXPORTS/identifier(key)/name
    if not path.is_file():raise ValueError('导出文件不存在')
    return path
