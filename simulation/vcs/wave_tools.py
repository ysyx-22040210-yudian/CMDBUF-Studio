"""Make entry points for imported FSDB display and native-viewer conversion."""
import argparse
from pathlib import Path
import subprocess
import eda
import verdi_wave


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['view', 'convert'])
    p.add_argument('--fsdb', required=True)
    p.add_argument('--output')
    p.add_argument('--dut-scope', default='')
    args = p.parse_args()
    path = Path(args.fsdb).resolve()
    if path.suffix.lower() != '.fsdb' or not path.is_file():
        raise ValueError('请选择存在的 FSDB 文件')
    env = eda.platform_config.tool_env(required=('fsdb2vcd',) if args.action == 'convert' else ('verdi',))
    if args.action == 'convert':
        if not args.output:
            raise ValueError('OUTPUT 必须指向生成的 VCD')
        output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix('.partial.vcd')
        rc = subprocess.call(['fsdb2vcd', str(path), '-o', str(temporary), '-sv', '-keep_last_time'], env=env)
        if rc or not temporary.is_file():
            raise RuntimeError('fsdb2vcd 转换失败')
        temporary.replace(output); print('VCD '+str(output))
    else:
        if not env.get('DISPLAY'):
            raise RuntimeError('请在 Linux 图形桌面运行 Verdi；当前未设置 DISPLAY')
        folder = eda.OUT/'views'/('import_'+eda.stamp()+'_'+eda.uuid.uuid4().hex[:6]); folder.mkdir(parents=True)
        cmd = ['verdi', '-full64', '-ssf', str(path)]
        cmd += verdi_wave.prepare(folder, env, args.dut_scope, eda.PROJECT['top'] + '.dut')
        eda.write_json(folder/'command.json', dict(argv=cmd, DISPLAY=env['DISPLAY']))
        with (folder/'verdi.log').open('w') as log:
            process = subprocess.Popen(cmd, cwd=str(folder), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        verdi_wave.wait_ready(process, folder)
        print('VERDI pid='+str(process.pid))


if __name__ == '__main__':
    main()
