import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import contracts
import protocol_adapter as adapter
from protocol_cases import basic,interactions


class ProtocolTests(unittest.TestCase):
    def test_current_catalog_is_complete(self):
        a=contracts.audit()
        self.assertEqual(11,a['assumption_instances'])
        self.assertEqual(4,len([r for r in a['rules'] if r['kind']=='environment_assumption']))

    def test_new_or_changed_contract_cannot_silently_run(self):
        c=json.loads((contracts.HERE/'contracts.json').read_text(encoding='utf-8'))
        for change in ('hash','new','missing','status'):
            x=copy.deepcopy(c)
            if change=='hash': x['sources']['rtl/cmdbuf_rd.sv']='0'*64
            if change=='new': x['rules'][0]['id']='E99_NEW_RULE'
            if change=='missing': x['rules'].pop()
            if change=='status': x['rules'][0]['status']='unsupported'
            with self.assertRaisesRegex(ValueError,'CONTRACT_UNSUPPORTED'): contracts.audit(catalog=x)

    def test_requests_have_legal_domains(self):
        for key,value,rule in [('src',3,'E03_SRC'),('resp',1,'E04_ARRESP'),('resp',2,'E04_ARRESP')]:
            c=basic(); c['sim']['requests']['A'][key]=value
            with self.assertRaisesRegex(ValueError,rule): adapter.compile_protocol(c)

    def test_conflicting_owners_and_unknown_policies_rejected(self):
        for mutation in ('owned','unknown','missing','release_reset','duplicate_offer'):
            c=basic()
            if mutation=='owned': c['signal'].append(dict(name='arvalid',wave='0'+'.'*23))
            if mutation=='unknown': c['sim']['drivers']['mem']='anything'
            if mutation=='missing': c['signal'].pop()
            if mutation=='release_reset': c['signal'][2]['wave']='='+'.'*23
            if mutation=='duplicate_offer': c['signal'][2].update(wave='0.=='+'.'*20,data=['A','A'])
            with self.assertRaises(ValueError): adapter.compile_protocol(c)

    def test_archive_captures_controls_and_prepare_before_every_edge(self):
        for cfg in interactions().values():
            p=adapter.compile_protocol(cfg); saved=adapter.read_program(adapter.make_program(cfg))
            self.assertEqual(p['frames'],saved['frames'])
            prep={f['time_ps'] for f in saved['frames'] if f['prepare']}
            self.assertEqual({s['time_ps']-p['clock_skew_ps'] for s in p['samples']},prep)
            self.assertEqual(p['flags'],saved['flags'])

    def test_offers_are_one_shot_and_timeline_is_not_paused(self):
        p=adapter.compile_protocol(interactions()['protocol_demo'])
        self.assertEqual(list(range(12)),[f['release'] for f in p['frames'] if f['release']>=0])
        self.assertEqual(720000,p['duration_ps'])
        self.assertEqual(69,len(p['samples']))
        self.assertIn('clk',p['fixed']); self.assertNotIn('arvalid',p['fixed'])


if __name__=='__main__': unittest.main()
