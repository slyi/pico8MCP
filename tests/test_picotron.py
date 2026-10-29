"""Offline P64/POD/protocol checks; native playback is covered by picotron_ci.py."""
import os,struct,subprocess,sys,tempfile,unittest
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from picotron_cart import pack_rom,unpack_rom,lz4_compress,lz4_decompress,userdata_pod
from picotron_audio import convert
from source_build import read_source

class PicotronTests(unittest.TestCase):
    def test_lz4_known_literals_and_overlap(self):
        self.assertEqual(lz4_decompress(b'\x30abc'),b'abc')
        self.assertEqual(lz4_decompress(b'\x15a\x01\x00'),b'a'*10)
        for raw in (b'',b'12345',b'abcd'*4000,bytes(range(256))*400):
            self.assertEqual(lz4_decompress(lz4_compress(raw)),raw)
    def test_malformed_and_bounded_lz4(self):
        for raw in (b'\xf0',b'\x00\x00\x00',b'\x30ab',b'\x00\x02\x00'):
            with self.assertRaises(ValueError):lz4_decompress(raw)
        with self.assertRaises(ValueError):lz4_decompress(b'\x15a\x01\x00',limit=9)
    def test_rom_assets_and_path_boundaries(self):
        files={'main.lua':b'function _draw() cls() end','sfx/':None,'sfx/0.sfx':b'\0'*1024}
        self.assertEqual(unpack_rom(pack_rom(files)),files)
        for name in ('../outside','/absolute','C:/outside','a\\b','a/../b'):
            with self.assertRaises(ValueError):pack_rom({'main.lua':b'',name:b'x'})
        with self.assertRaises(ValueError):unpack_rom(pack_rom(files)[:-1])
        with self.assertRaises(ValueError):pack_rom({'main.lua':os.urandom(300000)})
    def test_userdata_known_binary_layout(self):
        data=userdata_pod('i16',2,[-1,0,42,32767],2)[9:]
        if data.startswith(b'lz4\0'):data=lz4_decompress(data[12:])
        self.assertEqual(data[:4],b'pxu\0')
        self.assertEqual(data, b'pxu\0'+struct.pack('<HII',0x884c,2,2)+bytes.fromhex('00 00ffff 000000 002a00 00ff7f'))
        with self.assertRaises(ValueError):userdata_pod('i16',2,[0])
    def test_real_music_native_instrument_and_wave_descriptors(self):
        music=read_source(REPO/'examples/miku/miku.lua')[0]['music_data']
        bank=convert(music)
        # Existing row 2/channel 0 is MIDI 61: native pitch 49, pulse.
        self.assertEqual(bank[0x20000+8+2],49)
        self.assertEqual(bank[0x20000+40+2],4)
        self.assertEqual(bank[0x10000+480:0x10000+484],bytes.fromhex('00f80aff'))
        self.assertEqual(bank[0x10000+484:0x10000+488],bytes.fromhex('80f70d01'))
        self.assertEqual(bank[0x100+15*20+8],4)
    def test_cli_platform_and_unified_targets(self):
        for command in ([sys.executable,str(REPO/'pico8_mcp.py'),'--help'],
                        [sys.executable,str(REPO/'play.py'),'--help'],
                        [sys.executable,str(REPO/'examples/miku/play.py'),'--help']):
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('picotron',result.stdout)

if __name__=='__main__':unittest.main()
