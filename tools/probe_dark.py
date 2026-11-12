# Offline probe for low-exposure footage: plain red threshold, reports detection, ball color, miss runs.
# Usage: python3 -I tools/probe_dark.py <video.mp4>
import sys, subprocess, numpy as np
W,H=320,180
p=subprocess.Popen(["ffmpeg","-v","error","-i",sys.argv[1],"-vf",f"scale={W}:{H}","-f","rawvideo","-pix_fmt","rgb24","-"],stdout=subprocess.PIPE)
n=0; det=0; misses=[]; areas=[]; prev=None; jumps=[]; bg=[]; cols=[]
while True:
    b=p.stdout.read(W*H*3)
    if len(b)<W*H*3: break
    f=np.frombuffer(b,np.uint8).reshape(H,W,3).astype(np.int32)
    r,g,bl=f[...,0],f[...,1],f[...,2]
    lit=(r>60)&(r>2*g)&(r>1.5*bl)
    bg.append(np.percentile(f.max(-1),99))
    a=lit.sum()
    if a>=3:
        ys,xs=np.nonzero(lit); x,y=xs.mean(),ys.mean(); det+=1; areas.append(a)
        cols.append(f[lit].mean(0))
        spread=max(xs.std(),ys.std())
        if prev is not None and abs(x-prev[0])+abs(y-prev[1])>60: jumps.append((n,int(x),int(y)))
        prev=(x,y)
    else: misses.append(n)
    n+=1
print("frames",n,"detected",det)
print("area px (320w) min/med/max",min(areas),int(np.median(areas)),max(areas))
c=np.array(cols); print("mean ball rgb",c.mean(0).round(),"min",c.min(0).round(),"max",c.max(0).round())
print("99th pct brightness of frame (median over frames)",np.median(bg))
# group misses into runs
runs=[];
for m in misses:
    if runs and m==runs[-1][1]+1: runs[-1][1]=m
    else: runs.append([m,m])
print("miss runs",runs)
print("jumps",jumps[:20])
