"""Shared FSDB startup preset for regression and imported-wave Make targets."""
from pathlib import Path
import shutil
import time


def prepare(folder, env, scope='', default_scope='tb_cmdbuf_rd.dut'):
    folder = Path(folder)
    script = folder / 'waves.tcl'
    shutil.copyfile(str(Path(__file__).with_name('waves.tcl')), str(script))
    # Pass scope names as data, never interpolate them into executable Tcl.
    env['CMDBUF_WAVE_DUT_SCOPE'] = scope or ''
    env['CMDBUF_WAVE_DEFAULT_SCOPE'] = default_scope
    return ['-play', str(script)]


def wait_ready(process, folder):
    status = Path(folder) / 'wave_autoload.status'
    print('VERDI waiting for waveform initialization; status: ' + str(status), flush=True)
    # Do not report a successful launch before Tcl has verified the signal list.
    # License/startup waits have no deadline; closing Verdi ends this wait.
    while not status.is_file():
        if process.poll() is not None:
            raise RuntimeError('Verdi exited before loading DUT signals. See ' + str(Path(folder) / 'verdi.log'))
        time.sleep(.2)
    lines = status.read_text(encoding='utf-8').splitlines()
    if len(lines) < 3 or lines[0] != 'READY':
        raise RuntimeError('Verdi DUT signal loading failed: ' + '\n'.join(lines[1:]) +
                           '\nSee ' + str(Path(folder) / 'verdi.log'))
    print('VERDI DUT={} signals={} (all signals in this scope)'.format(lines[1], lines[2]))
