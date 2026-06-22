# -*- coding: utf-8 -*-
import sys, os, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with open('config.yaml', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)
import main
main.pre_market_brief(cfg)
print("완료", flush=True)
