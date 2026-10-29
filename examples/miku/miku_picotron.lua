-- Miku-specific host configuration. The animation/render/physics engine is
-- the same player.lua included by the four PICO-8 carts.
-- Assets are format conversions of existing miku*.lua data, not new motion
-- or music. 30 Hz / 1800 intervals; the interpolation endpoint is retained.
local asset,motion
local clock
local replay=false
local draws=0
local update_cpu,draw_cpu=0,0
local draw_started=0
local sum_cpu,peak_cpu,samples=0,0,0
local first_audio_time
local meter_time,meter_frames=0,0
local state_path="/appdata/mcp_poc/state.json"
player_host={}

function player_host.init()
 local v=picotron.viewport(480,270,430,"Miku / one minute / Picotron")
 view_w=v.width view_h=v.height view_cx=v.cx view_cy=v.cy
 view_focal=v.focal view_scale=v.scale
 -- stat(1) is in 60-Hz budget units, even at a 30-Hz application rate.
 monitor_cpu_scale=.5 monitor_fps=30
 shade_levels=8 shade_limit=255
 shade_ramps=picotron.material_ramps({{34,178,188},{248,204,188},{59,67,86},{211,221,226}},8,32)
 asset=fetch("model.pod")
 mkdir("/appdata/mcp_poc")
 store(state_path,'{"ready":true,"seq":0,"prepared":false,"elapsed":0}',{metadata_format="none"})
end
function player_host.asset(i) return asset[i] end
function player_host.value(block,channel,frame)
 return motion:get(channel,(block-1)*121+frame)
end
function player_host.preload()
 motion=fetch("motion.pod")
 for block=1,15 do
  local crc1,crc2=0,0
  for channel=0,62 do
   for frame=0,120 do
    local value=motion:get(channel,(block-1)*121+frame)
    crc1=(crc1+value)%251 crc2=(crc2+crc1)%251
   end
   if channel%4==3 then yield() end
  end
  assert(crc1==motion_checks[block][1] and crc2==motion_checks[block][2],"motion checksum "..block)
  checks[block]={crc1,crc2}
  progress=block/15 yield()
 end
 asset=nil
end
function player_host.audio_time() return clock and clock() or 0 end
local function start_music()
 clock=picotron.music_clock(32,15,128)
 audio_started=false first_audio_time=time()
 music(0,0,0x0f)
end
function player_host.update()
 if failure then return end
 if not ready then
  if prepared then ready=true start_music()
  else
   tick_work()
   if work and costatus(work)=="dead" then prepared=true end
  end
  return
 end
 if btnp(5) or keyp("x") then replay=true end
end
function player_host.before_draw()
 draw_started=stat(1)
 if not ready then return end
 if replay then
  replay=false music(-1) chunk=1 ended=false physics_ready=false
  elapsed=0 last_elapsed=0 physics_acc=0 frames=0
  sum_cpu=0 peak_cpu=0 samples=0
  start_music()
 end
 elapsed=audio_time() chunk=min(15,flr(elapsed/4)+1)
 if not ended then advance_motion() end
 update_cpu=stat(1)-draw_started
end
function player_host.after_draw()
 draw_cpu=stat(1)-draw_started-update_cpu
 draws+=1
 meter_frames+=1
 if time()-meter_time>=1 then
  monitor_fps=flr(meter_frames/(time()-meter_time)+.5)
  meter_frames=0 meter_time=time()
 end
 if ready then
  local total=update_cpu+draw_cpu
  sum_cpu+=total peak_cpu=max(peak_cpu,total) samples+=1
 end
 if draws%15==0 or ended or failure then
  local checksum_count=0
  for i=1,15 do if checks and checks[i] then checksum_count+=1 end end
  local text=string.format('{"ready":true,"seq":0,"prepared":%s,"elapsed":%.9f,"chunk":%d,"draws":%d,"frames":%d,"fps":%d,"drawCpu":%.9f,"updateCpu":%.9f,"meanCpu":%.9f,"peakCpu":%.9f,"ended":%s,"underflows":%d,"checks":%d,"musicPattern":%d,"musicRow":%d,"musicTick":%d,"wallElapsed":%.9f,"error":%q}',
   tostring(ready),elapsed or 0,chunk or 1,draws,frames or 0,stat(7),draw_cpu/2,update_cpu/2,
   sum_cpu/max(1,samples)/2,peak_cpu/2,tostring(ended or false),underflows or 0,checksum_count,
   stat(466),stat(400,9),stat(400,11),first_audio_time and time()-first_audio_time or 0,failure or "")
  store(state_path,text,{metadata_format="none"})

 end
 picotron.limit_30()
end
