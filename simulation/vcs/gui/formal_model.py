"""Local FSDB imports and Make/NPI job transport for Studio."""
import json
from pathlib import Path
import re
import tempfile
import zipfile
import shutil
import model

from model import eda, dut_config, ROOT, STATE
from fsdb_formal import core
from fsdb_formal.engine import regression_wave

FORMAT_LABELS = core.FORMAT_LABELS

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


def regression_source(item):
    source = dict(item)
    wave = regression_wave(source)
    source['archive'] = str(wave.parents[2].resolve())
    return source


def source_path(source):
    if source['kind'] == 'regression':
        return regression_wave(source)
    path = IMPORTS/identifier(source['id'])/'wave.fsdb'
    if not path.is_file():
        raise ValueError('找不到已导入的 FSDB: ' + str(path))
    return path


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
            source_path(source)
        elif source['kind']=='regression':
            source.update(batch=identifier(item['batch']),run=identifier(item['run']))
            if 'archive' in item:
                source['archive'] = item['archive']
            source = regression_source(source)
        else:raise ValueError('请选择导入波形或已知回归结果')
        normalized.append(source)
    return dict(sources=normalized,max_edges=core.number(payload.get('max_edges',100000),'采样沿上限',1,1000000),
                format=core.formal_format(payload.get('format', 'cycle')))


def stage(payload, settings, job_id, progress):
    request=selections(payload);request['id']=identifier(job_id)
    for source in request['sources']:
        if source['kind']=='import':
            folder=IMPORTS/source['id'];target=eda.OUT/'fsdb_imports'/source['id']
            target.mkdir(parents=True,exist_ok=True)
            progress('校验 FSDB: '+source['name'])
            if not (target/'wave.fsdb').exists():
                shutil.copyfile(str(folder/'wave.fsdb'),str(target/'wave.fsdb'))
            if file_hash(target/'wave.fsdb')!=eda.read_json(folder/'metadata.json')['sha256']:
                raise ValueError('FSDB 副本哈希不一致，请重新导入')
    local=STATE/'formal_requests'/(job_id+'.json');eda.write_json(local,request)
    return str(local)


def fetch(key):
    key = identifier(key)
    return unpack_export(eda.OUT/'fsdb_formal'/key/'package.zip', expected=key)


def unpack_export(path, expected=None):
    EXPORTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.formal-', dir=str(EXPORTS)) as temp:
        folder = Path(temp).resolve()/'export'
        folder.mkdir()
        with zipfile.ZipFile(str(path)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 16*1024**3:
                raise ValueError('导出包解压后超过 16 GB')
            for item in archive.infolist():
                target = (folder/item.filename).resolve()
                if (folder not in target.parents or '\\' in item.filename or item.filename == 'package.zip'
                        or (item.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError('导出包含不安全路径')
            archive.extractall(str(folder))
        manifest = eda.read_json(folder/'SHA256SUMS.json')
        for rel, digest in manifest.items():
            target = (folder/rel).resolve()
            if folder not in target.parents or file_hash(target) != digest:
                raise ValueError('导出文件校验失败: '+rel)
        summary = 'summary.json' if (folder/'summary.json').is_file() else 'inspection.json'
        if summary not in manifest or (expected is None and 'run_bmc.sby' not in manifest):
            raise ValueError('请选择 Studio 导出的 Formal 验证包')
        key = identifier(eda.read_json(folder/summary)['id']) if summary == 'summary.json' else expected
        if expected is not None and key != expected:
            raise ValueError('验证包编号不一致')
        destination = EXPORTS/key
        if destination.exists():
            if (destination/'SHA256SUMS.json').read_bytes() != (folder/'SHA256SUMS.json').read_bytes():
                raise ValueError('同一导出编号已有其他版本的验证包')
            return key
        shutil.copyfile(str(path), str(folder/'package.zip'))
        try:
            folder.rename(destination)
        except OSError:
            if (not destination.is_dir() or
                    (destination/'SHA256SUMS.json').read_bytes() != (folder/'SHA256SUMS.json').read_bytes()):
                raise
        return key


def artifact(key,name):
    allowed={'package.zip','summary.json','inspection.json','trace_properties.sv','formal_top.sv','files.f',
             'samples.csv','transactions.json','trace.json','integration.json','run_bmc.sby','run_cover.sby','sby_adaptation.json'}
    if name not in allowed:raise ValueError('不能打开此导出文件')
    path=EXPORTS/identifier(key)/name
    if not path.is_file():raise ValueError('导出文件不存在')
    return path


def latest_proof(key):
    root = eda.OUT/'formal_runs'/identifier(key)
    records = []
    manifest = EXPORTS/key/'SHA256SUMS.json'
    checksum = file_hash(manifest) if manifest.is_file() else None
    paths = root.glob('*/proof.json')
    for path in paths:
        record = eda.read_json(path)
        if record['status'] != 'RUNNING' and record.get('package_manifest_sha256') == checksum:
            records.append((path.parent, record))
    return max(records, key=lambda item: item[1].get('started_at', 0)) if records else (None, None)


def import_file(path, progress=lambda text: None):
    path=Path(path).resolve()
    meta=begin_upload(dict(name=path.name,size=path.stat().st_size))
    offset=0
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(8*1024**2),b''):
            chunk_upload(meta['id'],offset,data);offset+=len(data)
            progress('导入 %s · %d%%' % (path.name,100*offset//meta['size']))
    return finish_upload(meta)
