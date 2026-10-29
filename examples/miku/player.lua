-- miku: one minute. model 3, live secondary motion, four synth voices.
-- Optional platform adapter: asset I/O, music clock and viewport only.
-- Pose reconstruction, interpolation, skinning and Verlet remain shared.
view_w=128 view_h=128 view_focal=188 view_cx=64 view_cy=64 view_scale=1
shade_levels=4 shade_limit=15
shade_ramps={{10,0xca,0xbc,0x6c},{4,15,0x7f,7},{0,1,5,6},{5,6,0x67,7}}
function ar(i)
 if player_host then return player_host.asset(i) end
 return peek(i<0x3000 and i or i+0x200)
end
function rb()
 local v=ar(cursor) cursor+=1 return v
end
function rw() return rb()+rb()*256 end
function rc()
 local v=rw()
 -- PICO-8 words already wrap signed; Lua 5.4 needs an explicit conversion.
 if v>32767 then v-=65536 end
 return v/4096
end
function rn()
 local v=rb() if v>127 then v-=256 end
 return v/127
end

-- Provenance: packed rig/mesh loader written for this demo.
-- Asset source: ../miku-skeletal-four-models/model-data.json, model 3.
-- Original mesh: "Low poly Miku Hatsune" by emanueler, Sketchfab, 2021.
-- https://sketchfab.com/3d-models/low-poly-miku-hatsune-b944f4d0561641f2954fcd4f9424e27f
-- Sly_i's Scratch adaptation adds modesty changes and reduces polygon count.
-- Packing layout: build.py / pack_model. No external loader code copied.
function load_model()
 cursor=0
 parents={} offsets={} bones={}
 for i=1,21 do parents[i]=rb() end
 for i=1,21 do offsets[i]={rc(),rc(),rc()} end
 root_offset={rc(),rc(),rc()}
 weights={} helpers={} faces={} ropes={}
 for i=1,387 do
  local ws={}
  for j=1,rb() do add(ws,{rb(),rb()/255,rc(),rc(),rc()}) end
  weights[i]=ws
 end
 rigid_groups={} blended={}
 for i=1,45 do rigid_groups[i]={} end
 for i=1,387 do
  local ws=weights[i]
  if #ws==1 then
   local w=ws[1] add(rigid_groups[w[1]],{i,w[3],w[4],w[5]})
  else add(blended,i) end
 end
 for i=1,32 do helpers[i]={rb(),rc(),rc(),rc()} end
 for i=1,597 do
  local a,b,c=rw(),rw(),rw()
  faces[i]={(a&511)+1,(b&511)+1,(c&511)+1,flr(b/512)+1,a&512>0,0,0,0,0,0,0}
 end
 for i=1,27 do
  ropes[i]={rb(),rc(),rc(),rw(),rc(),rc(),rc()}
 end
 for i=1,45 do bones[i]={1,0,0,0,0,1,0,0,0,0,1,0} end
 wx={} wy={} wz={} sx={} sy={} sz={} order={}
 rx={} ry={} rz={} ox={} oy={} oz={}
 ax={} ay={} az={}
 light_x={} light_y={} light_z={} heads={}
end

function ram_string(address,length)
 local result=""
 for i=0,length-1,128 do
  result..=chr(peek(address+i,min(128,length-i)))
  if i%1024==0 then yield() end
 end
 return result
end

function copy_pose(i,dest,slow)
 local s=motion_cache[i]
 for pos=1,#s,128 do
  poke(dest+pos-1,ord(s,pos,min(128,#s-pos+1)))
  if slow and pos%512==1 then yield() end
 end
end

function queue_pose(i,dest)
 copy_data=motion_cache[i] copy_dest=dest copy_pos=1
end

function step_copy()
 if not copy_data then return end
 for i=1,4 do
  local count=min(128,#copy_data-copy_pos+1)
  poke(copy_dest+copy_pos-1,ord(copy_data,copy_pos,count))
  copy_pos+=count
  if copy_pos>#copy_data then copy_data=nil return end
 end
end

-- Provenance: this demo's preload/cache pipeline.
-- Standard delta + zigzag + byte planes are produced by build.py;
-- entropy compression is upstream ZX0, decoded by zx0_decompress above.
-- All compressed sources and decoded pose blocks are cached before playback.
function preload_motion()
 if player_host then player_host.preload() init_face_normals() return end
 local source={}
 local files={"miku_cache_a.p8","miku_cache_b.p8","miku_cache_c.p8"}
 for i=1,3 do
  reload(0,0,0x3000,files[i])
  cartridge_reads+=1
  io_wait=true
  yield()
  local first=ram_string(0,0x3000)
  reload(0,0x3200,0x1100,files[i])
  cartridge_reads+=1 io_wait=true yield()
  source[i]=first..ram_string(0,0x1100)
  printh("cached "..i.." length "..#source[i].." first "..tostr(ord(source[i],1)))
  progress=i/18 yield()
 end
 motion_cache={}
 for i=1,15 do
  local d=motion_dir[i]
  local crc1,crc2=0,0
  zx0_decompress(function(n)
    assert(n<d[3],"motion input overflow "..i.." at "..n)
    local pos=d[2]+n
    if pos<16640 then return ord(source[d[1]],pos+1) end
    return ord(source[d[1]+1],pos-16640+1)
   end,
   function(n) return peek(0x8000+n) end,
   function(n,v)
    poke(0x8000+n,v)
    if n%512==511 then yield() end
   end)
  for channel=0,62 do
   local v=0
   local a=0x8000+channel*242
   for frame=0,120 do
    local z=peek(a+frame)+peek(a+121+frame)*256
    v+=z%2==0 and z/2 or -(z+1)/2
    if channel>=3 then v=v%512 end
    crc1=(crc1+v)%251 crc2=(crc2+crc1)%251
    poke2(0xc000+(frame*63+channel)*2,v)
   end
   if channel%4==3 then yield() end
  end
  checks[i]={crc1,crc2}
  motion_cache[i]=ram_string(0xc000,15246)
  progress=(i+3)/18 yield()
 end
 source=nil
 copy_pose(1,0x8000,true) copy_pose(2,0xc000,true)
 init_face_normals()
end

-- Provenance: standard cross-product normals and dominant skin weights,
-- implemented here. Calculated once during preload in the first pose;
-- normals are retained in the dominant bone's local coordinates.
function init_face_normals()
 pose(0) bind_helpers() physics(0) skin()
 for i=1,#faces do
  local f=faces[i] local a,b,c=f[1],f[2],f[3]
  local scores={} local owner,best=1,-1
  for vertex in all({a,b,c}) do
   for w in all(weights[vertex]) do
    local bone=w[1] local score=(scores[bone] or 0)+w[2]
    scores[bone]=score
    if score>best then owner,best=bone,score end
   end
  end
  local ux,uy,uz=(wx[b]-wx[a])*8,(wy[b]-wy[a])*8,(wz[b]-wz[a])*8
  local vx,vy,vz=(wx[c]-wx[a])*8,(wy[c]-wy[a])*8,(wz[c]-wz[a])*8
  local nx,ny,nz=uy*vz-uz*vy,uz*vx-ux*vz,ux*vy-uy*vx
  local length=sqrt(max(0,nx*nx+ny*ny+nz*nz))
  if length>.001 then nx/=length ny/=length nz/=length
  else nx,ny,nz=0,0,1 end
  local m=bones[owner]
  f[7]=owner
  f[8]=m[1]*nx+m[5]*ny+m[9]*nz
  f[9]=m[2]*nx+m[6]*ny+m[10]*nz
  f[10]=m[3]*nx+m[7]*ny+m[11]*nz
  if i%16==0 then yield() end
 end
end

function tick_work()
 local steps=0
 while work and costatus(work)!="dead" and stat(1)<.85 and steps<32 do
   steps+=1
   local ok,err=coresume(work)
   if not ok then
    failure=tostr(err) printh("decode: "..failure)
    poke(0x5f80,77,75,2) work=nil
   end
   if io_wait then io_wait=false break end
 end
end

function value(channel,frame)
 if player_host then return player_host.value(chunk,channel,frame) end
 return peek2(buffer+(frame*63+channel)*2)
end

-- Provenance: retargeted VMD bone samples from the user's Scratch project.
-- Interpolation and Rz*Ry*Rx matrix reconstruction implemented in this demo.
-- Source quantization: 30 fps, 9-bit turns, root translation 0.5 mm.
function pose(seconds)
 local ft=(seconds-(chunk-1)*4)*30
 local frame=min(119,flr(ft)) local f=ft-frame
 local root={}
 for i=0,2 do
  local a,b=value(i,frame),value(i,frame+1)
  root[i+1]=(a+(b-a)*f)/2000+root_offset[i+1]
 end
 local m=bones[1]
 m[4]=root[1] m[8]=root[2] m[12]=root[3]
 camera_x=root[1] camera_y=root[2] camera_z=root[3]-.05
 for bi=2,21 do
  local ang={}
  for j=0,2 do
   local channel=3+(bi-2)*3+j
   local a,b=value(channel,frame),value(channel,frame+1)
   ang[j+1]=(a+((b-a+256)%512-256)*f)/512
  end
  local sx,cx=-sin(ang[1]),cos(ang[1])
  local sy,cy=-sin(ang[2]),cos(ang[2])
  local sz,cz=-sin(ang[3]),cos(ang[3])
  local r={cz*cy,cz*sy*sx-sz*cx,cz*sy*cx+sz*sx,
   sz*cy,sz*sy*sx+cz*cx,sz*sy*cx-cz*sx,-sy,cy*sx,cy*cx}
  local p=bones[parents[bi]] local o=offsets[bi] local m=bones[bi]
  for row=0,2 do
   local k=row*4+1
   for col=0,2 do m[k+col]=p[k]*r[col+1]+p[k+1]*r[col+4]+p[k+2]*r[col+7] end
   m[k+3]=p[k]*o[1]+p[k+1]*o[2]+p[k+2]*o[3]+p[k+3]
  end
 end
end

function bind_helpers()
 for i=1,32 do
  local h=helpers[i] local m=bones[h[1]] local v=387+i
  wx[v]=m[1]*h[2]+m[2]*h[3]+m[3]*h[4]+m[4]
  wy[v]=m[5]*h[2]+m[6]*h[3]+m[7]*h[4]+m[8]
  wz[v]=m[9]*h[2]+m[10]*h[3]+m[11]*h[4]+m[12]
 end
end

-- Provenance: Verlet rope integration/constraints implemented for this demo,
-- adapted to the Scratch project's Physics tables and pigtail/tie rig.
-- No external physics-library implementation is included here.
function physics(steps)
 local head=bones[7] local chest=bones[5]
 if not physics_ready then
  for n=1,27 do
   local v=387+n
   rx[n]=wx[v] ry[n]=wy[v] rz[n]=wz[v]
   ox[n]=wx[v] oy[n]=wy[v] oz[n]=wz[v]
   ax[n]=wx[v] ay[n]=wy[v] az[n]=wz[v]
  end
  physics_ready=true
 end
 for step=1,steps do
  local mix=step/steps
  for n=1,27 do
   local r=ropes[n] local v=387+n
   if r[1]==1 then
    rx[n]=ax[n]+(wx[v]-ax[n])*mix
    ry[n]=ay[n]+(wy[v]-ay[n])*mix
    rz[n]=az[n]+(wz[v]-az[n])*mix
    ox[n]=rx[n] oy[n]=ry[n] oz[n]=rz[n]
   else
    local x,y,z=rx[n],ry[n],rz[n]
    local dx=head[1]*r[5]+head[2]*r[6]+head[3]*r[7]
    local dy=head[5]*r[5]+head[6]*r[6]+head[7]*r[7]
    local dz=head[9]*r[5]+head[10]*r[6]+head[11]*r[7]
    rx[n]=x+(x-ox[n])*r[3]+(rx[n-1]+dx*r[2]-x)*r[4]/3600
    ry[n]=y+(y-oy[n])*r[3]+(ry[n-1]+dy*r[2]-y)*r[4]/3600
    rz[n]=z+(z-oz[n])*r[3]+((rz[n-1]+dz*r[2]-z)*r[4]-9.81)/3600
    ox[n]=x oy[n]=y oz[n]=z
   end
  end
  for pass=1,2 do
   for n=1,27 do
    local r=ropes[n]
    if r[1]==0 then
     local dx,dy,dz=rx[n]-rx[n-1],ry[n]-ry[n-1],rz[n]-rz[n-1]
     local length=sqrt(dx*dx+dy*dy+dz*dz)
     if length>.001 then
      local scale=r[2]/length
      rx[n]=rx[n-1]+dx*scale ry[n]=ry[n-1]+dy*scale rz[n]=rz[n-1]+dz*scale
     end
     if n>=20 then
      local x,y,z=rx[n]-chest[4],ry[n]-chest[8],rz[n]-chest[12]
      local local_x=x*chest[1]+y*chest[5]+z*chest[9]
      local local_z=x*chest[3]+y*chest[7]+z*chest[11]
      local depth=-(x*chest[2]+y*chest[6]+z*chest[10])
      if abs(local_x)<.14 and local_z>-.40 and local_z<.14 and depth<.101 then
       local d=.101-depth
       rx[n]-=chest[2]*d ry[n]-=chest[6]*d rz[n]-=chest[10]*d
       ox[n]-=chest[2]*d oy[n]-=chest[6]*d oz[n]-=chest[10]*d
      end
     end
    end
   end
  end
 end
 for n=1,27 do
  local v=387+n
  ax[n]=wx[v] ay[n]=wy[v] az[n]=wz[v]
  wx[v]=rx[n] wy[v]=ry[n] wz[v]=rz[n]
 end
 for chain=0,2 do
  for seg=0,7 do
   local a=388+chain*9+seg local b=a+1
   local dx,dy,dz=wx[b]-wx[a],wy[b]-wy[a],wz[b]-wz[a]
   local length=max(.001,sqrt(dx*dx+dy*dy+dz*dz))
   dx/=length dy/=length dz/=length
   local dot=head[1]*dx+head[5]*dy+head[9]*dz
   local x,y,z=head[1]-dx*dot,head[5]-dy*dot,head[9]-dz*dot
   length=max(.001,sqrt(x*x+y*y+z*z)) x/=length y/=length z/=length
   local m=bones[22+chain*8+seg]
   m[1]=x m[2]=dy*z-dz*y m[3]=dx m[4]=wx[a]
   m[5]=y m[6]=dz*x-dx*z m[7]=dy m[8]=wy[a]
   m[9]=z m[10]=dx*y-dy*x m[11]=dz m[12]=wz[a]
  end
 end
end

-- Provenance: standard rigid/linear-blend skinning implemented in this demo.
-- Weights and binds come from the original Scratch rig's Skinning tables.
-- Rigid grouping and dominant-bone normal lighting were added here.
function skin()
 for bone=1,45 do
  local m=bones[bone]
  local a,b,c,d=m[1],m[2],m[3],m[4]
  local e,f,g,h=m[5],m[6],m[7],m[8]
  local j,k,l,n=m[9],m[10],m[11],m[12]
  light_x[bone]=-a*.386-e*.628+j*.676
  light_y[bone]=-b*.386-f*.628+k*.676
  light_z[bone]=-c*.386-g*.628+l*.676
  for w in all(rigid_groups[bone]) do
   local i,x,y,z=w[1],w[2],w[3],w[4]
   wx[i]=a*x+b*y+c*z+d
   wy[i]=e*x+f*y+g*z+h
   wz[i]=j*x+k*y+l*z+n
  end
 end
 for i in all(blended) do
  local x,y,z=0,0,0
  for w in all(weights[i]) do
   local m=bones[w[1]] local a,b,c=w[3],w[4],w[5]
   x+=(m[1]*a+m[2]*b+m[3]*c+m[4])*w[2]
   y+=(m[5]*a+m[6]*b+m[7]*c+m[8])*w[2]
   z+=(m[9]*a+m[10]*b+m[11]*c+m[12])*w[2]
  end
  wx[i]=x wy[i]=y wz[i]=z
 end
end

-- Provenance: demo clock reading native PICO-8 MUSIC/SFX state.
-- "Miku" by Anamanaguchi feat. Hatsune Miku; instrumental arrangement:
-- creamil, https://onlinesequencer.net/2492007 (converted by build.py).
function audio_time()
 if player_host then return player_host.audio_time() end
 if stat(57) then
  audio_started=true
  return min(60,stat(54)*3.75+stat(56)/128)
 end
 return audio_started and 60 or 0
end

function project(x,y,z)
 local depth=y-camera_y+3.65
 return view_cx+(x-camera_x)*view_focal/depth,view_cy-(z-camera_z)*view_focal/depth,depth
end

-- Provenance: scanline filler implemented in this demo using rectfill.
-- Short-axis H/V traversal and thin-line fallback were benchmarked here.
-- Community benchmark context (not copied routine):
-- https://www.lexaloffle.com/bbs/index.php?tid=31478
function triangle(x1,y1,x2,y2,x3,y3,c)
 local span=rectfill local vertical=false
 if y1>y2 then x1,x2=x2,x1 y1,y2=y2,y1 end
 if y2>y3 then x2,x3=x3,x2 y2,y3=y3,y2 end
 if y1>y2 then x1,x2=x2,x1 y1,y2=y2,y1 end
 local lo,hi=x1,x1
 if x2<lo then lo=x2 end if x2>hi then hi=x2 end
 if x3<lo then lo=x3 end if x3>hi then hi=x3 end
 local w,h=hi-lo,y3-y1
 -- Normal triangles fall through after at most two size comparisons.
 -- The bounds above are also needed by the ordinary filler.
 if w<=1 then
  local x=(lo+hi)/2 line(x,y1,x,y3,c) return
 elseif h<=1 then
  local y=(y1+y3)/2 line(lo,y,hi,y,c) return
 end
 if h>w then
  vertical=true x1,y1,x2,y2,x3,y3=y1,x1,y2,x2,y3,x3
  if y1>y2 then x1,x2=x2,x1 y1,y2=y2,y1 end
  if y2>y3 then x2,x3=x3,x2 y2,y3=y3,y2 end
  if y1>y2 then x1,x2=x2,x1 y1,y2=y2,y1 end
 end
 if y3-y1<.01 then return end
 if ceil(y1)>flr(y3) then return end
 local slope=(x3-x1)/(y3-y1)
 local limit=vertical and view_w-1 or view_h-1
 local first=max(0,ceil(y1)) local last=min(limit,ceil(y2)-1)
 if y2>y1 and first<=last then
  local short=(x2-x1)/(y2-y1)
  local a=x1+(first-y1)*slope local b=x1+(first-y1)*short
  if vertical then
   for y=first,last do span(y,a,y,b,c) a+=slope b+=short end
  else
   for y=first,last do span(a,y,b,y,c) a+=slope b+=short end
  end
 end
 first=max(0,ceil(y2)) last=min(limit,flr(y3))
 if y3>y2 and first<=last then
  local short=(x3-x2)/(y3-y2)
  local a=x1+(first-y1)*slope local b=x2+(first-y2)*short
  if vertical then
   for y=first,last do span(y,a,y,b,c) a+=slope b+=short end
  else
   for y=first,last do span(a,y,b,y,c) a+=slope b+=short end
  end
 end
end

-- Provenance: linked depth buckets and flat shading implemented in this demo.
-- Standard bucket-sort method; tables reused, membership rebuilt each frame.
-- The 64-bin choice comes from this model's native A-pose measurements.
function scene()
 cls(0)
 local x,y,d=project(camera_x,camera_y,0)
 ovalfill(x-19.5*view_scale,y-2.5*view_scale,x+19.5*view_scale,y+2.5*view_scale,1)
 for i=-3,3 do
  local x1,y1=project(i,-1,0) local x2,y2=project(i,5,0)
  line(x1,y1,x2,y2,1)
  local a,b=project(-4,i+1,0) local c,d=project(4,i+1,0)
  line(a,b,c,d,1)
 end
 --profile_projection
 local count=0 local near,far=8,0
 local cx,cy,cz=camera_x,camera_y,camera_z
 for i=1,387 do
  local depth=wy[i]-cy+3.65 local scale=view_focal/depth
  sx[i]=view_cx+(wx[i]-cx)*scale sy[i]=view_cy-(wz[i]-cz)*scale sz[i]=depth
  if depth<near then near=depth end
  if depth>far then far=depth end
 end
 --profile_cull
 local scale=63/(3*(far-near)+.001) near*=3
 for key=0,63 do heads[key]=0 end
 for i=1,597 do
  local f=faces[i] local a,b,c=f[1],f[2],f[3]
  local area=(sx[b]-sx[a])*(sy[c]-sy[a])-(sy[b]-sy[a])*(sx[c]-sx[a])
  if f[5] or area<-.01 then
   count+=1
   local depth=sz[a]+sz[b]+sz[c]
   local key=mid(0,flr((depth-near)*scale),63)
   f[11]=heads[key] heads[key]=i
  end
 end
 --profile_raster
 local ramps=shade_ramps
 if shade_limit>15 then fillp() end
 for key=63,0,-1 do
  local index=heads[key]
  while index!=0 do
  local f=faces[index] local a,b,c=f[1],f[2],f[3]
  local bone=f[7]
  local light=f[8]*light_x[bone]+f[9]*light_y[bone]+f[10]*light_z[bone]
  if f[5] then light=abs(light) end
  local level=mid(1,flr((.35+.65*max(0,light))*shade_levels)+1,shade_levels)
  local color=ramps[f[4]][level]
  if shade_limit==15 then
   if color>15 then fillp(0x5a5a) else fillp() end
  end
  triangle(sx[a],sy[a],sx[b],sy[b],sx[c],sy[c],color)
  index=f[11]
  end
 end
 --profile_ui
 fillp()
 rectfill(4,view_h-4,view_w-5,view_h-4,1)
 rectfill(4,view_h-4,4+elapsed*(view_w-9)/60,view_h-4,12)
 if ended then print("press x to replay",view_cx-32,view_h-12,7) end
 drawn=count
 -- Native FPS and VM CPU budget, sampled after rendering, before the monitor.
 local cpu=flr(stat(1)*(monitor_cpu_scale or 1)*1000+.5)
 print("fps:"..(monitor_fps or stat(7)),4,4,12)
 print("cpu:"..flr(cpu/10).."."..(cpu%10).."%",view_w-44,4,6)
end

function advance_motion()
 physics_acc+=min(.1,max(0,elapsed-last_elapsed)) last_elapsed=elapsed
 local steps=min(6,flr(physics_acc*60)) physics_acc-=steps/60
 pose(min(elapsed,59.999)) bind_helpers() physics(steps) skin()
 if elapsed>=60 then ended=true end
 frames+=1
end

function _init()
 ready=false progress=0
 if player_host then player_host.init()
 else
  pal(10,131,1)
  if peek(0x4300)!=77 or peek(0x4301)!=75 or peek(0x4302)!=3 then
   load("miku_a.p8") return
  end
 end
 music(-1)
 load_model()
 checks={}
 chunk=1 buffer=0x8000
 ready=false elapsed=0 underflows=0 frames=0 progress=0 cartridge_reads=0
 physics_acc=0 last_elapsed=0
 work=cocreate(preload_motion)
end

function _update()
 if player_host then return player_host.update() end
 local command=peek(0x5f9f)
 if command!=0 then
  poke(0x5f9f,0)
  if command==1 then extcmd("rec") end
  if command==2 then extcmd("video",4) end
  if command==3 then extcmd("label") end
 end
 if not work then return end
 if not ready then
  if prepared then
   ready=true audio_started=false music(0)
  else
   tick_work()
   if not work then return end
   if costatus(work)=="dead" then prepared=true end
   return
  end
 end
 if btnp(5) then
  music(-1) chunk=1 buffer=0x8000 ended=false physics_ready=false
  elapsed=0 last_elapsed=0 physics_acc=0 audio_started=false
  ready=false prepared=false copy_data=nil
  work=cocreate(function() copy_pose(1,0x8000,true) copy_pose(2,0xc000,true) end)
  return
 end
 step_copy()
 elapsed=audio_time()
 local wanted=min(15,flr(elapsed/4)+1)
 if wanted>chunk then
  if copy_data then
   while copy_data do step_copy() end
  end
  if wanted==chunk+1 then buffer=buffer==0x8000 and 0xc000 or 0x8000
  else buffer=0x8000 copy_pose(wanted,buffer) end
  chunk=wanted
  if chunk<15 then queue_pose(chunk+1,buffer==0x8000 and 0xc000 or 0x8000) end
 end
 advance_motion()
 poke(0x5f80,77,75,ready and 1 or 0,chunk,frames&255,(frames>>8)&255,underflows,ended and 1 or 0)
 poke(0x5f88,flr(elapsed),mid(0,flr(stat(1)*100),255),drawn or 0)
 if bbox then
  for i=1,4 do poke2(0x5f8e+i*2,flr(bbox[i])) end
 end
 for i=0,3 do poke(0x5f98+i,stat(46+i)) end
 poke(0x5f9c,stat(7)) poke2(0x5fe0,stat(0))
 poke(0x5fe2,stat(54),stat(57) and 1 or 0) poke2(0x5fe4,stat(56))
 for i=1,15 do
  local c=checks[i]
  if c then poke(0x5fa0+i*2,c[1],c[2]) end
 end
end

function _draw()
 if player_host then player_host.before_draw() end
 if ready then scene()
 else
  cls(0) print("miku / preparing",view_cx-34,view_cy-8,12)
  rectfill(view_cx-40,view_cy+6,view_cx+39,view_cy+6,1)
  rectfill(view_cx-40,view_cy+6,view_cx-40+progress*79,view_cy+6,12)
  if failure then print(failure,0,view_cy+16,8) end
 end
 if player_host then player_host.after_draw()
 else poke(0x5f9d,cartridge_reads or 0,min(255,stat(1)*100)) end
end
