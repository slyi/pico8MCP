-- Shared DDA floor: the same Lua source runs on PICO-8 and Picotron.
-- Source: pico8mcp/examples/3dfloor/floor.lua, shared by both SDK backends.
-- Native viewport: PICO-8 128x128; Picotron 480x270.
-- Runtime detection replaces a build-time macro/preprocessor.
-- Controls: WASD move, arrows turn/pitch, X or 1-4 resolution,
-- G grid, B grid width, H CPU display, R reset, [/] crop.
-- T dithering is PICO-8-only in this proof (different colour formats).
-- Portable ceil/fraction/parity arithmetic replaces PICO-8 16.16 masks.
PICOTRON=type(window)=="function"
update_hz=PICOTRON and 60 or 30
screen_w=PICOTRON and 480 or 128
screen_h=PICOTRON and 270 or 128
screen_right=screen_w-1
screen_bottom=screen_h-1
screen_cx=screen_right/2

function held(scancode,name)
 if PICOTRON then return key(name) end
 return stat(28,scancode)
end

function floor_command(k)
 if k=="g" then grid=not grid end
 if k=="t" and not PICOTRON then dither=not dither end
 if k=="b" then grid_width=grid_width%4+1 end
 if k=="[" then horizon_clip-=1 end
 if k=="]" then horizon_clip+=1 end
 if k=="h" then cpu_hud=not cpu_hud end
 if k=="r" then reset_camera() end
 local n=tonum(k)
 if n and n>=1 and n<=4 then res=n end
end

function _init()
 if PICOTRON then
  if picotron then picotron.viewport(480,270,72*480/128,"Shared DDA floor")
  else window{title="Shared DDA floor",fullscreen=true,cursor=0} end
 end
 cam_u=0.25 cam_v=0.25 yaw=0 pitch=0
 res=PICOTRON and 4 or 2 grid=true grid_width=2 dither=false
 height=0.8 focal=72*screen_w/128 horizon=3*screen_h/8 far=16
 horizon_clip=horizon+11*screen_w/128 floor_y=flr(screen_h/2) cpu_hud=true
 speed=3 turn_speed=90 pitch_speed=60
 cpu=0 cpu_avg=0 telemetry_frame=0
 hud_text="cpu 0.0%" hud_value=-1
 spans=0 rows=0 grid_lines=0 grid_draws=0
 cached_yaw=-999 cached_res=0 cached_pitch=-999
 cached_height=0 cached_focal=0
 cached_horizon=0 cached_far=0
 cached_clip=0 cached_floor_y=-1
 row_cache={}
 if not PICOTRON then
  poke(0x5f2d,1)
  poke(0x5f34,1)
 end
end

function reset_camera()
 cam_u=0.25 cam_v=0.25 yaw=0 pitch=0
end

-- stat(28,scancode) is held keyboard input in the
-- education player; controller controls work too.
function _update()
 local forward,strafe,turn=0,0,0
 if held(26,"w") then forward+=1 end
 if held(22,"s") then forward-=1 end
 if held(4,"a") then strafe-=1 end
 if held(7,"d") then strafe+=1 end
 if btn(0) then turn-=1 end
 if btn(1) then turn+=1 end
 if btn(2) then pitch-=pitch_speed/update_hz end
 if btn(3) then pitch+=pitch_speed/update_hz end
 -- controller-only strafe: hold o + left/right
 if btn(4) then strafe+=turn turn=0 end
 if btnp(5) then res=res%4+1 end
 if PICOTRON then
  for k in all({"g","t","b","[","]","h","r","1","2","3","4"}) do
   if keyp(k) then floor_command(k) end
  end
 else
  while stat(30) do floor_command(stat(31)) end
 end
 res=mid(1,flr(res),4)
 pitch=mid(-45,pitch,45)
 horizon_clip=mid(horizon+4,horizon_clip,screen_h-8)
 floor_y=mid(0,floor_y,screen_h-8)
 yaw=(yaw+turn*turn_speed/update_hz)%360
 local c,s=cos(yaw/360),-sin(yaw/360)
 local length=sqrt(forward*forward+strafe*strafe)
 if length>1 then forward/=length strafe/=length end
 cam_u=(cam_u+(strafe*c+forward*s)*speed/update_hz)%2
 cam_v=(cam_v+(-strafe*s+forward*c)*speed/update_hz)%2
end

-- Cache row offsets and dda crossing periods for
-- a fixed heading. Walking does not rebuild it.
function prepare_view()
 local projection_changed=res!=cached_res or pitch!=cached_pitch or height!=cached_height or
  focal!=cached_focal or horizon!=cached_horizon or far!=cached_far or
  horizon_clip!=cached_clip or floor_y!=cached_floor_y
 if yaw==cached_yaw and not projection_changed then return end
 cached_yaw=yaw cached_res=res cached_pitch=pitch
 cached_height=height cached_focal=focal
 cached_horizon=horizon cached_far=far
 cached_clip=horizon_clip cached_floor_y=floor_y
 cs=cos(yaw/360) sn=-sin(yaw/360)
 if projection_changed then
  pitch_cos=cos(pitch/360) pitch_sin=-sin(pitch/360)
  -- Crop the dense source rows, then stretch the useful floor into the
  -- lower half. The same affine Y mapping is used by rays and grid lines.
  source_clip=max(horizon_clip,horizon+height*focal/far)
  view_y_scale=(screen_bottom-source_clip)/(screen_bottom-floor_y)
  view_y_offset=source_clip-floor_y*view_y_scale
  if pitch==0 then top=floor_y
  else
   local d=source_clip-horizon
   local den=focal*pitch_cos+d*pitch_sin
   if den<=0.001 then top=screen_h
   else
    local sy=horizon+focal*(d*pitch_cos-focal*pitch_sin)/den
    top=mid(0,ceil((sy-view_y_offset)/view_y_scale),screen_h)
   end
  end
  row_cache={}
  local hf=height*focal
  for y=top,screen_bottom,res do
   local bottom=min(screen_bottom,y+res-1)
   -- Keep the first band at the exact far boundary for every RES.
   local sample=y==top and top or (y+bottom)/2
   sample=sample*view_y_scale+view_y_offset
   if pitch==0 then
    local z=hf/max(0.001,sample-horizon)
    add(row_cache,{y,bottom,0,0,0,0,0,0,z,0})
   else
    local t=sample-horizon
    local down=max(0.001,t*pitch_cos+focal*pitch_sin)
    local forward=focal*pitch_cos-t*pitch_sin
    add(row_cache,{y,bottom,0,0,0,0,0,0,height*forward/down,height/down})
   end
  end
 end
 if top>=screen_h then min_u=0 max_u=0 min_v=0 max_v=0 return end
 local left_u=sn-screen_cx*cs/focal
 local left_v=cs+screen_cx*sn/focal
 for r in all(row_cache) do
  local z=r[9]
  local du,dv
  if pitch==0 then
   du=z*cs/focal dv=-z*sn/focal
   r[3]=z*left_u r[4]=z*left_v
  else
   local scale=r[10]
   du=scale*cs dv=-scale*sn
   r[3]=z*sn-screen_cx*scale*cs
   r[4]=z*cs+screen_cx*scale*sn
  end
  local pu,pv=32767,32767
  if abs(du)>0.00003 then pu=min(32767,1/abs(du)) end
  if abs(dv)>0.00003 then pv=min(32767,1/abs(dv)) end
  r[5]=du r[6]=dv r[7]=pu r[8]=pv
 end
 -- World offsets of the four clipping corners.
 local lo_u,hi_u,lo_v,hi_v=32767,-32767,32767,-32767
 for y in all({screen_bottom,top}) do
  local t=y*view_y_scale+view_y_offset-horizon
  local down=max(0.001,t*pitch_cos+focal*pitch_sin)
  local z,scale
  if pitch==0 then z=height*focal/t
  else z=height*(focal*pitch_cos-t*pitch_sin)/down scale=height/down end
  for x in all({-screen_cx,screen_cx}) do
   local u,v
   if pitch==0 then
    u=z*(sn+x*cs/focal) v=z*(cs-x*sn/focal)
   else
    u=z*sn+x*scale*cs v=z*cs-x*scale*sn
   end
   lo_u=min(lo_u,u) hi_u=max(hi_u,u)
   lo_v=min(lo_v,v) hi_v=max(hi_v,v)
  end
 end
 min_u=lo_u max_u=hi_u min_v=lo_v max_v=hi_v
end

-- Drop-in DDA experiment: row-local values, single-axis spans,
-- equal-period diagonal spans, and a simpler general walker.
function draw_scanline(r)
 local ceil,rectfill,min,flr=ceil,rectfill,min,flr
 local y0,y1=r[1],r[2]
 local u,v=cam_u+r[3],cam_v+r[4]
 local fu,fv=u-flr(u),v-flr(v)
 local pu,pv=r[7],r[8]
 local nu,nv=32767,32767
 if pu<32767 then nu=(r[5]>=0 and 1-fu or fu)*pu end
 if pv<32767 then nv=(r[6]>=0 and 1-fv or fv)*pv end
 local white=(flr(u)+flr(v))%2!=0
 local du,dv=min(16000,pu),min(16000,pv)
 if pu==32767 or pv==32767 then
  local edge,delta=nu,du
  if pu==32767 then edge=nv delta=dv end
  if edge>=screen_w then
   if white then rectfill(0,y0,screen_right,y1,7) spans+=1 end
   return
  end
  if white then
   local last=ceil(edge)-1
   if last>=0 then rectfill(0,y0,last,y1,7) spans+=1 end
   edge+=delta
  end
  local period=delta*2
  while edge<screen_w do
   local first,last=ceil(edge),min(screen_right,ceil(edge+delta)-1)
   if first<=last then rectfill(first,y0,last,y1,7) spans+=1 end
   edge+=period
  end
  return
 end
 if pu==pv and pu<screen_w then
  local a,b=nu,nv
  if a>b then a,b=b,a end
  if a==b then
   if white then rectfill(0,y0,screen_right,y1,7) spans+=1 end
   return
  end
  local first,last
  if white then
   last=ceil(a)-1
   if last>=0 then rectfill(0,y0,last,y1,7) spans+=1 end
   a,b=b,a+pu
  end
  while a<screen_w do
   first=ceil(a) last=min(screen_right,ceil(b)-1)
   if first<=last then rectfill(first,y0,last,y1,7) spans+=1 end
   a+=pu b+=pu
  end
  return
 end
 local pixel=0
 while true do
  local edge=nu<nv and nu or nv
  if edge>screen_w then edge=screen_w end
  local last=ceil(edge)-1
  if white and pixel<=last then
   rectfill(pixel,y0,last,y1,7) spans+=1
  end
  if edge>=screen_w then break end
  if nu<=edge then nu+=du white=not white end
  if nv<=edge then nv+=dv white=not white end
  pixel=last+1
 end
end


-- Clip A*x+B*y+C=0 against the floor rectangle.
-- Choose the stable pair of rectangle boundaries first, then clip only
-- the endpoints outside the other pair. Preserve the old endpoint order.
function grid_line(a,b,c)
 local abs,line=abs,line
 local core_col,edge_col=grid_core,grid_edge
 local x1,y1,x2,y2,p1,p2
 local top=top
 if abs(a)>=height/4 then
  x1=-(b*top+c)/a y1=top p1=2
  x2=-(b*screen_bottom+c)/a y2=screen_bottom p2=3
  if (x1<0 and x2<0) or (x1>screen_right and x2>screen_right) then return end
  if x1<0 then x1=0 y1=-c/b p1=0
  elseif x1>screen_right then x1=screen_right y1=-(a*screen_right+c)/b p1=1 end
  if x2<0 then x2=0 y2=-c/b p2=0
  elseif x2>screen_right then x2=screen_right y2=-(a*screen_right+c)/b p2=1 end
 else
  if abs(b)<0.00003 then return end
  x1=0 y1=-c/b p1=0
  x2=screen_right y2=-(a*screen_right+c)/b p2=1
  if (y1<top and y2<top) or (y1>screen_bottom and y2>screen_bottom) then return end
  if y1<top then x1=-(b*top+c)/a y1=top p1=2
  elseif y1>screen_bottom then x1=-(b*screen_bottom+c)/a y1=screen_bottom p1=3 end
  if y2<top then x2=-(b*top+c)/a y2=top p2=2
  elseif y2>screen_bottom then x2=-(b*screen_bottom+c)/a y2=screen_bottom p2=3 end
 end
 if p1>p2 then x1,y1,x2,y2=x2,y2,x1,y1 end
 if abs(x1-x2)+abs(y1-y2)<0.01 then return end

 if x2 then
  local ox,oy=0,1
  if abs(y2-y1)>abs(x2-x1) then ox=1 oy=0 end
  local width=grid_width>0 and mid(1,flr(grid_width),4) or res
  -- RES 4 checker bands can protrude past the two-pixel grid.
  -- Three additional major-axis strokes cover the band raster error.
  if res==4 then width+=3 end
  local half=(width-1)/2
  -- Major-axis offsets leave no gaps at diagonal headings.
  -- Thicker lines use optional dithering on their outer strokes.
  if width>=3 then
   line(x1-half*ox,y1-half*oy,x2-half*ox,y2-half*oy,edge_col)
   line(x1+half*ox,y1+half*oy,x2+half*ox,y2+half*oy,edge_col)
   for off=-half+1,half-1 do
    line(x1+off*ox,y1+off*oy,x2+off*ox,y2+off*oy,core_col)
   end
  else
   for off=-half,half do
    line(x1+off*ox,y1+off*oy,x2+off*ox,y2+off*oy,core_col)
   end
  end
  grid_draws+=width
  grid_lines+=1
 end
end

function draw_grid()
 -- 14/16 covered = 12.5% dither transparency.
 -- Pattern transparency leaves the checker beneath.
 if dither and not PICOTRON then
  grid_core=0x1100.0101 grid_edge=0x1100.5a5a
  fillp(0x0101.8)
 else
  grid_core=0 grid_edge=0
  fillp()
 end
 local a=height*cs
 local base=height*(focal*sn*pitch_cos-screen_cx*cs)
 for n=ceil(cam_u+min_u),flr(cam_u+max_u) do
  local delta=cam_u-n
  local b=delta*pitch_cos-height*sn*pitch_sin
  grid_line(a,b*view_y_scale,base+delta*focal*pitch_sin+b*(view_y_offset-horizon))
 end
 a=-height*sn
 base=height*(focal*cs*pitch_cos+screen_cx*sn)
 for n=ceil(cam_v+min_v),flr(cam_v+max_v) do
  local delta=cam_v-n
  local b=delta*pitch_cos-height*cs*pitch_sin
  grid_line(a,b*view_y_scale,base+delta*focal*pitch_sin+b*(view_y_offset-horizon))
 end
 fillp()
end

local render_cam_u,render_cam_v,render_yaw,render_res,render_grid,render_grid_width,render_height,render_focal,render_horizon,render_far,render_dither,render_pitch,render_clip,render_floor_y,render_hud
function _draw()
 local redrawn=false
 if render_cam_u!=cam_u or render_cam_v!=cam_v or render_yaw!=yaw or render_res!=res or render_grid!=grid or render_grid_width!=grid_width or render_height!=height or render_focal!=focal or render_horizon!=horizon or render_far!=far or render_dither!=dither or render_pitch!=pitch or render_clip!=horizon_clip or render_floor_y!=floor_y or render_hud!=cpu_hud then
 prepare_view()
 cls(0)
 spans=0 grid_lines=0 grid_draws=0 rows=#row_cache
 for r in all(row_cache) do draw_scanline(r) end
 if grid and res>1 and top<screen_h then
  clip(0,top,screen_w,screen_h-top)
  draw_grid()
  clip()
 end

 render_cam_u,render_cam_v,render_yaw,render_res,render_grid,render_grid_width,render_height,render_focal,render_horizon,render_far,render_dither,render_pitch,render_clip,render_floor_y,render_hud=cam_u,cam_v,yaw,res,grid,grid_width,height,focal,horizon,far,dither,pitch,horizon_clip,floor_y,cpu_hud
 redrawn=true
 end
 -- Format the compact CPU label at 7.5 Hz; reuse the string while moving.
 -- A static floor only needs the small label rectangle refreshed.
 if cpu_hud then
  local changed=false
  if telemetry_frame%4==0 then
   local n=flr(cpu_avg*1000+0.5)
   if n!=hud_value then
    hud_value=n hud_text="cpu "..flr(n/10).."."..(n%10).."%"
    changed=true
   end
  end
  if redrawn or changed then
   rectfill(0,0,80,11,0)
   print(hud_text,2,1,7)
  end
 end
 -- Native telemetry, independent of the optional label.
 -- GPIO 0..13 belongs to this cart; MCP reserves 64..127 separately.
 cpu=stat(1)
 cpu_avg+=(cpu-cpu_avg)*0.2775
 local value=flr(mid(0,cpu,3)*10000)
 telemetry_frame=(telemetry_frame+1)%256
 if not PICOTRON then
 poke(0x5f80,value%256,flr(value/256),stat(7),res,
  flr(yaw)%256,flr(yaw/256),telemetry_frame,(grid and res>1) and 1 or 0,
  dither and 1 or 0,pitch+45,horizon_clip,floor_y,top,cpu_hud and 1 or 0)
 end
end

-- PICO-8 uses _update at 30 Hz. Picotron updates at 60 Hz; retain both
-- callback names for Picotron player versions with different conventions.
if PICOTRON then
 _update60=_update
end
