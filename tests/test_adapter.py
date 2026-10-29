"""Offline unit/protocol tests; tests/ci.py covers real browser integration."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from adapter import Adapter,AdapterError,prepare_cart
from pico8_mcp import TOOLS
from profiles import ProfileLease,ProfileBusy
from installation import install_common
from pico8_paths import app_home,browser_profile
import profiling
from bundles import bundle_specs

HERE=Path(__file__).resolve().parent

class Client:
    def __init__(self,root,cache=None):
        args=[sys.executable,'-B',str(REPO/'pico8_mcp.py'),'--root',str(root)]
        if cache:args+=['--cache',str(cache)]
        self.proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1)
        self.ident=0
    def request(self,method,params=None):
        self.ident+=1;ident=self.ident
        request={'jsonrpc':'2.0','id':ident,'method':method,'params':params or {}}
        self.proc.stdin.write(json.dumps(request)+'\n');self.proc.stdin.flush()
        line=self.proc.stdout.readline()
        if not line:raise AssertionError('MCP process exited: '+self.proc.stderr.read())
        response=json.loads(line);assert response['id']==ident,response
        return response
    def call(self,tool_name,allow_error=False,**args):
        result=self.request('tools/call',{'name':'pico8_'+tool_name,'arguments':args})['result']
        if result.get('isError') and not allow_error:raise AssertionError(json.dumps(result))
        return result
    def data(self,tool_name,**args):return self.call(tool_name,**args)['structuredContent']
    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close();self.proc.wait(timeout=15)
        assert self.proc.returncode==0,self.proc.stderr.read()
        self.proc.stdout.close();self.proc.stderr.close()

class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp_root=REPO/'debug/unit';self.temp_root.mkdir(parents=True,exist_ok=True)
        isolated=tempfile.TemporaryDirectory(dir=self.temp_root)
        self.addCleanup(isolated.cleanup)
        patch=mock.patch.dict(os.environ,{'PICO8_MCP_HOME':str(Path(isolated.name)/'common')})
        patch.start();self.addCleanup(patch.stop)
    def test_shared_install_is_idempotent(self):
        first=install_common();self.assertTrue(first['updatedFiles'])
        second=install_common();self.assertEqual(second['updatedFiles'],[])
        self.assertTrue(Path(second['entrypoint']).is_file())
        self.assertEqual(browser_profile(),app_home()/'profiles/localhost')
    def test_profile_lock_and_crash_recovery(self):
        profile=app_home()/'profiles/localhost'
        first=ProfileLease(profile).acquire()
        try:
            (profile/'reuse-marker').write_text('retained')
            with self.assertRaises(ProfileBusy):ProfileLease(profile).acquire()
        finally:first.record(status='closed');first.release()
        script='from profiles import ProfileLease;import os,sys;lease=ProfileLease(sys.argv[1]).acquire();os._exit(0)'
        result=subprocess.run([sys.executable,'-c',script,str(profile)],cwd=REPO,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        again=ProfileLease(profile).acquire()
        try:self.assertEqual((profile/'reuse-marker').read_text(),'retained')
        finally:again.record(status='closed');again.release()
    def test_posix_paths(self):
        base=self.temp_root.resolve()
        with mock.patch('pico8_paths.sys.platform','linux'),mock.patch.dict(os.environ,{'XDG_DATA_HOME':str(base/'xdg')},clear=True):
            self.assertEqual(app_home(),base/'xdg/pico8mcp')
        with mock.patch('pico8_paths.sys.platform','win32'),mock.patch.dict(os.environ,{'LOCALAPPDATA':str(base/'local')},clear=True):
            self.assertEqual(app_home(),base/'local/pico8mcp')
    def test_bridge_injection_preserves_sections_and_does_not_edit_source(self):
        original=b'pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\nfunction _draw() cls() end\n__gfx__\n'+b'0'*128+b'\n'
        payload,meta=prepare_cart(original,'smoke.p8',True,True)
        self.assertIn(b'PICO-8 MCP bridge v2',payload)
        self.assertIn(b'local function rectfill',payload)
        self.assertTrue(payload.endswith(b'0'*128+b'\n'))
        self.assertTrue(meta['countDraws'])
    def test_plain_lua_has_complete_footer(self):
        payload,_=prepare_cart(b'function _draw() cls() end','input.lua',False)
        self.assertIn(b'\n__gfx__\n',payload)
        self.assertIn(b'function _draw() cls() end\n',payload)
    def test_png_injection_rejected(self):
        with self.assertRaises(AdapterError):prepare_cart(b'PNG','cart.p8.png',True)
    def test_path_containment(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            a=Adapter(folder)
            with self.assertRaises(AdapterError):a.path('../escape.png',True)
            a.close()
    def test_cached_runtime_without_network(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root=Path(folder);cache=root/'cache';cache.mkdir()
            payload=b'var Module={};'
            (cache/'runtime.js').write_bytes(payload)
            (cache/'manifest.json').write_text(json.dumps({'sha256':hashlib.sha256(payload).hexdigest()}))
            a=Adapter(root,cache)
            self.assertTrue(a.setup()['cached'])
            (cache/'runtime.js').write_bytes(b'corrupt')
            with self.assertRaises(AdapterError):a.setup()
            a.close()
    def test_build_rejects_paths_before_launch(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root=Path(folder);(root/'floor.lua').write_text('function _draw() cls() end')
            a=Adapter(root)
            with mock.patch.object(a,'start') as start:
                for args in [dict(cart='floor.lua',out='../escape.p8.png'),
                             dict(cart='floor.lua',out='floor.png'),
                             dict(cart='floor.lua',label_frames=0)]:
                    with self.assertRaises(AdapterError):a.build(**args)
                start.assert_not_called()
            a.close()
    def test_stdio_pipeline(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            c=Client(folder)
            try:
                init=c.request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'tests','version':'1'}})
                self.assertEqual(init['result']['protocolVersion'],'2025-06-18')
                c.proc.stdin.write(json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'})+'\n');c.proc.stdin.flush()
                self.assertEqual(c.request('ping')['result'],{})
                listed=c.request('tools/list')['result']['tools']
                self.assertEqual(len(listed),len(TOOLS))
                doctor=c.data('doctor');self.assertFalse(doctor['adminRequired'])
                self.assertFalse(doctor['running'])
                self.assertTrue(c.call('step',frames=0,allow_error=True)['isError'])
                self.assertTrue(c.call('get',name='x',allow_error=True)['isError'])
                self.assertTrue(c.call('missing_\u2603',allow_error=True)['isError'])
                self.assertIn('error',c.request('does/not/exist'))
                c.proc.stdin.write('{broken json\n');c.proc.stdin.flush()
                self.assertEqual(json.loads(c.proc.stdout.readline())['error']['code'],-32700)
            finally:c.close()

class ProfilingTests(unittest.TestCase):
    def test_scopes_are_opt_in_and_assets_unchanged(self):
        source=b'function work() end\nfunction _draw() work() end'
        plain,meta=prepare_cart(source,'test.lua')
        self.assertFalse(meta['profiling'])
        self.assertNotIn(b'function p.enter',plain)
        detailed,meta=prepare_cart(source,'test.lua',profile_functions=['work'])
        self.assertTrue(meta['profiling'])
        self.assertIn(b'work=__p8mcp_prof.wrap(1,work)',detailed)
        self.assertEqual(meta['profileScopes'],[dict(name='work',kind='function')])
        self.assertTrue(detailed.endswith(b'0'*128+b'\n'))
        with self.assertRaises(AdapterError):
            prepare_cart(source,'test.lua',False,profiling_enabled=True)

    def test_markers_ignore_strings_and_long_comments(self):
        source='''local a=[==[
-- @profile begin fake
]==]
--[=[
-- @profile end fake
]=]
local b="-- @profile begin fake"
function _draw()
 -- @profile begin raster
 cls()
 -- @profile end raster
end'''
        prefix,body,suffix,meta=profiling.instrument(source,sections=True)
        self.assertEqual(meta['profileScopes'],[dict(name='raster',kind='section')])
        self.assertIn('__p8mcp_prof.enter(1)',body)
        self.assertIn('__p8mcp_prof.exit(1)',body)
        self.assertIn('-- @profile begin fake',body)
        self.assertIn('-- @profile end fake',body)

    def test_reject_invalid_scopes_and_unbalanced_markers(self):
        for functions in [['end'],['x;printh(1)'],[42],['x','x'],['x']*17,'work']:
            with self.subTest(functions=functions),self.assertRaises(ValueError):
                profiling.instrument('',functions=functions)
        for source in ['-- @profile begin one','-- @profile end one',
                       '-- @profile begin one\n-- @profile end two','-- @profile wat']:
            with self.subTest(source=source),self.assertRaises(ValueError):
                profiling.instrument(source,sections=True)

    def test_nested_cpu_shares_use_self_time(self):
        def log(text):return dict(text='native: @@p8mcp_prof:7:'+text)
        report=profiling.decode_report([log('H:2:2:2:65536:32768:16384:32768:32768:0:0'),
            log('R:1:32768:16384:16384:2:0:0:0'),
            log('R:2:16384:16384:8192:2:0:0:0'),log('DONE')],7,
            [dict(name='parent',kind='function'),dict(name='child',kind='function')])
        self.assertEqual(report['callbacks']['totalCpu'],.75)
        self.assertEqual(report['unattributedCpu'],.25)
        self.assertAlmostEqual(sum(r['selfSharePercent'] for r in report['hotspots'])+
                               report['unattributedSharePercent'],100)
        with self.assertRaises(ValueError):profiling.decode_report([],7,[])

    def test_wall_rates_handle_counter_wrap(self):
        result=profiling.rates([dict(ms=0,updates=32760,draws=32765,vmFps=30),
                               dict(ms=1000,updates=22,draws=12,vmFps=15)])
        self.assertEqual(result['updatesPerSecond'],30)
        self.assertEqual(result['drawsPerSecond'],15)
        self.assertEqual(result['vmFps'],15)

    def test_failed_capture_disables_scope_collection(self):
        a=Adapter(REPO)
        a.loaded=dict(metadata=dict(profiling=True))
        with mock.patch.object(a,'pause'),mock.patch.object(a,'_command') as command,\
             mock.patch.object(a,'step',side_effect=AdapterError('FAIL','failed step')):
            with self.assertRaises(AdapterError):a.profile(frames=1)
        self.assertEqual([c.args[0] for c in command.call_args_list],[8,9])

class BundleTests(unittest.TestCase):
    def setUp(self):
        root=REPO/'debug/unit';root.mkdir(parents=True,exist_ok=True)
        temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.a=Adapter(self.root)
        (self.root/'main.lua').write_text('function _draw() cls() end',encoding='utf-8')
        (self.root/'data.p8').write_text('pico-8 cartridge\nversion 42\n__lua__\nfunction _draw() end\n__gfx__\n'+'0'*128+'\n',encoding='utf-8')

    def test_aliases_and_entry_last(self):
        specs=bundle_specs('nested/Main.p8.png',['assets/data.lua',{'cart':'elsewhere/data.p8','name':'DATA_2.p8'}])
        self.assertEqual([s['name'] for s in specs],['data.p8','data_2.p8','main.p8'])
        self.assertEqual([s['entry'] for s in specs],[False,False,True])

    def test_reject_collisions_and_console_paths(self):
        for args in [dict(entry='main.p8',carts=['main.p8.png']),
                     dict(entry='main.p8',carts=[{'cart':'a.p8','name':'../other.p8'}]),
                     dict(entry='main.p8',carts=[{'cart':'a.p8','name':'x.p8\nrun'}]),
                     dict(entry='main.p8',entry_name='@clip'),
                     dict(entry='main.p8',carts=[{'cart':'a.p8','name':'x.p8','unknown':1}]),
                     dict(entry='main.p8',carts=[None]),
                     dict(entry='main.p8',carts='data.p8'),
                     dict(entry='main.p8',carts=['a.p8']*64)]:
            with self.subTest(args=args),self.assertRaises(ValueError):bundle_specs(**args)

    def test_validation_before_vm_mutation(self):
        for deps in [['missing.p8'],['../escape.p8']]:
            with mock.patch.object(self.a,'_load_bytes') as load:
                with self.assertRaises(AdapterError):self.a.load_bundle('main.lua',deps)
                load.assert_not_called()

    def test_only_entry_is_instrumented(self):
        plan=self.a._plan_bundle('main.lua',['data.p8'],instrument=True,profiling=True)
        self.assertFalse(plan[0]['metadata']['instrumented'])
        self.assertTrue(plan[1]['metadata']['instrumented'])
        self.assertIn(b'bridge v2',plan[1]['payload'])
        self.assertNotIn(b'bridge v2',plan[0]['payload'])

    def test_stage_order_and_metadata(self):
        self.a.client=object()
        with mock.patch.object(self.a,'_stop_for_staging'),mock.patch.object(self.a,'_load_bytes') as load,mock.patch.object(self.a,'_save_staged_cart') as save:
            result=self.a.load_bundle('main.lua',['data.p8'],entry_name='entry.p8')
        self.assertEqual([call.args[1] for call in load.call_args_list],['data.p8','entry.p8'])
        self.assertEqual([call.args[0] for call in save.call_args_list],['data.p8','entry.p8'])
        self.assertEqual(result['stagedCount'],2)
        self.assertEqual(self.a.loaded['name'],'entry.p8')
        self.assertEqual(self.a.bundle['entry'],'entry.p8')

    def test_partial_failure_is_explicit(self):
        self.a.client=object()
        with mock.patch.object(self.a,'_stop_for_staging'),mock.patch.object(self.a,'_load_bytes'),mock.patch.object(self.a,'_save_staged_cart',side_effect=[None,AdapterError('FAIL','native save failed')]):
            with self.assertRaises(AdapterError) as caught:self.a.load_bundle('main.lua',['data.p8'])
        self.assertEqual(caught.exception.code,'BUNDLE_STAGE_FAILED')
        self.assertEqual(caught.exception.details['failedCart'],'main.p8')
        self.assertEqual(len(caught.exception.details['staged']),1)
        self.assertIsNone(self.a.bundle)
        self.assertIsNone(self.a.loaded)

    def test_missing_start_is_explicit(self):
        with self.assertRaises(AdapterError) as caught:self.a.load_bundle('main.lua')
        self.assertEqual(caught.exception.code,'NOT_STARTED')

    def test_png_drop_keeps_format_extension(self):
        (self.root/'image.p8.png').write_bytes(b'PNG fixture')
        self.a.client=object()
        with mock.patch.object(self.a,'_stop_for_staging'),mock.patch.object(self.a,'_load_bytes') as load,\
             mock.patch.object(self.a,'_save_staged_cart',return_value='pico-8 cartridge\nversion 42\n__lua__\n') as save:
            result=self.a.load_bundle('image.p8.png',entry_name='player.p8')
        self.assertEqual([call.args[1] for call in load.call_args_list],['player.p8.png','player.p8'])
        self.assertEqual(save.call_args.args[0],'player.p8')
        self.assertEqual(self.a.loaded['name'],'player.p8')
        self.assertIn(b'__gfx__\n',self.a.loaded['payload'])
        self.assertEqual(result['entry'],'player.p8')

class Zx0Tests(unittest.TestCase):
    def test_matches_upstream_c_vectors(self):
        from zx0_codec import compress,decompress
        for raw,encoded in [(b'a','d5615560'),(b'aaaa','9d615556'),
                            (b'abc'*100,'78616263fa440d5556'),(bytes(512),'950055755558')]:
            packed=bytes.fromhex(encoded)
            self.assertEqual(compress(raw),packed)
            self.assertEqual(decompress(packed,max_output=len(raw)),raw)
    def test_rejects_truncation_and_output_overflow(self):
        from zx0_codec import compress,decompress
        packed=compress(b'abc'*40)
        with self.assertRaises(ValueError):decompress(packed[:-1])
        with self.assertRaises(ValueError):decompress(packed,max_output=100)
        with self.assertRaises(ValueError):compress(b'')
    def test_verified_cache_avoids_recompression(self):
        import zx0_codec
        from source_build import compress_zx0
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(zx0_codec,'compress',wraps=zx0_codec.compress) as compressor:
                a=compress_zx0(b'example'*10,folder,'fixture')
                b=compress_zx0(b'example'*10,folder,'fixture')
            self.assertEqual(a,b)
            self.assertEqual(compressor.call_count,1)

class SourceBuildTests(unittest.TestCase):
    def test_includes_expand_and_reject_cycles(self):
        from source_build import read_source
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'main.lua').write_text('#include shared.lua\n')
            (root/'shared.lua').write_text('function answer() return 42 end\n')
            self.assertIn('return 42',read_source(root/'main.lua')[1])
            (root/'shared.lua').write_text('#include main.lua\n')
            with self.assertRaises(ValueError):read_source(root/'main.lua')
    def test_literal_assets_build_without_vm(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=Adapter(root)
            (root/'source.lua').write_text('asset_bytes={1,2,255}\n-- ACTUAL CARTRIDGE LUA CODE\nfunction _draw() cls(3) end\n')
            result=a.pack_source('source.lua')
            text=Path(result['out']).read_text()
            self.assertIn('1020ff',text)
            self.assertIn('function _draw()',text)
            self.assertNotIn('asset_bytes=',text)
            self.assertIsNone(a.client)
    def test_reject_expressions_and_invalid_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=Adapter(root)
            (root/'source.lua').write_text('asset_bytes={danger()}\n-- ACTUAL CARTRIDGE LUA CODE\n')
            with self.assertRaises(AdapterError) as error:a.pack_source('source.lua')
            self.assertEqual(error.exception.code,'INVALID_SOURCE')
            self.assertFalse((root/'build/source.p8').exists())
            with self.assertRaises(AdapterError):a.pack_source('source.lua',out='wrong.png')
    def test_source_paths_stay_within_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=Adapter(root)
            (root/'source.lua').write_text('-- ACTUAL CARTRIDGE LUA CODE\nfunction _draw() cls() end\n')
            with self.assertRaises(AdapterError) as error:a.pack_source('source.lua',out='../outside.p8')
            self.assertEqual(error.exception.code,'PATH_OUTSIDE_ROOT')

if __name__=='__main__':unittest.main()
