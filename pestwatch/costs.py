"""Running cost of PestWatch for one region-season, and benefit-cost ratio.

Unit prices are indicative assumptions (CostConfig); replace with local quotes.
"""
from typing import Dict

import numpy as np

from .farmers import generate_profiles
from .simulation import LEAD_FARMER, PHOTO, SimResult

UNIT = {"app": "app_push", "sms": "sms", "voice": "voice_call"}


def season_cost(r: SimResult) -> Dict[str, float]:
    c = r.cfg.costs
    profiles = generate_profiles(r.region, r.cfg.language, r.seed)
    msgs = [a.farm for a in r.alerts] + [n.farm for n in r.notices]
    messaging = sum(getattr(c, UNIT[profiles[f].channel]) for f in msgs)
    photos = int(np.isin(r.reports["src"], [PHOTO, LEAD_FARMER]).sum()) if len(r.reports["farm"]) else 0
    villages_alerted = len({int(r.region.farm_village[a.farm]) for a in r.alerts if a.risk == "HIGH"})
    out = {
        "messaging": messaging,
        "platform": c.platform_per_farm_season * r.region.n_farms,
        "incentives": c.incentive_per_photo * photos,
        "lead_farmers": c.lead_farmer_stipend_season * len(r.lead_farmers),
        "traps": c.trap_per_season * len(r.env.trap_xy) if r.env is not None else 0.0,
        "extension_visits": c.extension_visit * villages_alerted,
    }
    out["total"] = float(sum(out.values()))
    out["per_farm"] = out["total"] / r.region.n_farms
    return out
