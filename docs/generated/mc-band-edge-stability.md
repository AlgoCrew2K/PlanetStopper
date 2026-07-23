# MC Band-Edge Stability Probe

Boundary: 5.0% | Near-edge true prob (closed-form): 5.30% | n_seeds: 300

## Flip-rate: 300-path replay vs each reference (fraction of 300 independent seeded runs whose arm-side disagrees)

| reference paths | flip-rate |
|---|---|
| 1000 | 0.4800 |
| 5000 | 0.3967 |
| 20000 | 0.3533 |

**Decision threshold:** 5.00% ([PM-ASSUMED] — see feature-plans/math-r3a-checklist.md)
**Decision reference (production parity):** 5000 paths
**Recommendation:** BUMP

## Evidence-based bump target

**No candidate path count up to and including production parity (5000) achieved a flip-rate below 5%** vs the parity reference itself. The instability at this offset is dominated by proximity to the boundary, not reducible by more paths alone. No constant change is made — see the candidates-tried table below.

### Candidates tried (vs 5000-path production parity)

| candidate paths | flip-rate vs 5000 |
|---|---|
| 400 | 0.3933 |
| 500 | 0.4200 |
| 600 | 0.3667 |
| 750 | 0.4233 |
| 1000 | 0.3800 |
| 1250 | 0.3867 |
| 1500 | 0.2867 |
| 2000 | 0.3333 |
| 2500 | 0.2933 |
| 3000 | 0.3367 |
| 4000 | 0.3233 |
| 5000 | 0.2800 |
