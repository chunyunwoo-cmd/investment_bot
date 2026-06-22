# -*- coding: utf-8 -*-
import json
with open('beta_cache.json', encoding='utf-8') as f:
    d = json.load(f)
print(f"캐시 업데이트: {d.get('_ts', '?')}")
print()
for k, v in d.items():
    if k != '_ts':
        print(f"{v['name']}: SOXL β={v['soxl_beta']:.3f}(r={v['soxl_corr']:.2f}) | KORU β={v['koru_beta']:.3f}(r={v['koru_corr']:.2f})")
