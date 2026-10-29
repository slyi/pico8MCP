-- Development-only Picotron bridge. Filesystem IPC replaces PICO-8 GPIO.
-- Injected in memory; the project's source and asset files are untouched.
-- CPU readings use Picotron's 60-Hz units; 1.0 is ~280k system cycles.
local app_init,app_update,app_draw=_init,_update or _update60,_draw
local native_btn,native_btnp=btn,btnp
local root="/appdata/picotron_mcp"
local paused,pending,updates,draws=true,0,0,0
local dirty,drawn_update=true,-1
local sequence,last_text=0,""
local controlled,mask,previous_mask=false,0,0
local update_cpu,draw_cpu=0,0
local value=nil
local problem=""

local function json(v)
 if type(v)=="number" then return v==v and abs(v)<1e300 and tostring(v) or "null" end
 if type(v)=="boolean" then return tostring(v) end
 return "null"
end
local function report()
 local text=string.format('{"ready":true,"seq":%d,"paused":%s,"pending":%d,"updates":%d,"draws":%d,"drawnUpdate":%d,"fps":%d,"cpu":%.9f,"updateCpu":%.9f,"drawCpu":%.9f,"cpuBaseHz":60,"value":%s,"error":%q}',
  sequence,tostring(paused),pending,updates,draws,drawn_update,stat(7),stat(1),update_cpu,draw_cpu,json(value),problem)
 store(root.."/state.json",text,{metadata_format="none"})
end
function btn(i,p,flags)
 if controlled and (p==nil or p==0) then
  if i==nil then return mask end
  return (mask&(1<<i))!=0
 end
 return native_btn(i,p,flags)
end
function btnp(i,p,flags)
 if controlled and (p==nil or p==0) then
  local pressed=mask&(~previous_mask)
  if i==nil then return pressed end
  return (pressed&(1<<i))!=0
 end
 return native_btnp(i,p,flags)
end
local function command(text)
 local fields={}
 for field in text:gmatch("[^|]+") do add(fields,field) end
 local seq=tonum(fields[1])
 if not seq or seq<=sequence then return end
 sequence=seq problem=""
 local op=fields[2]
 if op=="pause" then paused=true pending=0
 elseif op=="resume" then paused=false controlled=false
 elseif op=="step" then paused=true pending=tonum(fields[3]) mask=tonum(fields[4]) controlled=true
 elseif op=="get" or op=="set" then
  local owner=_ENV local parts={}
  for part in fields[3]:gmatch("[^.]+") do add(parts,part) end
  for i=1,#parts-1 do
   if type(owner)!="table" then error("not a table") end
   owner=owner[parts[i]]
  end
  if type(owner)!="table" then error("not a table") end
  local key=parts[#parts]
  if op=="set" then
   if owner[key]==nil and fields[5]!="true" then error("variable does not exist") end
   local v=fields[4]
   if v=="true" then v=true elseif v=="false" then v=false else v=tonum(v) end
   if v==nil then error("numeric or boolean values only") end
   owner[key]=v dirty=true
  end
  value=owner[key]
 elseif op!="state" then error("unknown bridge command") end
end
function _init()
 mkdir(root)
 store(root.."/command.txt","",{metadata_format="none"})
 if app_init then local ok,err=pcall(app_init) if not ok then problem=tostring(err) end end
 report()
end
function _update()
 local changed=false
 local text=fetch(root.."/command.txt",{raw_str=true})
 if text and text!=last_text then
  changed=true last_text=text
  local ok,err=pcall(command,text) if not ok then problem=tostring(err) end
 end
 if (not paused or pending>0) and problem=="" then
  local before=stat(1)
  if app_update then local ok,err=pcall(app_update) if not ok then problem=tostring(err) end end
  update_cpu=stat(1)-before updates+=1 dirty=true previous_mask=mask
  if pending>0 then pending-=1 end
 end
 if changed and not dirty then report() end
end
_update60=_update
function _draw()
 if dirty then
  local before=stat(1)
  if app_draw and problem=="" then local ok,err=pcall(app_draw) if not ok then problem=tostring(err) end end
  draw_cpu=stat(1)-before draws+=1 drawn_update=updates dirty=false
  report()
 end
end
