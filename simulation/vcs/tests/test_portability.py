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
            self.assertEqual(clean['mode'],'local');self.assertEqual(clean['tool_setup'],'')
            saved=config.save(dict(clean,profile='manual',tool_setup='/opt/site/env.sh'))
            self.assertEqual(config.load()['tool_setup'],'/opt/site/env.sh')
            with patch.dict(os.environ,{'EDA_TOOL_SETUP':'/opt/other/env.sh'}):self.assertEqual(config.load()['tool_setup'],'/opt/other/env.sh')
            self.assertEqual(json.loads(config.CONFIG.read_text())['tool_setup'],'/opt/site/env.sh')
            self.assertNotIn('license',saved)

    def test_local_config_validates_paths_and_rejects_remote_mode(self):
        base=dict(mode='local',display=':0')
        config.validate(base)
        for fields in [dict(mode='ssh'),dict(display='bad'),dict(vcs_home='relative/path'),dict(tool_setup='/tmp/a\ncommand')]:
            with self.assertRaises(ValueError):config.validate(dict(base,**fields))

    def test_old_ssh_settings_and_environment_cannot_change_execution_host(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(config,'CONFIG',Path(temp)/'eda.local.json'),patch.dict(os.environ,{'EDA_MODE':'ssh','EDA_VM_HOST':'old-host'},clear=True):
            config.CONFIG.write_text(json.dumps(dict(mode='ssh',host='old-host',vm_root='/old/project',tool_setup='/opt/site/env.sh')))
            value=config.load()
            self.assertEqual(value['mode'],'local');self.assertNotIn('host',value);self.assertNotIn('vm_root',value)
            self.assertEqual(value['tool_setup'],'')
            self.assertTrue(value['legacy_config_ignored'])

    def test_overrides_are_opt_in_and_bound_to_this_device(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(config,'CONFIG',Path(temp)/'eda.local.json'),patch.dict(os.environ,{},clear=True):
            config.save(dict(profile='manual',tool_setup='/opt/site/env.sh'))
            self.assertEqual(config.load()['profile'],'manual')
            with patch.object(config,'machine_id',return_value='linux:other-device'):
                self.assertEqual(config.load()['tool_setup'],'')
                self.assertEqual(config.load()['profile'],'auto')
            config.save(dict(profile='auto'))
            self.assertEqual(config.load()['tool_setup'],'')
            self.assertEqual(config.load()['profile'],'auto')

    @unittest.skipIf(os.name=='nt','Tool discovery executes Linux bash')
    def test_path_discovery_needs_no_config_or_home_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            for tool in ('vcs','verdi','urg'):
                home=base/('verdi' if tool=='verdi' else 'vcs');(home/'bin').mkdir(parents=True,exist_ok=True)
                file=home/'bin'/tool;file.write_text('#!/bin/sh\nexit 0\n');file.chmod(0o755)
            environment={'PATH':str(base/'vcs/bin')+':'+str(base/'verdi/bin')+':/usr/bin:/bin','DISPLAY':':12','XAUTHORITY':'/tmp/current-session'}
            with patch.object(config,'CONFIG',base/'missing.json'),patch.dict(os.environ,environment,clear=True):
                result=config.tool_env()
                self.assertEqual(result['VCS_HOME'],str(base/'vcs'));self.assertEqual(result['VERDI_HOME'],str(base/'verdi'))
                self.assertEqual(result['DISPLAY'],':12');self.assertEqual(result['XAUTHORITY'],'/tmp/current-session')
                self.assertFalse(config.CONFIG.exists())

    def test_distribution_contains_offline_runtime_and_no_machine_data(self):
        paths={p.relative_to(portable.ROOT).as_posix() for p in portable.selected_files()}
        for rel in ('simulation/vcs/platform_config.py','simulation/vcs/gui/desktop.py','simulation/vcs/gui/widgets.py',
                    'simulation/vcs/fsdb_formal/npi_reader.cpp','CMDBUF Studio.sh','CMDBUF Studio.cmd','rtl/cmdbuf_rd.sv'):
            self.assertIn(rel,paths)
        self.assertNotIn('eda.local.json',paths)
        self.assertFalse(any(p.startswith(('build/','verification/','dist/')) for p in paths))
        self.assertFalse(any('/plans/' in p for p in paths))
        self.assertFalse(any('/gui/web/' in p or p.endswith(('server.py','vm_make.py')) for p in paths))
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
