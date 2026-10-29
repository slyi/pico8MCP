"""Standard ZX0 v2 encoder/decoder with optional Python-package acceleration.

Provenance: direct translation of Einar Saukas' optimize.c, compress.c and
dzx0.c from https://github.com/einar-saukas/ZX0 (BSD-3-Clause, v2.2).
The optimal parser and tie-breaking follow the upstream C implementation.
Only forward, modern-v2 streams are supported. First builds can be slow;
source_build.compress_zx0 caches verified results.

(c) Copyright 2021 by Einar Saukas. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
 * Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
 * The name of its author may not be used to endorse or promote products
   derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


def _optimal(data,offset_limit,progress=None):
    size=len(data);ceiling=max(1,min(size-1,offset_limit))
    literals=[None]*(ceiling+1);matches=[None]*(ceiling+1)
    optimal=[None]*size;lengths=[0]*(ceiling+1);best=[0]*max(3,size)
    best[2]=2
    # Blocks are (bit cost, end index, match offset or 0, preceding block).
    matches[1]=(-1,-1,1,None)
    gamma=[0]+[2*n.bit_length()-1 for n in range(1,size+1)]
    offset_cost=[0]+[8+2*((n-1)//128+1).bit_length()-1 for n in range(1,ceiling+1)]
    for index,current in enumerate(data):
        best_size=2;winner=None
        for offset in range(1,max(1,min(index,offset_limit))+1):
            if index and index>=offset and current==data[index-offset]:
                literal=literals[offset]
                if literal is not None:
                    length=index-literal[1];bits=literal[0]+1+gamma[length]
                    block=(bits,index,offset,literal);matches[offset]=block
                    if winner is None or winner[0]>bits:winner=block
                length=lengths[offset]+1;lengths[offset]=length
                if length>1:
                    if best_size<length:
                        chosen=best[best_size]
                        bits=optimal[index-chosen][0]+gamma[chosen-1]
                        while best_size<length:
                            best_size+=1
                            candidate=optimal[index-best_size][0]+gamma[best_size-1]
                            if candidate<=bits:best[best_size]=best_size;bits=candidate
                            else:best[best_size]=best[best_size-1]
                    chosen=best[length];previous=optimal[index-chosen]
                    bits=previous[0]+offset_cost[offset]+gamma[chosen-1]
                    match=matches[offset]
                    if match is None or match[1]!=index or match[0]>bits:
                        block=(bits,index,offset,previous);matches[offset]=block
                        if winner is None or winner[0]>bits:winner=block
            else:
                lengths[offset]=0;match=matches[offset]
                if match is not None:
                    length=index-match[1];bits=match[0]+1+gamma[length]+length*8
                    block=(bits,index,0,match);literals[offset]=block
                    if winner is None or winner[0]>bits:winner=block
        optimal[index]=winner
        if progress is not None and (index%256==0 or index==size-1):progress(index+1,size)
    return optimal[-1]


def compress(data,offset_limit=32640,progress=None,backend="auto"):
    """Return an optimal ZX0 v2 stream; optional NumPy/Numba, stdlib fallback."""
    data=bytes(data)
    if not data:raise ValueError('ZX0 requires non-empty input')
    if not 1<=offset_limit<=32640:raise ValueError('ZX0 offset limit must be 1..32640')
    if backend not in ("auto","python","numpy","numba"):raise ValueError("ZX0 backend must be auto, python, numpy, or numba")
    # Blocks form an acyclic DAG. Refcounts release them; cyclic-GC scans
    # during the allocation-heavy optimal parse add substantial overhead.
    import gc
    enabled=gc.isenabled()
    if enabled:gc.disable()
    try:
        if backend=="python":block=_optimal(data,offset_limit,progress)
        else:
            from zx0_accel import optimal
            block=optimal(data,offset_limit,progress,backend,_optimal)
    finally:
        if enabled:gc.enable()
    path=[]
    while block[1]>=0:path.append(block);block=block[3]
    path.reverse();out=bytearray();mask=0;bit_index=0;backtrack=True
    def bit(value):
        nonlocal mask,bit_index,backtrack
        if backtrack:
            if value:out[-1]|=1
            backtrack=False
        else:
            if not mask:mask=128;bit_index=len(out);out.append(0)
            if value:out[bit_index]|=mask
            mask>>=1
    def gamma(value,inverted=False):
        for shift in range(value.bit_length()-2,-1,-1):
            bit(0);bit(((value>>shift)&1)^inverted)
        bit(1)
    previous=-1;position=0;last_offset=1
    for _,index,offset,_ in path:
        length=index-previous
        if not offset:
            bit(0);gamma(length);out.extend(data[position:position+length])
        elif offset==last_offset:
            bit(0);gamma(length)
        else:
            bit(1);gamma((offset-1)//128+1,True)
            out.append((127-(offset-1)%128)<<1);backtrack=True
            gamma(length-1);last_offset=offset
        position+=length;previous=index
    bit(1);gamma(256,True)
    return bytes(out)


def decompress(data,max_output=16*1024*1024):
    """Decode forward ZX0 v2 with bounds checks and a caller-selected limit."""
    data=bytes(data);position=0;mask=0;bit_value=0;last_byte=0;backtrack=False
    out=bytearray();offset=1;state='literal'
    def byte():
        nonlocal position,last_byte
        if position>=len(data):raise ValueError('Truncated ZX0 stream')
        last_byte=data[position];position+=1;return last_byte
    def bit():
        nonlocal mask,bit_value,backtrack
        if backtrack:backtrack=False;return last_byte&1
        if not mask:mask=128;bit_value=byte()
        value=int(bool(bit_value&mask));mask>>=1;return value
    def gamma(inverted=False):
        value=1
        while not bit():
            value=(value<<1)|(bit()^inverted)
            if value>max(max_output,32640)+256:raise ValueError('ZX0 length exceeds bound')
        return value
    def copy(length):
        if offset>len(out) or len(out)+length>max_output:raise ValueError('Invalid ZX0 match or output limit')
        for _ in range(length):out.append(out[-offset])
    while True:
        if state=='literal':
            length=gamma()
            if len(out)+length>max_output:raise ValueError('ZX0 output limit exceeded')
            for _ in range(length):out.append(byte())
            state='new' if bit() else 'repeat'
        elif state=='repeat':
            copy(gamma());state='new' if bit() else 'literal'
        else:
            msb=gamma(True)
            if msb==256:
                if position!=len(data):raise ValueError('Trailing bytes after ZX0 end marker')
                return bytes(out)
            offset=msb*128-(byte()>>1);backtrack=True
            copy(gamma()+1);state='new' if bit() else 'literal'
