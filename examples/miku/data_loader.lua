-- Provenance: this demo's shared native CSTORE/LOAD preload.
-- https://www.lexaloffle.com/dl/docs/pico-8_manual.html
-- The including data cart sets data_cart_number to 1, 2 or 3.
function _init()
 local letter=chr(96+data_cart_number)
 cstore(0,0,0x4300,"miku_cache_"..letter..".p8")
 poke(0x4300,77,75,data_cart_number)
 if data_cart_number<3 then
  load("miku_"..chr(97+data_cart_number)..".p8")
 else load("miku.p8") end
end
function _draw()
 cls(1) print("miku / warming up",28,56,12)
 rectfill(28,70,28+data_cart_number*24,71,12)
end
