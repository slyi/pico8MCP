"""Run installed multicart checks, optionally including an external Miku demo."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from ci import Client
from installation import install_common
from multicart_checks import run_multicart_checks


def check_miku(common,root,env,cache,png=False):
    root=Path(root).resolve();sub='compact/' if png else ''
    suffix='.p8.png' if png else '.p8'
    files=[root/(sub+'miku'+suffix)]+[root/(sub+f'miku_{letter}'+suffix) for letter in 'abc']
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    client=Client(common/'pico8_mcp.py',root,env,cache)
    try:
        client.tool('start',headless=True,timeout_ms=60000)
        bundle=client.tool('load_bundle',entry=sub+'miku'+suffix,carts=[sub+f'miku_{letter}'+suffix for letter in 'abc'])
        client.tool('run',timeout_ms=60000)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            state=client.tool('state');g=state['gpio']
            assert not state['errors'],state['errors']
            if g[:3]==[77,75,1] and g[8]>=8:break
            time.sleep(.1)
        else:raise AssertionError('Miku did not finish its preload and playback check')
        expected=json.loads((root/'build-report.json').read_text(encoding='utf-8'))['motion_checksums']
        assert g[34:64]==[v for pair in expected for v in pair],g[34:64]
        assert g[6]==0,g[:12]
        shot=client.tool('screenshot',out='debug/multicart-api-'+('png' if png else 'text')+'.png')
        assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        return dict(staged=bundle['stagedCount'],nativeNames=[p['name'] for p in bundle['carts']],
                    allMotionChecksums=True,underflows=g[6],animationSeconds=g[8],
                    sourceFilesUnchanged=True,screenshot=shot['path'])
    finally:
        try:client.tool('close')
        finally:client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',type=Path)
    p.add_argument('--miku',type=Path)
    p.add_argument('--miku-only',action='store_true')
    args=p.parse_args()
    folder=REPO/'debug/multicart-native';folder.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,PICO8_MCP_HOME=str(folder/'common'))
    with patch.dict(os.environ,env):installed=install_common()
    common=Path(installed['home']);report={}
    if not args.miku_only:
        client=Client(common/'pico8_mcp.py',folder,env,args.cache)
        try:
            client.tool('start',headless=True,timeout_ms=60000)
            print('Testing installed multicart MCP tools.',flush=True)
            report['synthetic']=run_multicart_checks(client,folder)
        finally:
            try:client.tool('close')
            finally:client.close()
    if args.miku:
        print('Testing Miku text bundle.',flush=True)
        report['mikuText']=check_miku(common,args.miku,env,args.cache)
        print('Testing Miku PNG bundle.',flush=True)
        report['mikuPng']=check_miku(common,args.miku,env,args.cache,True)
    (folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
