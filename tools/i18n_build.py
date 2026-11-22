"""Genere les dictionnaires web/static/i18n/{en,ar,ja}.js a partir de tools/i18n_source.py :  python tools/i18n_build.py"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")
from i18n_source import ENTRIES
from i18n_source_more import ENTRIES2
ENTRIES = ENTRIES + ENTRIES2

OUT = os.path.join(HERE, "..", "web", "static", "i18n")
os.makedirs(OUT, exist_ok=True)
seen = {}
for fr, *rest in ENTRIES:
    if len(rest) != 3 or not all(rest):
        sys.exit(f"Entree incomplete : {fr!r}")
    if fr in seen:
        print("doublon ignore :", fr)
        continue
    seen[fr] = rest
for i, code in enumerate(("en", "ar", "ja")):
    d = {fr: tr[i] for fr, tr in seen.items()}
    with open(os.path.join(OUT, code + ".js"), "w", encoding="utf-8") as f:
        f.write("/* Genere par tools/i18n_build.py : ne pas modifier a la main */\nwindow.KW_DICT = " + json.dumps(d, ensure_ascii=False, indent=0) + ";\n")
    print(code, len(d), "textes")
