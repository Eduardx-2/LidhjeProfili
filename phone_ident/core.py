from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import re


SYSTEMS = ("ANDROID", "IPHONE")


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError("make/model deben contener str o None")
    return " ".join(value.split()).casefold()


def identify_device(make, model):

    make, model = _text(make), _text(model)
    camera = (
        (make in {"canon", "canon inc."} and model.startswith(("canon ", "eos", "powershot")))
        or (make in {"nikon", "nikon corporation"} and model.startswith(("nikon ", "coolpix", "d", "z")))
        or (make == "sony" and model.startswith(("ilce-", "ilca-", "dsc-", "nex-", "slt-")))
        or (make == "fujifilm" and model.startswith(("x-", "gfx", "finepix")))
        or (make == "panasonic" and model.startswith(("dc-", "dmc-")))
        or (make == "gopro" and model.startswith("hero"))
    )
    iphone = re.match(r"^iphone(?:\s|$)", model) is not None
    android = (
        (make in {"samsung", "samsung electronics"} and model.startswith(("sm-", "galaxy ")))
        or (make in {"google", "google inc."} and model.startswith("pixel"))
        or (make in {"motorola", "motorola mobility"} and model.startswith("moto "))
        or (make == "sony" and model.startswith("xperia"))
    )

    if iphone and make not in {"", "apple", "apple inc."}:
        return {"kind": "UNKNOWN", "os": None, "model": model, "specific": False}
    if camera:
        return {"kind": "CAMERA", "os": None, "model": model, "specific": True}
    if iphone or android:
        return {"kind": "PHONE", "os": "IPHONE" if iphone else "ANDROID",
                "model": model, "specific": model not in {"iphone", "pixel", "galaxy", "xperia"}}
    return {"kind": "UNKNOWN", "os": None, "model": model, "specific": False}


def _distribution(values, keys, name):
    if not isinstance(values, dict) or set(values) != set(keys):
        raise ValueError(f"{name} debe tener exactamente las claves {keys}")
    try:
        result = {k: float(values[k]) for k in keys}
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name}: valores numéricos requeridos") from exc
    if any(not math.isfinite(v) or v <= 0 for v in result.values()):
        raise ValueError(f"{name}: valores finitos y mayores que cero requeridos")
    if not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{name} debe sumar 1")
    return result


@dataclass
class BayesModel:
    likelihoods: dict
    priors: dict | None = None

    def __post_init__(self):
        self.priors = _distribution(
            self.priors if self.priors is not None else {s: 0.5 for s in SYSTEMS},
            SYSTEMS, "priors")
        if set(self.likelihoods) != set(SYSTEMS):
            raise ValueError("likelihoods requiere las dos hipótesis ANDROID/IPHONE")
        self.likelihoods = {
            h: _distribution(self.likelihoods[h], SYSTEMS, f"likelihoods[{h}]")
            for h in SYSTEMS
        }

    @classmethod
    def fit(cls, samples, alpha=1.0):
        """Aprende de perfiles etiquetados: {label, make, model}. Suavizado de Laplace."""
        if not math.isfinite(alpha) or alpha <= 0:
            raise ValueError("alpha debe ser finito y positivo")
        profiles = Counter()
        counts = {s: Counter() for s in SYSTEMS}
        for sample in samples:
            label = sample["label"]
            if label not in SYSTEMS:
                raise ValueError("label debe ser ANDROID o IPHONE")
            result = PhoneData(sample["make"], sample["model"]).identify_checkfon()
            profiles[label] += 1
            counts[label].update(result["phone"]["counts"])
        if any(not profiles[s] or not sum(counts[s].values()) for s in SYSTEMS):
            raise ValueError("Se necesitan perfiles con evidencia telefónica para ambas clases")
        priors = {s: (profiles[s] + alpha) / (sum(profiles.values()) + 2 * alpha) for s in SYSTEMS}
        likelihoods = {
            h: {s: (counts[h][s] + alpha) / (sum(counts[h].values()) + 2 * alpha)
                for s in SYSTEMS} for h in SYSTEMS
        }
        return cls(likelihoods, priors)

    def posterior(self, counts):
        """log P(H) + sum_e n(e) log P(e|H); normalización estable."""
        if any(k not in SYSTEMS for k in counts) or any(
            not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in counts.values()
        ):
            raise ValueError("counts requiere conteos enteros no negativos por SO")
        logs = {h: math.log(self.priors[h]) + sum(
            counts.get(s, 0) * math.log(self.likelihoods[h][s]) for s in SYSTEMS
        ) for h in SYSTEMS}
        maximum = max(logs.values())
        values = {h: math.exp(logs[h] - maximum) for h in SYSTEMS}
        total = sum(values.values())
        return {h: values[h] / total for h in SYSTEMS}


def _day(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("Fecha no finita")
        # El JSON de VSCO usa milisegundos Unix, no segundos.
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    if isinstance(value, str):
        return date.fromisoformat(value).isoformat()
    raise TypeError("dates admite fechas ISO, date/datetime, milisegundos Unix o None")


def identify_phone(make: list, model: list, *, dates=None,
                   weights=(0.5, 0.3, 0.2), bayes=None):
    """Recibe las dos listas alineadas y devuelve la identificación completa."""
    return PhoneData(make, model, dates=dates, weights=weights,
                     bayes=bayes).identify_checkfon()


class PhoneData:
    def __init__(self, make: list, model: list, *, dates=None,
                 weights=(0.5, 0.3, 0.2), bayes=None):
        if not isinstance(make, list) or not isinstance(model, list):
            raise TypeError("make y model deben ser listas")
        if len(make) != len(model):
            raise ValueError("make y model deben tener igual longitud y corresponder por índice")
        if dates is not None and len(dates) != len(make):
            raise ValueError("dates debe tener la misma longitud que make/model")
        weights = tuple(float(w) for w in weights)
        if len(weights) != 3 or any(not math.isfinite(w) or w < 0 for w in weights) or not sum(weights):
            raise ValueError("weights requiere tres pesos finitos no negativos, con suma positiva")
        self.make, self.model, self.dates = make, model, dates
        self.weights = tuple(w / sum(weights) for w in weights)
        self.bayes = bayes

    def identify_checkfon(self):
        counts, cameras, unknown = Counter(), Counter(), Counter()
        candidates = {}
        days = {s: set() for s in SYSTEMS}
        dated = 0
        for i, (make, model) in enumerate(zip(self.make, self.model)):
            device = identify_device(make, model)
            day = _day(self.dates[i]) if self.dates is not None else None
            if device["kind"] == "CAMERA":
                cameras[(_text(make), device["model"])] += 1
            elif device["kind"] == "UNKNOWN":
                unknown[(_text(make), device["model"])] += 1
            else:
                system = device["os"]
                counts[system] += 1
                if day is not None:
                    days[system].add(day)
                    dated += 1
                key = (system, device["model"])
                candidate = candidates.setdefault(key, {"count": 0, "days": set(), "specific": 0})
                candidate["count"] += 1
                candidate["specific"] += int(device["specific"])
                if day is not None:
                    candidate["days"].add(day)

        total = sum(counts.values())
        all_days = days["ANDROID"] | days["IPHONE"]
        # Datos temporales parciales sesgarían la comparación: se desactiva T.
        temporal = bool(total and dated == total)
        active = (self.weights[0], self.weights[1] if temporal else 0, self.weights[2])
        denominator = sum(active)
        effective = tuple(w / denominator for w in active) if denominator else (0, 0, 0)

        def score(count, specific_count, observed_days):
            c = count / total if total else 0.0
            t = len(observed_days) / len(all_days) if temporal else None
            d = specific_count / count if count else 0.0
            value = sum(w * v for w, v in zip(effective, (c, t or 0, d)))
            return {"C": c, "T": t, "D": d, "score": value if total and denominator else None}

        models = [{"os": s, "model": m, "count": item["count"],
                   **score(item["count"], item["specific"], item["days"])}
                  for (s, m), item in candidates.items()]
        # Un máximo lineal conserva empates; no ordenar todas las observaciones.
        best = max((item["score"] for item in models if item["score"] is not None), default=None)
        preferred = [item["model"] for item in models if best is not None and item["score"] == best]
        return {
            "camera": {"count": sum(cameras.values()), "models": [
                {"make": a, "model": b, "count": c} for (a, b), c in cameras.items()]},
            "phone": {"count": total, "counts": {s: counts[s] for s in SYSTEMS},
                      "models": models, "preferred_models": preferred,
                      "probability": self.bayes.posterior(counts) if self.bayes else None,
                      "probability_status": ("conditional_on_assumptions" if total else "prior_only") if self.bayes else "not_configured"},
            "unknown": {"count": sum(unknown.values()), "models": [
                {"make": a, "model": b, "count": c} for (a, b), c in unknown.items()]},
            "weights": dict(zip(("C", "T", "D"), effective)),
            "temporal": {"available": temporal, "dated_phone_images": dated, "distinct_days": len(all_days)},
        }
