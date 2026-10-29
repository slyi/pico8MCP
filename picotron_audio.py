"""Translate existing PICO-8 tracker words to a native PFX6416 bank.

Preserves note/rest order, pitch, volume, voice, and tracker speed. Waveform
and effect IDs select equivalent native synth operations; it is not PCM.
Layout provenance: official picotron_synth.html and pancelor's corrected
2024 sfx/music data layout (CC BY 4.0):
https://www.lexaloffle.com/bbs/?tid=143443
The older working synth document describes an earlier header/pattern layout.
"""
import struct

EFFECTS=(0,ord('g'),ord('v'),ord('-'),ord('<'),ord('>'),ord('a'),ord('b'))

def convert(music):
    bank=bytearray(0x40000)
    # Tick size in 1/16 of a 44.1 kHz sample. Closest integer to 128 Hz.
    rows=len(music['notes'])
    struct.pack_into('<4H',bank,0,8,64,16,1)
    # Eight reserved bytes follow the counts; addresses begin at 0x10.
    struct.pack_into('<4I',bank,0x10,0x10000,0x20000,0,0)
    struct.pack_into('<HHHBBBB',bank,0x20,5512,32,music['speed'],0,0,0,0)
    for pattern,row in enumerate(music['patterns']):
        address=0x100+pattern*20
        bank[address:address+8]=bytes(row[:4]+[255]*4)
        # Current native flow bits match the PICO-8 source: loop0, loop1, stop.
        bank[address+8]=row[4]
        bank[address+9]=15
        struct.pack_into('<H',bank,address+10,32)
    # Standard mixer + oscillator nodes. Four used pitched waves use WT-0;
    # the existing noise voice uses WT-1. PICO-8's C0 is native C2 (+24).
    waves={0:64,1:96,2:128,3:192,4:224,5:32,6:0,7:208}
    for instrument in range(8):
        address=0x10000+instrument*512
        bank[address+1]=1
        bank[address+33]=0x12 if instrument==6 else 2
        for node in (address,address+32):
            bank[node+4:node+8]=bytes((2,32,0,0))
            bank[node+8:node+12]=bytes((1,0,0,0))
            bank[node+12:node+16]=bytes((1,0,0,0))
            bank[node+16:node+20]=bytes((1,0,0,0))
        bank[address+52:address+56]=bytes((0,waves[instrument],0,0))
        # Explicit waveform descriptors are required. A zero address reads
        # silent legacy RAM; it does not select Picotron's default waves.
        # Standard descriptors verified against the runtime's own boot bank:
        # WT-0: 256 x 1024-sample waves; WT-1: two 8192-sample noise waves.
        struct.pack_into('<HBB',bank,address+480,0xf800,10,255)
        struct.pack_into('<HBB',bank,address+484,0xf780,13,1)
        name=f'P8 wave {instrument}'.encode();bank[address+496:address+496+len(name)]=name
    for row_index,row in enumerate(music['notes']):
        for channel,word in enumerate(row):
            track=row_index//32*4+channel
            address=0x20000+track*328
            struct.pack_into('<hBBBBBB',bank,address,32,music['speed'],0,0,0,0,0)
            if row_index%32==0:bank[address+8:address+328]=b'\xff'*320
            index=row_index%32
            pitch=word&63;wave=(word>>6)&7;volume=(word>>9)&7;effect=(word>>12)&7
            for column,value in enumerate((pitch+24,wave,round(volume*64/7),EFFECTS[effect],0)):
                # Current synth columns use the declared 32-row track length;
                # default track slots remain 328 bytes apart.
                bank[address+8+column*32+index]=value
            # Check that the unchanged note data round-trips from native form.
            decoded=(bank[address+8+index]-24)|bank[address+40+index]<<6|round(bank[address+72+index]*7/64)<<9|EFFECTS.index(bank[address+104+index])<<12
            assert decoded==word,(row_index,channel,decoded,word)
    return bytes(bank)
