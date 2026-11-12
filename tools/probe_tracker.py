# Offline feasibility probe: runs a core+halo color tracker over a video.
# Usage: python3 tools/probe_tracker.py <video.mp4> <contact_sheet_out.png>
import sys, subprocess, numpy as np, zlib, struct
W,H=320,180
def png(path,a):
    h,w=a.shape[:2]; raw=b''.join(b'\0'+a[y].tobytes() for y in range(h))
    def ch(t,d): return struct.pack('>I',len(d))+t+d+struct.pack('>I',zlib.crc32(t+d))
    open(path,'wb').write(b'\x89PNG\r\n\x1a\n'+ch(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+ch(b'IDAT',zlib.compress(raw))+ch(b'IEND',b''))
def boxblur(m,k):
    c=np.cumsum(np.cumsum(np.pad(m,((k+1,k),(k+1,k))),0),1)
    return c[2*k+1:,2*k+1:]-c[:-2*k-1,2*k+1:]-c[2*k+1:,:-2*k-1]+c[:-2*k-1,:-2*k-1]
p=subprocess.Popen(["ffmpeg","-v","error","-i",sys.argv[1],"-vf",f"scale={W}:{H}","-f","rawvideo","-pix_fmt","rgb24","-"],stdout=subprocess.PIPE)
n=0; hits=0; traj=[]; snaps=[]; prev=None
while True:
    b=p.stdout.read(W*H*3)
    if len(b)<W*H*3: break
    f=np.frombuffer(b,np.uint8).reshape(H,W,3).astype(np.float32)
    r,g,bl=f[...,0],f[...,1],f[...,2]
    mx=f.max(-1); sat=(mx-f.min(-1))/(mx+1e-3)
    red=((r>170)&(sat>0.55)&(r>g*2)).astype(np.float32)
    core=(f.min(-1)>230).astype(np.float32)
    # score: red density in neighborhood, boosted where white core nearby
    s=boxblur(red,4)+1.5*boxblur(core,3)*(boxblur(red,6)>3)
    y,x=np.unravel_index(np.argmax(s),s.shape); v=s[y,x]
    ok=v>12
    if ok: hits+=1
    traj.append((n,x,y,round(float(v),1),ok))
    if n%45==0:
        vis=f.astype(np.uint8).copy()
        if ok: vis[max(0,y-6):y+7,max(0,x-6):x+7:12]=[0,255,0]; vis[max(0,y-6):y+7:12,max(0,x-6):x+7]=[0,255,0]
        snaps.append(vis)
    n+=1
print("frames",n,"detected",hits)
jumps=[(a[0],a[1],a[2],b[1],b[2]) for a,b in zip(traj,traj[1:]) if a[4] and b[4] and abs(a[1]-b[1])+abs(a[2]-b[2])>60]
print("big jumps",len(jumps),jumps[:15])
miss=[t[0] for t in traj if not t[4]]; print("missed frames",miss[:60])
rows=[np.concatenate(snaps[i:i+3],1) for i in range(0,len(snaps)-len(snaps)%3,3)]
png(sys.argv[2],np.concatenate(rows,0))
