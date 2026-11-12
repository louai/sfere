# Offline audio probe: band power, spectral-flux onsets, tempo (onset autocorrelation) and beat grid.
# Uses the same FFT size/hop as the planned analyser (FFT 2048, hop 512). Usage: python3 -I tools/probe_audio.py <audio>
import sys, subprocess, numpy as np
SR=44100
x=np.frombuffer(subprocess.run(["ffmpeg","-v","error","-i",sys.argv[1],"-ac","1","-ar",str(SR),"-f","f32le","-"],capture_output=True).stdout,np.float32)
N,HOP=2048,512; win=np.hanning(N); n=(len(x)-N)//HOP
P=np.array([np.abs(np.fft.rfft(x[i*HOP:i*HOP+N]*win))**2 for i in range(n)])
f=np.fft.rfftfreq(N,1/SR); t=np.arange(n)*HOP/SR; fps=SR/HOP
bands={"sub 20-60":(20,60),"bass 60-250":(60,250),"lowmid 250-500":(250,500),"mid 500-2k":(500,2000),"high 2k-8k":(2000,8000),"air 8k+":(8000,22050)}
tot=P.sum()
for k,(a,b) in bands.items(): print(f"{k:16s} {100*P[:,(f>=a)&(f<b)].sum()/tot:5.1f}% of power")
print("spectral centroid median %.0f Hz"%np.median((P*f).sum(1)/(P.sum(1)+1e-12)))
L=np.log1p(1e4*P); flux=np.r_[0,np.maximum(np.diff(L,axis=0),0).sum(1)]
flux=(flux-flux.mean())/flux.std()
# tempo from autocorrelation of onset envelope, fine resolution
ac=np.correlate(flux,flux,"full")[n-1:]; ac/=ac[0]
best=None
for bpm in np.arange(70,181,0.5):
    lag=fps*60/bpm; i=int(lag); fr=lag-i
    v=ac[i]*(1-fr)+ac[i+1]*fr
    if best is None or v>best[1]: best=(bpm,v)
print("tempo from autocorrelation: %.1f BPM (score %.2f)"%best)
for bpm in (68,136,150,102):
    lag=fps*60/bpm;i=int(lag);print(f"  ac at {bpm} BPM: {ac[i]:.2f}")
# beat grid phase: choose phase maximizing summed flux on grid
bpm=best[0]; per=fps*60/bpm
ph=max(np.arange(0,per,0.25),key=lambda p: sum(flux[int(round(p+k*per))] for k in range(int((n-p)/per))))
beats=[(ph+k*per)/fps for k in range(int((n-ph)/per))]
print("beat grid (s):",np.round(beats,2))
# how many strong onsets land within 50 ms of the grid
pk=[i for i in range(3,n-3) if flux[i]>1.5 and flux[i]==flux[i-3:i+4].max()]
on=np.array(pk)/fps; near=[min(abs(o-b) for b in beats)<0.05 for o in on]
print(f"strong onsets {len(on)}, within 50 ms of beat grid: {sum(near)}")
print("strong onset times:",np.round(on,2))
