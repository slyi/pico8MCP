-- Optional development profiler. CPU units come from stat(1).
local __p8mcp_stat,__p8mcp_str,__p8mcp_log=stat,tostr,printh
local __p8mcp_max,__p8mcp_min=max,min
local __p8mcp_prof={active=false,rows={},stack={},starts={},children={},epoch=0,depth=0}
do
 local p=__p8mcp_prof
 for i=1,__SCOPE_COUNT__ do p.rows[i]={0,0,0,0,0,0,0} end
 function p.clear_stack()
  for i=1,p.depth do
   p.rows[p.stack[i]][6]+=1 p.invalid+=1
  end
  p.depth=0 p.epoch=(p.epoch+1)&0x7fff
 end
 function p.reset()
  p.active=false p.depth=0 p.epoch=(p.epoch+1)&0x7fff
  p.frames=0 p.updates=0 p.draws=0 p.native=0 p.update=0 p.draw=0
  p.minimum=32767 p.maximum=0 p.over=0 p.invalid=0 p.phase=nil
  for i=1,#p.rows do
   local r=p.rows[i]
   for j=1,6 do r[j]=0 end
  end
  p.active=true
 end
 function p.enter(id)
  if not p.active or not p.phase then return 0,p.epoch end
  local n=p.depth+1 p.depth=n p.stack[n]=id p.children[n]=0
  p.starts[n]=__p8mcp_stat(1)
  return n,p.epoch
 end
 function p.leave(id,n,epoch,...)
  if n==0 then return ... end
  if not p.active or epoch!=p.epoch then return ... end
  if n!=p.depth or p.stack[n]!=id then
   p.clear_stack() return ...
  end
  local elapsed=__p8mcp_stat(1)-p.starts[n]
  local r=p.rows[id]
  if elapsed<0 then
   r[6]+=1 p.invalid+=1
  else
   r[1]+=elapsed r[2]+=__p8mcp_max(0,elapsed-p.children[n])
   r[3]=__p8mcp_max(r[3],elapsed)
   if r[4]==32767 then r[4]=0 r[5]+=1 else r[4]+=1 end
   if n>1 then p.children[n-1]+=elapsed end
  end
  p.depth=n-1
  return ...
 end
 function p.exit(id)
  if p.active and p.phase then
   if p.depth==0 then p.rows[id][6]+=1 p.invalid+=1
   else p.leave(id,p.depth,p.epoch) end
  end
 end
 function p.wrap(id,fn)
  return function(...)
   local n,epoch=p.enter(id)
   return p.leave(id,n,epoch,fn(...))
  end
 end
 function p.begin_phase(phase)
  if not p.active then return end
  p.clear_stack() p.phase=phase p.before=__p8mcp_stat(1)
 end
 function p.end_phase()
  if not p.active then return end
  local elapsed=__p8mcp_stat(1)-p.before
  p.clear_stack()
  if elapsed>=0 then
   if p.phase==1 then p.updates+=1 p.update+=elapsed
   else p.draws+=1 p.draw+=elapsed end
  end
  p.phase=nil
 end
 function p.frame()
  if not p.active then return end
  local cpu=__p8mcp_stat(1)
  p.frames+=1 p.native+=cpu p.minimum=__p8mcp_min(p.minimum,cpu)
  p.maximum=__p8mcp_max(p.maximum,cpu)
  if cpu>1 then p.over+=1 end
 end
 function p.cancel()
  p.clear_stack() p.active=false p.phase=nil
 end
 function p.report(seq)
  p.cancel()
  local prefix="@@p8mcp_prof:"..seq..":"
  local function raw(v) return __p8mcp_str(v,0x2) end
  __p8mcp_log(prefix.."H:"..p.frames..":"..p.updates..":"..p.draws..":"..
   raw(p.native)..":"..raw(p.update)..":"..raw(p.draw)..":"..
   raw(p.minimum)..":"..raw(p.maximum)..":"..p.over..":"..p.invalid)
  for i=1,#p.rows do
   local r=p.rows[i]
   __p8mcp_log(prefix.."R:"..i..":"..raw(r[1])..":"..raw(r[2])..":"..
    raw(r[3])..":"..r[4]..":"..r[5]..":"..r[6]..":"..r[7])
  end
  __p8mcp_log(prefix.."DONE")
 end
 -- Initialize totals without recording initialization or ordinary gameplay.
 p.reset() p.active=false
end
