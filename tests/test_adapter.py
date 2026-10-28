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
        self.assertIn(b'PICO-8 MCP bridge v1',payload)
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

if __name__=='__main__':unittest.main()
