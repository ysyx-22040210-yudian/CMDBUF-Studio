"""License/startup waiting must not consume the simulation execution deadline."""
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gui'))
import execution
import model
import backend


class ExecutionTests(unittest.TestCase):
    def child(self,folder,source,timeout=.2,wait=True,name='sim'):
        root=Path(folder);log=root/(name+'.log')
        env=os.environ.copy();env['CMDBUF_STUDIO_START_FILE']='must_not_be_inherited'
        result=execution.run([sys.executable,'-c',source],root,log,env,timeout,
                             wait_for_start=wait,phase='simulation')
        self.assertEqual(env['CMDBUF_STUDIO_START_FILE'],'must_not_be_inherited')
        return result,json.loads(log.with_suffix('.process.json').read_text())

    def test_wait_longer_than_execution_limit_then_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'sim.started').write_text('stale')
            (rc,elapsed),state=self.child(folder,
                "import os,time;time.sleep(.6);open(os.environ['CMDBUF_STUDIO_START_FILE'],'w').write('started');time.sleep(.05)")
            self.assertEqual(rc,0);self.assertGreater(elapsed,.6)
            self.assertGreater(state['startup_wait_seconds'],.5)
            self.assertEqual(state['timeout_scope'],'execution_only')
            self.assertIsNone(state['license_wait_limit_seconds'])

    def test_execution_hang_times_out_after_start(self):
        with tempfile.TemporaryDirectory() as folder:
            (rc,elapsed),state=self.child(folder,
                "import os,time;time.sleep(.5);open(os.environ['CMDBUF_STUDIO_START_FILE'],'w').write('started');time.sleep(20)")
            self.assertEqual(rc,124);self.assertGreater(elapsed,.7)
            self.assertEqual(state['state'],'TIMEOUT')
            self.assertGreaterEqual(state['execution_seconds'],.2)

    def test_zero_execution_timeout_and_legacy_unlimited(self):
        with tempfile.TemporaryDirectory() as folder:
            for timeout,wait,source in [(0,True,"import os,time;open(os.environ['CMDBUF_STUDIO_START_FILE'],'w').write('started');time.sleep(.3)"),
                                       (None,False,"import os,time;assert 'CMDBUF_STUDIO_START_FILE' not in os.environ;time.sleep(.3)")]:
                (rc,_),state=self.child(folder,source,timeout,wait)
                self.assertEqual(rc,0);self.assertIsNone(state['execution_timeout_seconds'])

    def test_failure_before_start_exits_without_hanging(self):
        with tempfile.TemporaryDirectory() as folder:
            (rc,_),state=self.child(folder,'raise SystemExit(7)')
            self.assertEqual(rc,7);self.assertIsNone(state['execution_seconds'])

    def test_batch_child_does_not_wait_on_open_terminal_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source="""import os,sys
from pathlib import Path
sys.path.insert(0,{module!r})
import execution
root=Path({folder!r})
rc,_=execution.run([sys.executable,'-c',"import sys;assert sys.stdin.read()==''"],
                   root,root/'stdin.log',os.environ,1)
raise SystemExit(rc)
""".format(module=str(Path(execution.__file__).parent),folder=folder)
            # Keep the parent's input pipe open for the entire child run.
            parent=subprocess.Popen([sys.executable,'-c',source],stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            try:
                parent.wait(timeout=5)
                output=parent.stdout.read()
                self.assertEqual(parent.returncode,0,output.decode(errors='replace'))
            finally:
                parent.stdin.close();parent.stdout.close()
                if parent.poll() is None:parent.kill();parent.wait()

    def test_parallel_waiters_have_independent_markers(self):
        with tempfile.TemporaryDirectory() as folder:
            def run(index):
                return self.child(folder,"import os,time;time.sleep(.4);open(os.environ['CMDBUF_STUDIO_START_FILE'],'w').write('started')",name='sim'+str(index))
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                rows=list(pool.map(run,range(2)))
            self.assertTrue(all(row[0][0]==0 and row[1]['startup_wait_seconds']>=.4 for row in rows))
            self.assertTrue(all((Path(folder)/('sim'+str(i)+'.started')).is_file() for i in range(2)))

    def test_gui_execution_timeout_zero_and_parallel_wait_status(self):
        plan=model.default_plan();plan['timeout']=0
        self.assertEqual(model.check_plan(plan)['timeout'],0)
        plan['timeout']=-1
        with self.assertRaises(ValueError):model.check_plan(plan)
        jobs=backend.Jobs();job={}
        jobs.process_status(job,dict(key='a',state='WAITING_FOR_START',phase='simulation'))
        jobs.process_status(job,dict(key='b',state='RUNNING',phase='simulation'))
        self.assertIn('仿真运行 1',job['stage']);self.assertIn('启动 1',job['stage'])
        jobs.process_status(job,dict(key='a',state='FINISHED',phase='simulation'))
        self.assertIn('仿真运行 1',job['stage']);self.assertIn('启动 0',job['stage'])

    @unittest.skipUnless(sys.platform=='linux','Linux process groups')
    def test_interrupt_stops_parallel_license_waiters(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            child="import os,time;open('pid','w').write(str(os.getpid()));time.sleep(30)"
            source="""import concurrent.futures,sys
from pathlib import Path
sys.path.insert(0,{module!r})
import execution,os
execution.install_signal_handlers()
root=Path({folder!r})
def run(i):
    p=root/str(i);p.mkdir()
    return execution.run([sys.executable,'-c',{child!r}],p,p/'sim.log',os.environ,.1,True)
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,range(4)))
except KeyboardInterrupt:raise SystemExit(130)
""".format(module=str(Path(execution.__file__).parent),folder=folder,child=child)
            parent=subprocess.Popen([sys.executable,'-c',source],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+8
                while time.monotonic()<deadline and not all((root/str(i)/'pid').is_file() for i in range(2)):time.sleep(.05)
                self.assertTrue(all((root/str(i)/'pid').is_file() for i in range(2)))
                parent.send_signal(signal.SIGINT)
                output,_=parent.communicate(timeout=8)
                self.assertEqual(parent.returncode,130,output.decode(errors='replace'))
                for i in range(2):
                    pid=int((root/str(i)/'pid').read_text())
                    with self.assertRaises(ProcessLookupError):os.kill(pid,0)
                self.assertFalse((root/'2'/'pid').exists());self.assertFalse((root/'3'/'pid').exists())
            finally:
                if parent.poll() is None:parent.kill();parent.wait()


if __name__=='__main__':unittest.main()
