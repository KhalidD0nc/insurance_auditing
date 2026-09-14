from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, ROUND_HALF_UP

from .models import ContractRules, ServiceMappingOverride, ServiceMatch


_ALIASES = {
    "adv": "advanced", "amb": "ambulatory", "asst": "assisted",
    "beds": "bedside", "compr": "comprehensive", "cont": "continuous",
    "elect": "elective", "emer": "emergency", "emerg": "emergency",
    "ext": "extended", "foc": "focused", "intens": "intensive",
    "interm": "intermittent", "inpt": "inpatient", "outpt": "outpatient",
    "postop": "postoperative", "preop": "preoperative", "rtn": "routine",
    "spclst": "specialist", "spec": "specialist", "std": "standard",
    "supv": "supervised", "sup": "supervised",
    "card": "cardiac", "derm": "dermatologic", "endo": "endocrine",
    "ent": "otolaryngologic", "ger": "geriatric", "gi": "gastrointestinal",
    "haem": "haematology", "hep": "hepatic", "immun": "immunologic",
    "infect": "infectious", "metab": "metabolic", "msk": "musculoskeletal",
    "neuro": "neurological", "obst": "obstetric", "onc": "oncology",
    "ophth": "ophthalmic", "ortho": "orthopaedic", "paed": "paediatric",
    "pall": "palliative", "pulm": "pulmonary", "psych": "psychiatric",
    "ren": "renal", "rheum": "rheumatologic", "urol": "urologic",
    "vasc": "vascular",
    "admin": "administration", "anaes": "anaesthesia", "anly": "analysis",
    "bd": "bed",
    "biop": "biopsy", "conf": "conference", "consult": "consultation",
    "cr": "care", "crit": "critical", "cs": "case", "diag": "diagnostic", "dial": "dialysis",
    "disch": "discharge", "disp": "dispensing", "endosc": "endoscopic",
    "fract": "fraction", "hm": "home", "img": "imaging",
    "inf": "infusion", "interp": "interpretation", "isol": "isolation",
    "lab": "laboratory", "monit": "monitoring", "nurs": "nursing",
    "nutr": "nutritional", "obs": "observation", "occ": "occupancy",
    "physio": "physiotherapy", "pnl": "panel", "proc": "procedure", "prog": "programme",
    "radiother": "radiotherapy", "recov": "recovery", "rehab": "rehabilitation",
    "rm": "room", "sess": "session", "spcm": "specimen",
    "ster": "sterilisation", "steril": "sterilisation", "supp": "support",
    "svc": "service", "telem": "telemetry", "ther": "therapy",
    "thtr": "theatre", "tm": "time", "transf": "transfusion",
    "transp": "transport", "vent": "ventilation", "vst": "visit",
    "wd": "ward", "wnd": "wound",
}


def normalise_service_description(value: str) -> str:
    value = re.sub(r"/[a-z]+-\d+", " ", value.lower())
    tokens = re.sub(r"[^a-z]+", " ", value).split()
    return " ".join(_ALIASES.get(token, token) for token in tokens)


def normalise_service_tokens(value: str) -> frozenset[str]:
    return frozenset(normalise_service_description(value).split())


class ServiceMatcher:
    def __init__(
        self,
        contract: ContractRules,
        mappings: Mapping[str, ServiceMappingOverride] | None = None,
        *,
        minimum_margin: float = 0.0,
        allow_price_tiebreaker: bool = True,
    ) -> None:
        self._contract = contract
        self._minimum_margin = minimum_margin
        self._allow_price_tiebreaker = allow_price_tiebreaker
        self._mappings = dict(mappings or {})
        unknown_services = {
            mapping.service_name for mapping in self._mappings.values()
        } - contract.services.keys()
        if unknown_services:
            raise ValueError(
                f"service mappings reference unknown services: {sorted(unknown_services)}"
            )
        self._tokens = {
            service: normalise_service_tokens(service) for service in contract.services
        }
        self._plausible_rates = {
            service: self._rates_for_service(service) for service in contract.services
        }

    def match(self, description: str, unit_basis: str, unit_price_cents: int) -> ServiceMatch:
        mapping_key = normalise_service_description(description)
        override = self._mappings.get(mapping_key)
        if override is not None:
            return ServiceMatch(
                override.service_name,
                1.0,
                1.0,
                source="llm_mapping",
                key=mapping_key,
                confidence=override.confidence,
            )

        description_tokens = normalise_service_tokens(description)
        ranked = self.rank_candidates(description)
        best_score, best_service = ranked[0]
        second_score = ranked[1][0]
        margin = best_score - second_score

        if (
            description_tokens == self._tokens[best_service]
            and margin >= self._minimum_margin
        ):
            return ServiceMatch(best_service, best_score, margin, key=mapping_key)

        # A billed rate is only used to break a textual tie. It never overrides a
        # clear description, which prevents a corrupted rate from changing identity.
        if self._allow_price_tiebreaker and margin >= self._minimum_margin:
            tied = [service for score, service in ranked if best_score - score <= 0.01]
            price_matches = [
                service for service in tied if unit_price_cents in self._plausible_rates[service]
            ]
            if len(price_matches) == 1:
                return ServiceMatch(
                    price_matches[0], best_score, margin, key=mapping_key
                )

        service_words = best_service.split()
        identity_tokens = normalise_service_tokens(" ".join(service_words[:2]))
        is_safe_abbreviation = (
            description_tokens <= self._tokens[best_service]
            and identity_tokens <= description_tokens
            and best_score >= 0.60
            and margin >= max(0.15, self._minimum_margin)
        )
        return ServiceMatch(
            best_service if is_safe_abbreviation else None,
            best_score,
            margin,
            key=mapping_key,
        )

    def rank_candidates(
        self,
        description: str,
        *,
        limit: int | None = None,
    ) -> list[tuple[float, str]]:
        description_tokens = normalise_service_tokens(description)
        ranked: list[tuple[float, str]] = []
        for service_name, service_tokens in self._tokens.items():
            union = description_tokens | service_tokens
            text_score = (
                len(description_tokens & service_tokens) / len(union)
                if union
                else 0.0
            )
            ranked.append((text_score, service_name))
        ranked.sort(reverse=True)
        return ranked[:limit]

    def _rates_for_service(self, service_name: str) -> frozenset[int]:
        base_rates = {self._contract.services[service_name].rate_cents}
        for bundle in self._contract.bundles:
            if bundle.service_a == service_name:
                base_rates.add(bundle.rate_a_cents)
            if bundle.service_b == service_name:
                base_rates.add(bundle.rate_b_cents)

        premiums = {Decimal("1")}
        threshold = self._contract.threshold_premiums.get(service_name)
        if threshold:
            premiums.add(threshold.multiplier)
        weekend = self._contract.non_business_day_uplifts.get(service_name)
        if weekend:
            premiums.add(weekend)

        discounts = {Decimal("1")}
        discounts.update(
            rule.multiplier for rule in self._contract.volume_discounts.get(service_name, ())
        )
        rates = set()
        for base_rate in base_rates:
            for premium in premiums:
                after_premium = int(
                    (Decimal(base_rate) * premium).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
                for discount in discounts:
                    rates.add(
                        int(
                            (Decimal(after_premium) * discount).quantize(
                                Decimal("1"), rounding=ROUND_HALF_UP
                            )
                        )
                    )
        return frozenset(rates)
