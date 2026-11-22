"""Convertit un modele XGBoost enregistre avec pickle en fichier JSON standard, SANS executer le pickle.

Pourquoi : un pickle XGBoost est fragile (il depend de la version de XGBoost et execute du code a la lecture).
Ce script lit seulement les octets du modele (tampon UBJSON) et ecrit un modele JSON que `xgboost.Booster().load_model()` sait relire.

  python tools/convert_xgb_pickle.py models/original/kizuna_watt_model.pkl models/kizuna_watt_xgb.json

Conseil pour l'equipe : a l'avenir, enregistrer avec  model.save_model("modele.json")  plutot qu'avec pickle.
Bibliotheque standard uniquement.
"""
import json
import pickletools
import struct
import sys


class UBJ:
    """Lecteur minimal de UBJSON (format binaire utilise par XGBoost)."""
    NUM = {b"i": ">b", b"U": ">B", b"I": ">h", b"l": ">i", b"L": ">q", b"d": ">f", b"D": ">d"}

    def __init__(self, b):
        self.b, self.i = b, 0

    def peek(self):
        return self.b[self.i:self.i + 1]

    def take(self, n):
        v = self.b[self.i:self.i + n]
        if len(v) < n:
            raise EOFError(f"fin de fichier inattendue (offset {self.i})")
        self.i += n
        return v

    def num(self, t):
        f = self.NUM[t]
        return struct.unpack(f, self.take(struct.calcsize(f)))[0]

    def length(self):
        return self.num(self.take(1))

    def value(self, t=None):
        t = t or self.take(1)
        if t == b"Z":
            return None
        if t == b"T":
            return True
        if t == b"F":
            return False
        if t in self.NUM:
            return self.num(t)
        if t == b"C":
            return self.take(1).decode()
        if t in (b"S", b"H"):
            return self.take(self.length()).decode("utf-8")
        if t in (b"[", b"{"):
            et = n = None
            if self.peek() == b"$":
                self.i += 1
                et = self.take(1)
            if self.peek() == b"#":
                self.i += 1
                n = self.length()
            if t == b"[":
                if n is not None:
                    return [self.value(et) for _ in range(n)]
                out = []
                while self.peek() != b"]":
                    out.append(self.value())
                self.i += 1
                return out
            out = {}
            if n is not None:
                for _ in range(n):
                    k = self.take(self.length()).decode()
                    out[k] = self.value(et)
                return out
            while self.peek() != b"}":
                k = self.take(self.length()).decode()
                out[k] = self.value()
            self.i += 1
            return out
        raise ValueError(f"marqueur UBJSON inconnu {t!r} (offset {self.i - 1})")


def booster_bytes(path):
    best = None
    for op, arg, _ in pickletools.genops(open(path, "rb").read()):
        if op.name in ("BYTEARRAY8", "BINBYTES", "BINBYTES8") and (best is None or len(arg) > len(best)):
            best = bytes(arg)
    if not best:
        sys.exit("Aucun modele XGBoost trouve dans ce fichier.")
    return best


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    tree = UBJ(booster_bytes(sys.argv[1])).value()
    json.dump(tree["Model"], open(sys.argv[2], "w"), separators=(",", ":"))
    n = len(tree["Model"]["learner"]["gradient_booster"]["model"]["trees"])
    print(f"Modele converti : {n} arbres, variables : {len(tree['Model']['learner']['feature_names'])} -> {sys.argv[2]}")
