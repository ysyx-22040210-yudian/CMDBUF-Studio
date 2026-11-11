"""Portable DUT selection; the protocol checker remains independent of RTL."""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PORTS = [
    ('clk','input',1), ('rst_n','input',1), ('arvalid','input',1), ('arready','output',3),
    ('araddr','input',18), ('src','input',2), ('arid','input',8), ('ar_port_id','input',5),
    ('arresp','input',2), ('rvalid','output',3), ('rready','input',3), ('rdata','output',128),
    ('rid','output',8), ('r_port_id','output',5), ('raddr','output',3), ('rresp','output',2),
    ('req','output',8), ('arb_addr','output',12), ('pending_cnt','output',4),
    ('ar_rd_gnt','input',8), ('rw_rd_gnt','input',8), ('rd_mem_status','input','[1:0] [7:0]'),
    ('mem_rdata','input',128), ('pc_err','input',1)]


def project_path(value, directory=False):
    path = (ROOT / str(value)).resolve()
    if ROOT not in path.parents or '\n' in str(value) or '\r' in str(value) or '"' in str(value):
        raise ValueError('RTL path must stay inside project: ' + str(value))
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError('RTL path does not exist: ' + str(value))
    return path


def validate(cfg):
    if not isinstance(cfg, dict) or not str(cfg.get('name', '')).strip():
        raise ValueError('请填写 DUT 配置名称')
    files = cfg.get('files', [])
    if not isinstance(files, list) or not files or len(files) != len(set(files)):
        raise ValueError('请选择不重复的 RTL 编译文件，并按依赖排序')
    for f in files:
        if project_path(f).suffix.lower() not in ('.v', '.sv'):
            raise ValueError('编译清单只接受 .v / .sv；头文件请配置 include 目录')
    for d in cfg.get('include_dirs', []):
        project_path(d, True)
    for d in cfg.get('defines', []):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:=[A-Za-z0-9_\x27.:-]+)?', d):
            raise ValueError('非法宏定义: ' + d)
    code = cfg.get('instance', '')
    # This is a wiring editor, never a replacement for authoritative VCS elaboration.
    stripped = re.sub(r'//[^\n]*|/\*.*?\*/', '', code, flags=re.S)
    if not re.search(r'\bdut\s*\(', stripped) or re.search(r'\b(?:module|endmodule|class|program)\b', stripped):
        raise ValueError('例化代码须保留实例名 dut，填写端口连接/适配语句，不要粘贴 module 定义')
    if not 1 <= len(code) <= 100000:
        raise ValueError('例化代码长度无效')
    return {k: cfg.get(k, []) for k in ('name', 'files', 'include_dirs', 'defines', 'instance')}


def load():
    return validate(json.loads((HERE / 'dut.json').read_text(encoding='utf-8')))


def source_files():
    cfg = load()
    paths = {project_path(f) for f in cfg['files']}
    for directory in cfg['include_dirs']:
        paths.update(p for p in project_path(directory, True).rglob('*')
                     if p.is_file() and p.suffix.lower() in ('.vh', '.svh', '.v', '.sv'))
    return sorted(paths)
