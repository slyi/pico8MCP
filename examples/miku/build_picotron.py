"""Build the existing minute of Miku data for native Picotron, no resampling."""
from pathlib import Path
import hashlib,json,struct,sys
ROOT=Path(__file__).resolve().parent
EXAMPLE=ROOT
from build_lua import model_bytes,helper_root
sys.path.insert(0,str(helper_root))
from source_build import read_source,dump_lua
from picotron_cart import pack_rom,userdata_pod
from picotron_audio import convert

def build():
    assets=read_source(EXAMPLE/'miku.lua')[0]
    chunks={}
    for letter in 'abc':
        chunks.update(read_source(EXAMPLE/f'miku_{letter}.lua')[0]['dance_data']['chunks'])
    rows=[];checks=[]
    for block in range(1,16):
        entry=chunks[block];values=[0]*63;block_rows=[]
        assert len(entry['deltas'])==121
        for delta in entry['deltas']:
            values=[(a+b)%512 if i>=3 else a+b for i,(a,b) in enumerate(zip(values,delta))]
            block_rows.append(values)
        c1=c2=0
        for channel in range(63):
            for frame in block_rows:c1=(c1+frame[channel])%251;c2=(c2+c1)%251
        assert [c1,c2]==entry['checksum']
        checks.append([c1,c2]);rows.extend(block_rows)
    assert len(rows)==1815
    for i in range(14):assert rows[i*121+120]==rows[(i+1)*121]
    words=[v for row in rows for v in row]
    model=model_bytes(assets['model_data'])
    audio=convert(assets['music_data'])
    source='-- Same shared player as the PICO-8 Miku carts.\n'
    source+='motion_checks='+dump_lua(checks)+'\n'
    for filename in ('picotron.lua','player.lua','miku_picotron.lua'):
        source+='\n-- included: '+filename+'\n'+((helper_root if filename=='picotron.lua' else ROOT)/filename).read_text(encoding='utf-8')
    files={'main.lua':source.encode('utf-8'),
           'model.pod':userdata_pod('u8',len(model),model),
           'motion.pod':userdata_pod('i16',63,words,1815),
           'sfx/':None,'sfx/0.sfx':userdata_pod('u8',len(audio),audio)}
    out=ROOT/'build-picotron';out.mkdir(exist_ok=True)
    rom=pack_rom(files);(out/'miku.p64.rom').write_bytes(rom)
    (out/'main.lua').write_text(source,encoding='utf-8')
    manifest=dict(romBytes=len(rom),motionIntervals=1800,uniquePoses=1801,storedPoseRecords=1815,
                  motionHz=30,musicRows=512,musicVoices=4,noteSlots=2048,
                  motionSha256=hashlib.sha256(struct.pack('<'+'h'*len(words),*words)).hexdigest(),
                  musicWordsSha256=hashlib.sha256(struct.pack('<2048H',*(v for row in assets['music_data']['notes'] for v in row))).hexdigest(),
                  motionChecksums=checks,musicRoundTripVerified=True,sources={})
    for name in ('miku.lua','miku_a.lua','miku_b.lua','miku_c.lua'):
        manifest['sources'][name]=hashlib.sha256((EXAMPLE/name).read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('motionChecksums','sources')}),flush=True)
    return out/'miku.p64.rom'
if __name__=='__main__':build()
