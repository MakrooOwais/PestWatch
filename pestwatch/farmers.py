"""Farmer profiles: who gets messages, in which language, over which channel."""
from dataclasses import asdict, dataclass
from typing import List

import numpy as np

from .config import LanguageConfig
from .i18n import resolve
from .region import Region

CHANNELS = ("app", "sms", "voice")


@dataclass
class FarmerProfile:
    farm: int
    language: str
    channel: str            # app push for smartphone users, SMS or voice (IVR) otherwise
    subscribed: bool = True
    phone: str = ""         # E.164; never included in public JSON
    consent: bool = True    # may this farmer's data appear in shared (coarsened) datasets?

    def to_json(self, private: bool = False):
        d = asdict(self)
        if not private:
            d.pop("phone")
        return d


def demo_phone(seed: int, farm: int) -> str:
    """Deterministic fake Kenyan mobile number (+2547 0 ss fffff), unique per farm."""
    return f"+25470{seed % 100:02d}{farm:05d}"


def generate_profiles(region: Region, cfg: LanguageConfig, seed: int) -> List[FarmerProfile]:
    # Own RNG stream so profiles never perturb the simulation's random draws.
    rng = np.random.default_rng([seed, 7919])
    langs = [resolve(k) for k in cfg.language_mix]
    w = np.array(list(cfg.language_mix.values()), dtype=float)
    pick = rng.choice(len(langs), size=region.n_farms, p=w / w.sum())
    voice = rng.random(region.n_farms) < cfg.voice_share_without_app
    return [FarmerProfile(f, langs[pick[f]],
                          "app" if region.participates[f] else ("voice" if voice[f] else "sms"),
                          phone=demo_phone(seed, f))
            for f in range(region.n_farms)]
