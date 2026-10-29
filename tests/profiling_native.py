"""Run the installed MCP profiler checks with an optional existing runtime cache."""
import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from ci import Client
from installation import install_common
from profiling_checks import run_profiler_checks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path)
    args=parser.parse_args()
    folder=REPO/'debug/profiling-native'
    folder.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,PICO8_MCP_HOME=str(folder/'common'))
    with patch.dict(os.environ,env):installation=install_common()
    client=Client(installation['entrypoint'],folder,env,args.cache)
    try:
        client.tool('start',headless=True,timeout_ms=60000)
        results=run_profiler_checks(client)
        (folder/'report.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        print(json.dumps({name:(value['rates'] if 'rates' in value else value['detail']['nativeFrameCpu'])
                          for name,value in results.items()},indent=2),flush=True)
    finally:
        try:client.tool('close')
        finally:client.close()


if __name__=='__main__':main()
