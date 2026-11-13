"""Process timing: unlimited license/startup wait, optional post-start deadline."""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid

_cancelled=threading.Event()
_events=threading.Lock()


class RegressionStopped(InterruptedError):
    """The regression requested a stop before coverage finalization."""


def check_stop(stop_file):
    if stop_file is not None and stop_file.is_file():
        raise RegressionStopped('Regression stop requested')


def install_signal_handlers():
    """CLI only: interrupt queued workers as well as their child process groups."""
    def cancel(signum, frame):
        _cancelled.set()
        raise KeyboardInterrupt
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,cancel)


def save(path, value):
    temporary=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(str(temporary),str(path))


def stop(process):
    if process.poll() is not None:return
    try:
        if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
        else:process.terminate()
    except ProcessLookupError:return
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
            else:process.kill()
        except ProcessLookupError:pass
        process.wait()


def run(cmd,cwd,log,env,timeout,wait_for_start=False,phase='tool',stop_file=None):
    if _cancelled.is_set():raise InterruptedError('EDA job cancelled')
    check_stop(stop_file)
    log=Path(log);log.parent.mkdir(parents=True,exist_ok=True)
    limit=None if timeout is None or timeout==0 else timeout
    if limit is not None and limit<0:raise ValueError('Execution timeout must be nonnegative')
    marker=log.with_suffix('.started')
    if wait_for_start and marker.exists():marker.unlink()
    child_env=dict(env)
    # Never inherit another concurrently running job's marker path.
    child_env.pop('CMDBUF_STUDIO_START_FILE',None)
    if wait_for_start:child_env['CMDBUF_STUDIO_START_FILE']=str(marker.resolve())
    scope='execution_only' if wait_for_start else 'unlimited' if limit is None else 'process'
    save(log.with_suffix('.command.json'),dict(argv=cmd,cwd=str(cwd),timeout_seconds=limit,
         timeout_scope=scope,stdin='closed',license_wait_limit_seconds=None if scope!='process' else 'not_a_license_wait_command'))
    start=time.monotonic();active=None if wait_for_start else start
    state=dict(phase=phase,task=Path(cwd).name,state='WAITING_FOR_START' if wait_for_start else 'RUNNING',
               timeout_scope=scope,execution_timeout_seconds=limit,license_wait_limit_seconds=None,
               startup_wait_seconds=None,execution_seconds=None)
    key=str(log.resolve())

    def emit(status):
        state['state']=status
        save(log.with_suffix('.process.json'),state)
        with _events:print('EDA_PROCESS '+json.dumps(dict(key=key,**state),ensure_ascii=False),flush=True)

    emit(state['state'])
    with log.open('w',encoding='utf-8') as output:
        process=None;rc=None;failure_state=None
        try:
            # Batch jobs must not inherit the GUI/terminal's live stdin. A DUT
            # $stop may enter UCLI; EOF ends that interrupted run instead of
            # waiting indefinitely for someone to type a debugger command.
            process=subprocess.Popen(cmd,cwd=str(cwd),env=child_env,stdin=subprocess.DEVNULL,stdout=output,
                                     stderr=subprocess.STDOUT,start_new_session=True)
            while True:
                if _cancelled.is_set():raise InterruptedError('EDA job cancelled')
                now=time.monotonic()
                if wait_for_start and active is None and marker.is_file() and marker.stat().st_size:
                    active=now
                    state['startup_wait_seconds']=round(now-start,3)
                    emit('RUNNING')
                rc=process.poll()
                if rc is not None:break
                check_stop(stop_file)
                if active is not None and limit is not None and now-active>=limit:
                    stop(process);output.write('\nEDA_RUNNER_TIMEOUT\n');rc=124;break
                # A missing start callback never spends the execution timeout.
                time.sleep(.05)
        except BaseException:
            if process is not None:
                stop(process)
                rc=process.returncode
                failure_state='INTERRUPTED'
            else:
                failure_state='LAUNCH_ERROR'
            raise
        finally:
            end=time.monotonic()
            state.update(seconds=round(end-start,3),returncode=rc,
                         execution_seconds=round(end-active,3) if active is not None else None)
            if wait_for_start and active is None:state['startup_wait_seconds']=round(end-start,3)
            if failure_state:emit(failure_state)
            elif rc is not None:emit('TIMEOUT' if rc==124 else 'FINISHED')
    return rc,round(time.monotonic()-start,3)
