import numpy as np

FS = 48828.125
z = np.load("/home/xilinx/dma_exp/loopback_caps.npz")
for k in sorted(z.files):
    if not (k.endswith("_in") or k.endswith("_out")):
        continue
    x = z[k].astype(float)
    x = x - np.median(x)
    w = np.hanning(len(x))
    X = np.abs(np.fft.rfft(x * w))
    f = np.fft.rfftfreq(len(x), d=1.0 / FS)
    idx = np.argsort(X)[-4:][::-1]
    top = ", ".join("%.0fHz=%d" % (f[i], X[i]) for i in idx)
    print("%-16s peak=%6d  %s" % (k, int(np.max(np.abs(x))), top))
