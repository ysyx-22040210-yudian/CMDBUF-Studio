"""Migration invariants: no device defaults, isolated settings and offline distribution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
import platform_config as config
import portable


class PortabilityTests(unittest.TestCase):
    def test_clean_defaults_and_separate_machine_settings(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(config,'CONFIG',Path(temp)/'eda.local.json'),patch.dict(os.environ,{},clear=True):
            clean=config.load()
            self.assertEqual(clean['host'],'');self.assertEqual(clean['tool_setup'],'')
            saved=config.save(dict(clean,mode='ssh',host='my-eda',vm_root='/tmp/my_eda_project',tool_setup='/opt/site/env.sh'))
            self.assertEqual(config.load()['host'],'my-eda')
            with patch.dict(os.environ,{'EDA_VM_HOST':'other-eda'}):self.assertEqual(config.load()['host'],'other-eda')
            self.assertEqual(json.loads(config.CONFIG.read_text())['host'],'my-eda')
            self.assertNotIn('license',saved)

    def test_bad_remote_targets_fail_before_execution(self):
        base=dict(mode='ssh',host='user@host',vm_root='/home/user/cmdbuf',display='localhost:10.0')
        config.validate(base)
        for fields in [dict(host='-oProxyCommand=bad'),dict(host='host; echo bad'),dict(vm_root='/'),
                       dict(vm_root='/tmp'),dict(vm_root='/tmp/a/../b'),dict(tool_setup='/tmp/a\ncommand')]:
            with self.assertRaises(ValueError):config.validate(dict(base,**fields))

    def test_distribution_contains_offline_runtime_and_no_machine_data(self):
        paths={p.relative_to(portable.ROOT).as_posix() for p in portable.selected_files()}
        for rel in ('simulation/vcs/platform_config.py','simulation/vcs/gui/web/vendor/codemirror/lib/codemirror.js',
                    'simulation/vcs/fsdb_formal/npi_reader.cpp','CMDBUF Studio.sh','CMDBUF Studio.cmd','rtl/cmdbuf_rd.sv'):
            self.assertIn(rel,paths)
        self.assertNotIn('eda.local.json',paths)
        self.assertFalse(any(p.startswith(('build/','verification/','dist/')) for p in paths))
        self.assertFalse(any('/plans/' in p for p in paths))
        for rel in ('simulation/vcs/project.json','simulation/vcs/toolchain.sh','simulation/vcs/platform_config.py'):
            source=(portable.ROOT/rel).read_text(encoding='utf-8')
            self.assertNotIn('192.168.31.116',source)
            self.assertNotIn('27000@IC_EDA',source)
            self.assertNotIn('/home/synopsys/',source)

    @unittest.skipIf(os.name=='nt','Tool discovery executes Linux bash')
    def test_site_script_banner_and_path_discovery(self):
        with tempfile.TemporaryDirectory(prefix='cmdbuf discovery ') as tmp:
            base=Path(tmp)
            for tool in ('vcs','verdi','urg'):
                home=base/('verdi' if tool=='verdi' else 'vcs')
                (home/'bin').mkdir(parents=True,exist_ok=True)
                file=home/'bin'/tool;file.write_text('#!/bin/sh\nexit 0\n');file.chmod(0o755)
            setup=base/'site setup.sh'
            setup.write_text('echo "site environment banner"\nexport PATH='+config.shlex.quote(str(base/'vcs/bin')+':'+str(base/'verdi/bin'))+':/usr/bin:/bin\n')
            environment={'PATH':'/usr/bin:/bin','EDA_TOOL_SETUP':str(setup)}
            with patch.object(config,'CONFIG',base/'missing.json'),patch.dict(os.environ,environment,clear=True):
                result=config.tool_env()
                self.assertEqual(result['VCS_HOME'],str(base/'vcs'))
                self.assertEqual(result['VERDI_HOME'],str(base/'verdi'))
                self.assertNotIn('site environment banner',result)
            setup.write_text('return 23\n')
            with patch.object(config,'CONFIG',base/'missing.json'),patch.dict(os.environ,environment,clear=True):
                with self.assertRaises(RuntimeError):config.tool_env()

    def test_component_discovery_rejects_partial_installs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);pli=base/'pli';pli.mkdir()
            (pli/'novas.tab').touch()
            with self.assertRaises(RuntimeError):config.pli_directory({'EDA_FSDB_PLI':str(pli)})
            (pli/'pli.a').touch();self.assertEqual(config.pli_directory({'EDA_FSDB_PLI':str(pli)}),pli)
            with self.assertRaises(RuntimeError):config.npi_directories({'VERDI_HOME':str(base)})


if __name__=='__main__':unittest.main()
