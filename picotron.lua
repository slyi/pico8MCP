-- Reusable Picotron adapters for projects sharing Lua with PICO-8.
-- No overrides of native peek/poke or graphics APIs; PICO-8 RAM addresses
-- must not be interpreted as Picotron addresses.
-- API/layout provenance: official Picotron manual / PFX6416 documentation.
-- https://www.lexaloffle.com/dl/docs/picotron_manual.html
-- https://www.lexaloffle.com/dl/docs/picotron_synth.html
picotron={}

function picotron.viewport(width,height,focal,title)
 window{title=title,fullscreen=true,cursor=0}
 return {width=width,height=height,cx=width/2,cy=height/2,focal=focal,scale=focal/188}
end

function picotron.material_ramps(base,levels,first)
 local ramps={}
 for material=1,#base do
  local colors={} local rgb=base[material]
  for level=0,levels-1 do
   local scale=.25+.75*level/(levels-1)
   local color=flr(rgb[1]*scale)*65536+flr(rgb[2]*scale)*256+flr(rgb[3]*scale)
   local index=first+(material-1)*levels+level
   pal(index,color,2) colors[level+1]=index
  end
  ramps[material]=colors
 end
 return ramps
end

-- Time is read from the synth, not counted from rendered frames.
-- source_hz / speed describe the existing PICO-8 tracker data.
function picotron.music_clock(rows,speed,source_hz)
 local started=false local last=0
 return function()
  local pattern=stat(466)
  if pattern>=0 then
   started=true
   last=max(last,(pattern*rows*speed+stat(400,11))/source_hz)
  elseif started then last=60 end
  return min(60,last)
 end
end

-- Maximum 30 rendered frames/sec. Native foot.lua performs the final flip;
-- when the OS operates at 60 Hz this additional ordinary flip holds the
-- completed image for another display interval. At 30 Hz no extra wait.
-- Sound continues in the native synthesizer during both intervals.
function picotron.limit_30()
 -- A draw already spanning two OS frames needs no additional hold.
 if stat(7)==60 and stat(1)<.85 then flip() end
end
