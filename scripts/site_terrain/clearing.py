from pathlib import Path
import json, sys, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_terrain as st
from scipy import ndimage
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
TITLES = {"bow": "Bow Summit", "goat": "Goat's Eye", "simpson": "Simpson", "parker": "Parker Ridge"}
fig, axs = plt.subplots(2, 2, figsize=(11, 12.6), dpi=200, gridspec_kw={"hspace": 0.32})
res = {}
for ax, name in zip(axs.ravel(), st.PLOTS):
    h = np.nan_to_num(np.load(st.OUT / f"{name}_meta_chm1m.npy"), nan=0.0)
    n = h.shape[0]; yy, xx = np.mgrid[0:n, 0:n]
    dy, dx = (n / 2 - 0.5 - yy), (xx - n / 2 + 0.5); dist = np.hypot(dy, dx)
    open_ = h < 2.0
    edt = ndimage.distance_transform_edt(open_)  # metres to the nearest tree
    # the most open spot within 60 m of the marker
    cand = np.where(dist <= 60, edt, -1); i = np.unravel_index(cand.argmax(), cand.shape)
    lab, k = ndimage.label(open_)
    here = lab[n // 2, n // 2]
    res[name] = {"marker_m_to_nearest_tree": round(float(edt[n // 2, n // 2]), 1),
                 "marker_in_opening_of_m2": int((lab == here).sum()) if here else 0,
                 "most_open_spot_within_60m": {"east_m": float(dx[i]), "north_m": float(dy[i]), "m_to_nearest_tree": round(float(edt[i]), 1),
                                               "opening_m2": int((lab == lab[i]).sum())}}
    im = ax.imshow(h, extent=(-n / 2, n / 2, -n / 2, n / 2), cmap="YlGn", vmin=0, vmax=20)
    ax.plot(0, 0, marker="+", color="#d6336c", ms=18, mew=2.5)
    ax.plot(dx[i], dy[i], marker="o", mfc="none", color="#1c7ed6", ms=12, mew=2)
    for r_ in (15, 60):
        t = np.linspace(0, 2 * np.pi, 200); ax.plot(r_ * np.cos(t), r_ * np.sin(t), color="#d6336c", lw=0.8, ls="--")
    ax.set_title(f"{TITLES[name]}", fontsize=13); ax.set_xlabel("metres east of the marker"); ax.set_ylabel("metres north of the marker")
    ax.set_xlim(-100, 100); ax.set_ylim(-100, 100); ax.set_aspect("equal")
cb = fig.colorbar(im, ax=axs, shrink=0.6, location="bottom", pad=0.06); cb.set_label("estimated tree height (m); white = open ground")
fig.suptitle("Tree height around each plot marker (pink cross = the coordinates on file; rings at 15 m and 60 m;\nblue circle = the most open spot within 60 m). North is up.", fontsize=12)

fig.savefig(st.OUT / "tree-height-around-plots.png", bbox_inches="tight")
print(json.dumps(res, indent=1))
