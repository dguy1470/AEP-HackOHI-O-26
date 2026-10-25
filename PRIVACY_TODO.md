# Privacy follow-up for whoever owns the raw data

## The issue
`app_data.json` currently stores premise coordinates at full precision
(`round(float(r.x), 6)` → ~0.1 m), which pins each customer — including
life-support/critical ones — to an exact house.

The frontend now **coarsens coordinates to ~300 m at runtime** (see
`privacyCoarsen()` in `template.html`/`index.html`), so the map no longer shows
exact homes. But the precise coordinates still exist inside `app_data.json`.

## The real fix (needs the raw challenge data)
In `prep.py`, change the coordinate rounding when building the rows and damage
points so exact locations are never written to the file:

```python
# before:
round(float(r.x), 6), round(float(r.y), 6)
# after (~300 m grid; adjust the 3 if you want coarser/finer):
round(float(r.x), 3), round(float(r.y), 3)
```
Do the same for the `dmg` list (the historical damage dots).

Then regenerate:
```bash
pip install pyshp pandas numpy scipy scikit-learn
python prep.py && python build.py
```

This makes the README's "coordinates rounded" claim actually true at the data
level, so no precise location ever leaves the pipeline. Requires `Data1/`,
`Data2/`, and `matched_upstream/matched_upstream.txt` next to `prep.py`.

## Also worth confirming
- `critical_c` (the life-support flag) is a declared status already in the
  data — the app reads it, never infers it. Keep it that way.
- No names, addresses, phone numbers, or account numbers are in `app_data.json`
  (verified) — only a hashed ID, coordinates, and model features.
