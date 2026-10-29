-- Provenance: ZX0 PICO-8 decoder by Nat Pryce, BSD-3-Clause.
-- Upstream: https://github.com/npryce/ZX0-Pico-8
-- Format/compressor: Einar Saukas, https://github.com/einar-saukas/ZX0
-- The decoder below is included from upstream; retain its license notice.
--[[
BSD 3-Clause License

Copyright (c) 2026, Nat Pryce

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

]]


function zx0_decompress(get_in, get_out, set_out)
	local offset, shift, input_count, output_count, last_byte, backtrack, temp, v = 1, 0, -1, 0

	local function read_byte()
		input_count += 1
		return get_in(input_count)
	end

	local function read_bit()
		if backtrack then
			temp, backtrack = backtrack & 1
			return temp
		end
		if (shift == 0) last_byte, shift = read_byte(), 8
		shift -= 1
		return last_byte >> shift & 1
	end

	local function read_var(invert)
		v = 1
		while (read_bit() == 0) v <<= 1 v |= read_bit() ^^ invert
		return v
	end

	::copy_literals::
	for _ = 1, read_var"0" do set_out(output_count, read_byte()) output_count += 1 end
	if (read_bit() == 0) read_var"0" goto copy_from_last_offset

	::copy_from_new_offset::
	if (read_var"1" == 256) return
	backtrack = read_byte()
	offset, v = v * 128 - backtrack \ 2, read_var"0" + 1

	::copy_from_last_offset::
	for _ = 1, v do set_out(output_count, get_out(output_count - offset)) output_count += 1 end

	::loop::
	if (read_bit() == 0) goto copy_literals
	goto copy_from_new_offset
end
