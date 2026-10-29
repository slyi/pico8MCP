"""Fresh setup, installed MCP server, real 3dfloor VM and launcher smoke test."""
import argparse
import base64
import ctypes
from contextlib import contextmanager
import hashlib
import json
import os
import platform
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from profiling_checks import run_profiler_checks
from multicart_checks import run_multicart_checks

REPO = Path(__file__).resolve().parents[1]


@contextmanager
def temporary_installation():
    temporary = tempfile.TemporaryDirectory(prefix='pico8mcp-ci-')
    folder = Path(temporary.name).resolve()
    assert folder.parent == Path(tempfile.gettempdir()).resolve()
    try:
        yield folder
    finally:
        # Windows can retain Chromium's profile lock briefly after exit.
        for attempt in range(40):
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.25)


def run(command, timeout=180, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding='utf-8', timeout=timeout, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{command[0]} exited {result.returncode}:\n{result.stdout}\n{result.stderr}')
    return result.stdout


class Client:
    def __init__(self, entrypoint, root, env, cache=None, platform="pico8"):
        self.platform=platform
        command=[sys.executable, '-B', str(entrypoint), '--root', str(root)]
        if platform!='pico8':command+=['--platform',platform]
        if cache:command+=['--cache',str(cache)]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env=env)
        self.responses = queue.Queue()
        self.ident = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self.responses.put(line)
        self.responses.put(None)

    def request(self, method, params=None):
        self.ident += 1
        self.process.stdin.write(json.dumps(dict(jsonrpc='2.0', id=self.ident,
                                                method=method, params=params or {}))+'\n')
        self.process.stdin.flush()
        line = self.responses.get(timeout=120)
        if line is None:
            raise RuntimeError('MCP exited: '+self.process.stderr.read())
        reply = json.loads(line)
        assert reply['id'] == self.ident, reply
        assert 'error' not in reply, reply
        return reply['result']

    def tool(self, tool_name, **arguments):
        result = self.request('tools/call', dict(name=self.platform+'_'+tool_name, arguments=arguments))
        assert not result.get('isError'), result
        value = result['structuredContent']
        if tool_name == 'screenshot':
            value['data'] = next(item['data'] for item in result['content'] if item['type'] == 'image')
        return value

    def close(self):
        try:
            if self.process.poll() is None:
                self.process.stdin.close()
                self.process.wait(timeout=20)
            assert self.process.returncode == 0, self.process.stderr.read()
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=10)


def png_pixels(data, expected_size=(128, 128)):
    """Decode browser RGB/RGBA PNGs to reject a blank or broken framebuffer."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    width, height, depth, color = struct.unpack('>IIBB', data[16:26])
    assert (width, height) == expected_size
    assert depth == 8
    assert color in (2, 6), f'Unexpected canvas PNG color type {color}'
    channels = 4 if color == 6 else 3
    compressed = bytearray()
    pos = 8
    while pos < len(data):
        size = struct.unpack('>I', data[pos:pos+4])[0]
        if data[pos+4:pos+8] == b'IDAT':
            compressed.extend(data[pos+8:pos+8+size])
        pos += size+12
    raw = zlib.decompress(compressed)
    stride = width*channels
    previous = bytearray(stride)
    pixels = []
    for y in range(height):
        offset = y*(stride+1)
        kind = raw[offset]
        row = bytearray(raw[offset+1:offset+1+stride])
        assert kind in range(5)
        for x in range(stride):
            left = row[x-channels] if x >= channels else 0
            up = previous[x]
            upper_left = previous[x-channels] if x >= channels else 0
            prediction = left+up-upper_left
            paeth = min((left, up, upper_left), key=lambda v: abs(prediction-v))
            predictor = (0, left, up, (left+up)//2, paeth)[kind]
            row[x] = (row[x]+predictor) & 255
        pixels.append([tuple(row[x:x+3]) for x in range(0, stride, channels)])
        previous = row
    return pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect-non-admin', action='store_true')
    parser.add_argument('--expect-arch', choices=['x64', 'arm64'])
    parser.add_argument('--out-dir', type=Path, default=REPO/'debug/ci')
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    machine = platform.machine().lower()
    arch = 'arm64' if machine in ('arm64', 'aarch64') else 'x64' if machine in ('x86_64', 'amd64') else machine
    admin = bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == 'nt' else os.geteuid() == 0
    report = dict(ok=False, platform=sys.platform, architecture=arch,
                  python=platform.python_version(), administrator=admin)
    try:
        if args.expect_non_admin:
            assert not admin, 'Tests must execute without administrator/root privileges'
        if args.expect_arch:
            assert arch == args.expect_arch, (arch, args.expect_arch)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        with temporary_installation() as folder:
            env['PICO8_MCP_HOME'] = str(Path(folder)/'common')
            env.pop('PICO8_MCP_PROFILE_DIR', None)
            run([sys.executable, '-B', str(REPO/'tests/test_adapter.py')], env=env, cwd=REPO)
            run([sys.executable, '-B', str(REPO/'tests/test_zx0_accel.py')], env=env, cwd=REPO)
            print('Offline regression suite passed.', flush=True)
            example = REPO/'examples/3dfloor'
            setup_command = [sys.executable, '-B', str(REPO/'pico8_mcp.py'),
                             '--root', str(example), '--setup']
            first = json.loads(run(setup_command, env=env))
            assert first['cached'] is False
            common = Path(first['installation']['home'])
            runtime = common/'runtime/runtime.js'
            assert hashlib.sha256(runtime.read_bytes()).hexdigest() == first['sha256']
            assert not (common/'profiles').exists(), 'Setup must not launch a browser'
            assert json.loads(run(setup_command, env=env))['cached'] is True
            report.update(freshSetup=True, cachedSetup=True, runtimeSha256=first['sha256'])
            print('Fresh and cached official runtime setup passed.', flush=True)
            client = Client(common/'pico8_mcp.py', example, env)
            try:
                init = client.request('initialize', dict(protocolVersion='2025-06-18', capabilities={},
                                                         clientInfo=dict(name='ci', version='1')))
                assert init['serverInfo']['name'] == 'pico8-education-mcp'
                help_resource = client.request('resources/read', dict(uri='pico8://help'))
                assert help_resource['contents'][0]['mimeType'] == 'text/markdown'
                doctor = client.tool('doctor')
                assert doctor['runtimeIntegrity'] and doctor['browser'], doctor
                report['browser'] = doctor['browser']
                client.tool('start', headless=True, timeout_ms=60000)
                client.tool('load', cart='floor.lua', instrument=True, count_draws=False)
                client.tool('run', paused=True, timeout_ms=30000)
                screenshots = []
                for yaw in (0, 45):
                    client.tool('set', name='yaw', value=yaw)
                    client.tool('step', frames=3, buttons=0)
                    assert client.tool('get', name='yaw')['value'] == yaw
                    shot = client.tool('screenshot', out=f'debug/ci/yaw-{yaw}.png')
                    data = base64.b64decode(shot['data'])
                    pixels = png_pixels(data)
                    ground = [p for row in pixels[76:124] for p in row[4:124]]
                    assert sum(max(p) < 20 for p in ground) > len(ground)*0.15, 'Missing black floor tiles'
                    assert sum(min(p) > 200 for p in ground) > len(ground)*0.15, 'Missing white floor tiles'
                    (out/f'yaw-{yaw}.png').write_bytes(data)
                    screenshots.append(hashlib.sha256(data).hexdigest())
                assert screenshots[0] != screenshots[1], 'Yaw did not change rendered floor'
                before = client.tool('get', name='cam_u')['value']
                # O + right is the example's controller-only strafe input.
                # Up/down control pitch; WASD uses native keyboard scancodes.
                client.tool('step', frames=12, buttons=18)
                after = client.tool('get', name='cam_u')['value']
                assert before != after, 'Controller strafe did not update the camera'
                profiling=run_profiler_checks(client)
                report['profiling']={name:value['mode'] for name,value in profiling.items()}
                report['multicart']=run_multicart_checks(client,example)
                built = client.tool('build', cart='floor.lua', out='debug/ci/floor.p8.png', label_frames=6)
                assert built['verified'] and built['labelCaptured'] and not built['instrumented'], built
                cartridge = Path(built['path']).read_bytes()
                size=client.tool('cart_size',cart='debug/ci/floor.p8.png')
                assert size['romPayloadBytes']==32768 and size['compressedLuaBytes']>0,size
                report['cartSizeInspector']=True
                (out/'floor.p8.png').write_bytes(cartridge)
                label = png_pixels(cartridge, expected_size=(160, 205))
                label_ground = [p for row in label[100:148] for p in row[20:140]]
                assert sum(max(p) < 20 for p in label_ground) > len(label_ground)*0.15, 'Label is missing black floor tiles'
                assert sum(min(p) > 200 for p in label_ground) > len(label_ground)*0.15, 'Label is blank or missing white floor tiles'
                report.update(nativePngBuild=True, nativeLabel=True, pngReload=True)
                closed = client.tool('close')
                assert closed['profileCleanup'] == 'preserved', closed
                report.update(mcpStdio=True, run3dfloor=True, screenshots=[0, 45], controllerStrafe=True)
            finally:
                client.close()
            launcher = (['cmd.exe', '/d', '/c', str(example/'play.cmd')] if os.name == 'nt'
                        else ['sh', str(example/'play.sh')])
            output = run([*launcher, '--headless', '--duration', '2'], env=env, cwd=example)
            assert '30 FPS' in output, output
            report.update(launcher=True, nativeFps=30)
            print('3dfloor run, yaw screenshots, movement and native launcher passed.', flush=True)
            multicart = REPO/'examples/miku'
            launcher = (['cmd.exe', '/d', '/c', str(multicart/'play.cmd')] if os.name == 'nt'
                        else ['sh', str(multicart/'play.sh')])
            print('Building/running Miku; cold Python compression can take several minutes.', flush=True)
            output = run([*launcher, '--headless', '--duration', '8'], env=env, cwd=multicart, timeout=1800)
            assert 'Staged 4 carts with pico8mcp load_bundle.' in output, output
            assert 'All 15 motion checksums match the Lua build.' in output, output
            assert 'Headless playback verified; zero motion underflows.' in output, output
            preview=multicart/'build/carts/preview.png'
            assert preview.is_file(), 'Missing Miku playback screenshot'
            (out/'miku.png').write_bytes(preview.read_bytes())
            report['multicartLauncher'] = True
            report['miku'] = dict(carts=4,allMotionChecksums=True,underflows=0)
            print('Miku multicart launcher and motion checksums passed.', flush=True)
        report['ok'] = True
    finally:
        (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
