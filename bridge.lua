-- PICO-8 MCP bridge v2. Appended only to the in-memory development cart.
-- Reserves GPIO 64..127; GPIO 0..63 remains available to the cartridge.
-- The user callbacks and framebuffer are frozen while simulation is paused.
local p8m={base=0x5fc0,paused=false,left=0,frame=0,seq=0,done=0,
 mask=0,previous=0,controlled=false,holds={},draw=false,cpu=0,draws=0}
local p8m_init,p8m_draw,p8m_u60,p8m_u=_init,_draw,_update60,_update
local p8m_btn,p8m_btnp=btn,btnp
local p8m_rate=p8m_u60 and 60 or 30

local function p8m_word(offset,value)
 poke(p8m.base+offset,value%256)
 poke(p8m.base+offset+1,flr(value/256))
end

local function p8m_percent(offset,value)
 p8m_word(offset,flr(mid(0,value,3)*10000))
end

local function p8m_publish()
 poke(p8m.base+6,p8m.paused and 1 or 0)
 p8m_word(8,p8m.frame)
 p8m_word(28,p8m.draws)
 poke(p8m.base+30,p8m_rate)
 p8m_word(22,p8m.left)
 poke(p8m.base+12,stat(7))
 poke(p8m.base+24,p8m.done)
end

local function p8m_variable(write)
 local name=""
 for i=32,55 do
  local ch=peek(p8m.base+i)
  if(ch==0) break
  name=name..chr(ch)
 end
 local export=p8mcp_exports and p8mcp_exports[name]
 local parent,key,value
 if export then
  if type(export)!="table" or type(export.get)!="function" then poke(p8m.base+15,2) return end
  value=export.get()
 else
  local parts=split(name,".",false)
  parent=_ENV
  for i=1,#parts-1 do
   if type(parent)!="table" then poke(p8m.base+15,3) return end
   parent=parent[parts[i]]
  end
  if type(parent)!="table" or #parts==0 then poke(p8m.base+15,3) return end
  key=parts[#parts]
  value=parent[key]
 end
 if write then
  if value==nil and peek(p8m.base+31)==0 then poke(p8m.base+15,1) return end
  if value!=nil and type(value)!="number" and type(value)!="boolean" then poke(p8m.base+15,2) return end
  if peek(p8m.base+60)==1 then value=peek4(p8m.base+56)!=0
  else value=peek4(p8m.base+56) end
  if export then
   if type(export.set)!="function" then poke(p8m.base+15,2) return end
   export.set(value) value=export.get()
  else parent[key]=value end
 end
 if type(value)=="number" then
  poke4(p8m.base+56,value) poke(p8m.base+60,0)
 elseif type(value)=="boolean" then
  poke4(p8m.base+56,value and 1 or 0) poke(p8m.base+60,1)
 elseif value==nil then
  poke4(p8m.base+56,0) poke(p8m.base+60,2)
 else
  poke(p8m.base+15,2)
 end
end

local function p8m_commands()
 local seq=peek(p8m.base+17)
 if seq!=p8m.seq then
  p8m.seq=seq
  local cmd=peek(p8m.base+16)
  poke(p8m.base+15,0)
  if cmd==1 then p8m.paused=true p8m.left=0 end
  if cmd==2 then p8m.paused=false p8m.left=0 end
  if cmd==3 then
   p8m.paused=true
   p8m.left=peek(p8m.base+18)+256*peek(p8m.base+19)
  end
  if cmd==3 or cmd==4 then
   p8m.mask=peek(p8m.base+20)&63
   p8m.controlled=peek(p8m.base+21)==1
  end
  if cmd==5 then p8m_variable(false) end
  if cmd==6 then p8m_variable(true) end
  if cmd==7 or cmd==8 or cmd==9 then
   if __p8mcp_prof then
    if cmd==7 then __p8mcp_prof.report(seq)
    elseif cmd==8 then __p8mcp_prof.reset()
    else __p8mcp_prof.cancel() end
   else poke(p8m.base+15,4) end
  end
  if cmd!=3 or p8m.left==0 then p8m.done=seq end
  poke(p8m.base+7,seq)
 end
end

-- Direct definitions preserve references captured in PICO-8's API-localized
-- cart chunk. Virtual input is explicit; native input remains the default.
function btn(b,player)
 if not p8m.controlled or (player or 0)!=0 then return p8m_btn(b,player) end
 if b==nil then return p8m.mask end
 return (p8m.mask&(1<<b))!=0
end

function btnp(b,player)
 if not p8m.controlled or (player or 0)!=0 then return p8m_btnp(b,player) end
 local bits=0
 local factor=p8m_rate/30
 for i=0,5 do
  local n=p8m.holds[i] or 0
  if n==1 or (n>15*factor and (n-15*factor-1)%(4*factor)==0) then bits=bits|(1<<i) end
 end
 if b==nil then return bits end
 return (bits&(1<<b))!=0
end

function _init()
 for i=0,63 do poke(p8m.base+i,0) end
 if p8m_init then p8m_init() end
 poke(p8m.base,80,56,65,49,2,1)
 p8m_publish()
end

local function p8m_update()
 p8m_commands()
 if not p8m.paused or p8m.left>0 then
  if not p8m.draw then p8m.cpu=0 __p8mcp_draws=0 end
  for i=0,5 do
   if (p8m.mask&(1<<i))!=0 then p8m.holds[i]=(p8m.holds[i] or 0)+1
   else p8m.holds[i]=0 end
  end
  local before=stat(1)
  if __p8mcp_prof then __p8mcp_prof.begin_phase(1) end
  if p8m_u60 then p8m_u60()
  elseif p8m_u then p8m_u() end
  if __p8mcp_prof then __p8mcp_prof.end_phase() end
  p8m.cpu+=stat(1)-before
  p8m.previous=p8m.mask
  p8m.frame=(p8m.frame+1)&0x7fff
  if p8m.left>0 then p8m.left+=-1 end
  p8m.draw=true
 end
 p8m_publish()
end

if p8m_u60 then _update60=p8m_update else _update=p8m_update end

function _draw()
 if p8m.draw then
  local before=stat(1)
  if __p8mcp_prof then __p8mcp_prof.begin_phase(2) end
  if p8m_draw then p8m_draw() end
  if __p8mcp_prof then __p8mcp_prof.end_phase() end
  p8m.draws=(p8m.draws+1)&0x7fff
  p8m.cpu+=stat(1)-before
  p8m_percent(10,stat(1))
  p8m_percent(13,p8m.cpu)
  p8m_word(26,min(__p8mcp_draws,32767))
  if p8m.paused and p8m.left==0 then p8m.done=p8m.seq end
  p8m.draw=false
  if __p8mcp_prof then __p8mcp_prof.frame() end
 end
 p8m_publish()
end
