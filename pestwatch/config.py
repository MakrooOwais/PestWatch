"""All tunable parameters for the PestWatch simulation, detector and alerting.

Values are plausible defaults for smallholder Arabica coffee under coffee leaf
rust (Hemileia vastatrix) pressure in the East African highlands. They are NOT fitted to field data; treat them as
scenario assumptions and vary them in sensitivity analysis.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# Coffee leaf classes of the on-device vision model (see docs/MODEL_CARD.md).
CLASSES = ["healthy", "leaf_rust", "leaf_miner", "cercospora", "phoma"]
# Non-target leaf problems and how common each is among background damage (look-alikes).
OTHER_DAMAGE = {"leaf_miner": 0.4, "cercospora": 0.35, "phoma": 0.25}
TARGET = 1   # index of the disease being watched (leaf_rust)


@dataclass
class RegionConfig:
    size_km: float = 14.0
    n_villages: int = 6
    farms_per_village: Tuple[int, int] = (18, 30)
    village_spread_km: float = 0.8
    app_participation: float = 0.7          # share of farmers who take photos
    # Approximate origin for lat/lon display (simulated region, western Kenya).
    origin_lat: float = 0.56
    origin_lon: float = 34.50
    farm_ha: Tuple[float, float] = (0.5, 2.5)


@dataclass
class PestConfig:
    target_class: str = "leaf_rust"         # the disease this profile watches (a class of the vision model)
    n_landings: int = 1                     # independent spore introductions per outbreak season
    second_landing_gap_days: Tuple[float, float] = (6.0, 20.0)
    introduce_day: float = 10.0             # wind/rain-borne rust spores arrive after the rains start
    landing_days: float = 3.0               # infections spread over this many days
    landing_sigma_km: float = 0.9           # spatial footprint of the introduction
    landing_peak_prob: float = 0.55         # P(farm at the centre gets infected)
    i0: float = 0.02                        # share of leaves with first faint lesions
    growth_per_day: float = 0.12            # logistic within-farm growth (rust is slower than insect pests)
    beta_per_day: float = 1.2              # between-farm transmission scale
    kernel_km: float = 1.1                  # exponential dispersal kernel scale
    long_jump_per_day: float = 0.08         # rare long-distance introductions (region-wide rate)
    visible_threshold: float = 0.25         # damage share farmers notice unaided
    widespread_frac: float = 0.10           # share of farms visibly damaged = "widespread"


@dataclass
class ReportingConfig:
    photo_sessions_per_day: float = 0.35    # mean per participating farmer
    photos_per_session_extra: float = 1.5   # photos = 1 + Poisson(this)
    # Farmers photograph plants that look "off", not random ones.
    symptom_targeting: float = 3.0              # farmers photograph leaves that look "off"
    other_targeting: float = 2.0
    # Background prevalence of non-TARGET damage per village (confounders).
    other_damage_range: Tuple[float, float] = (0.04, 0.14)
    # Connectivity per village: (share syncing near-instantly, mean offline delay hours)
    online_share_range: Tuple[float, float] = (0.2, 0.85)
    offline_delay_h_range: Tuple[float, float] = (6.0, 40.0)


@dataclass
class EnvironmentConfig:
    """Weather and optional trap / forecast signals (all simulated). Coffee leaf rust
    has no pheromone traps, so traps are off by default; wind still spreads spores."""
    wind_speed_mean: float = 3.0            # m/s
    wind_persistence: float = 0.8           # day-to-day autocorrelation of direction
    wind_anisotropy: float = 0.6            # how strongly moths drift downwind (0 = isotropic)
    temp_mean_c: float = 22.0
    temp_daily_sd: float = 2.0
    pest_base_temp_c: float = 14.0          # rust development slows sharply below ~15 °C ...
    pest_ref_temp_c: float = 22.0           # ... is fastest around 21–25 °C (growth_per_day is calibrated here) ...
    pest_temp_width_c: float = 8.0          # ... and falls off again above ~28 °C
    traps_per_village: int = 0              # no pheromone traps exist for a fungus
    trap_background_per_day: float = 0.4    # male moths caught with no outbreak
    trap_flight_gain: float = 25.0          # catches during a migratory flight overhead
    trap_local_gain: float = 6.0            # catches from locally emerging moths
    trap_reach_km: float = 1.5
    trap_read_days: float = 1.0             # how often a lead farmer reads and reports the trap
    migration_lead_days: float = 3.0        # forecast warns this long before a landing
    passing_flights_mean: float = 0.0       # spore showers that hit traps but don't establish (only used with traps)
    migration_false_spike_prob: float = 0.3 # chance per season of a spurious forecast spike


@dataclass
class BehaviourConfig:
    engagement_halflife_days: float = 35.0  # photo-taking fades over the season ...
    engagement_floor: float = 0.5           # ... to this share of the initial rate
    alert_reengagement: float = 0.6         # +60% photo rate for a week after an alert / request
    incentive_multiplier: float = 1.0       # e.g. airtime-per-photo programme (1 = none)
    lead_farmers_per_village: int = 2       # do a weekly 10-plant scouting round
    lead_farmer_plants: int = 10
    n_spammers: int = 0                     # farms sending bogus high-confidence reports
    spam_rate_per_day: float = 1.0
    inspection_reply_rate: float = 0.6      # alerted farmers who report what they found


@dataclass
class ScoutingConfig:
    """Targeted scouting requests sent when a signal reaches watch level."""
    watch_llr: float = 1.2                  # scan score that triggers requests (independent of alert threshold)
    watch_p: float = 0.4                    # or fused P(outbreak) at/above this
    trap_anomaly: float = 3.0               # or a trap catch this many SDs above its baseline
    forecast_high: float = 0.5              # migration forecast index counted as "flight likely"
    forecast_trap_factor: float = 0.5       # ... which halves the trap level that triggers scouting
    farmers_per_request: int = 12
    cooldown_days: float = 3.0
    max_requests_per_14d: int = 2
    compliance: float = 0.6
    response_delay_days: float = 0.5
    plants_checked: int = 10
    per_plant_detect: float = 1.5           # x damage share: scouts look at whorls closely
    false_find: float = 0.03                # mistake look-alike spots (Cercospora, Phoma) for rust


@dataclass
class FusionConfig:
    """Combine photo scan, farmer confirmations, traps and forecasts into P(outbreak)."""
    weights_path: str = "data/fusion.json"
    alert_p: float = 0.8                    # recalibrated by evaluate.py to the false-alarm budget
    trigger_p: float = 0.95                 # anticipatory-finance trigger
    trigger_min_farms: int = 5


@dataclass
class FeatureFlags:
    inspection_replies: bool = True
    scouting: bool = True
    fusion: bool = True
    wind_zones: bool = True
    alert_budget: bool = True
    all_clear: bool = True


@dataclass
class CostConfig:
    """Running-cost assumptions in USD (indicative East African prices; replace with quotes)."""
    sms: float = 0.008
    voice_call: float = 0.04
    app_push: float = 0.0005
    platform_per_farm_season: float = 0.30  # hosting, support, model updates
    incentive_per_photo: float = 0.0        # airtime incentives, if any
    lead_farmer_stipend_season: float = 5.0
    trap_per_season: float = 6.0            # trap + lures
    extension_visit: float = 5.0            # per alerted village


@dataclass
class NuisanceConfig:
    """Non-outbreak events that fool naive detectors; present in every season."""
    engagement_surges: int = 2              # e.g. extension training day: photo rate spikes in one village
    surge_multiplier: float = 3.0
    surge_days: float = 3.0
    confounder_flares: int = 1              # e.g. a Cercospora / leaf miner flare-up in one village (look-alikes)
    flare_multiplier: float = 2.5
    flare_days: float = 10.0


@dataclass
class ClassifierConfig:
    """Simulated on-device model behaviour (stand-in for a real TFLite model).

    Rows of ``confusion`` give P(top-1 prediction | true class) over CLASSES.
    The TARGET row is interpolated between early (subtle) and late (obvious) damage.
    """
    confusion: Tuple[Tuple[float, ...], ...] = (
        #  hlth  rust  miner cercos phoma
        (0.90, 0.03, 0.03, 0.02, 0.02),   # healthy
        (0.00, 0.00, 0.00, 0.00, 0.00),   # leaf rust (see target_* below)
        (0.06, 0.08, 0.78, 0.05, 0.03),   # leaf miner
        (0.06, 0.12, 0.04, 0.72, 0.06),   # cercospora: brown spots, the main look-alike for early rust
        (0.06, 0.08, 0.03, 0.08, 0.75),   # phoma
    )
    target_early: Tuple[float, ...] = (0.25, 0.50, 0.05, 0.15, 0.05)
    target_late: Tuple[float, ...] = (0.03, 0.88, 0.02, 0.05, 0.02)
    # If present, the trained model's held-out confusion matrix replaces the rows above
    # (TARGET row -> target_late; target_early is derived by degrading it). See scripts/train_classifier.py.
    measured_confusion_path: str = "models/pest_classifier/confusion.json"
    use_measured: bool = True
    measured_smoothing: float = 1.0         # pseudo-count per cell: small test classes can't show a 0% error rate
    field_shift: float = 0.3                # blend measured toward the pessimistic matrix: field photos != test-set photos
    degrade: float = 0.0                    # sensitivity: mix confusion rows this far toward uniform
    conf_correct: float = 0.80              # mean top-1 confidence when right
    conf_wrong: float = 0.58                # ... when wrong (models are less sure)
    conf_target_early: float = 0.60            # ... on subtle early TARGET damage
    concentration: float = 12.0


@dataclass
class ScanConfig:
    radii_km: List[float] = field(default_factory=lambda: [0.6, 1.0, 1.5, 2.2])
    windows_h: List[float] = field(default_factory=lambda: [24.0, 48.0, 72.0])
    history_days: float = 21.0
    history_gap_days: float = 5.0           # skip recent days so a growing outbreak doesn't mask itself
    min_signal_p: float = 0.0               # ignore P(TARGET) below this (suppresses background mass)
    prior_rate: float = 0.07                # prior mean signal per photo
    prior_strength: float = 300.0           # pseudo-photos for regional prior
    farm_shrinkage: float = 60.0            # pseudo-photos shrinking farm rate to regional
    min_farms: int = 3                      # distinct farms with a positive photo
    max_per_farm: float = 3.0               # cap on one farm's soft count per window
    positive_p: float = 0.5                 # P(TARGET) counted as a "report" in messages
    threshold: float = 9.0                  # LLR alert threshold (recalibrated by evaluate.py)
    watch_fraction: float = 0.5             # watch level = this * threshold
    max_clusters: int = 3


@dataclass
class LanguageConfig:
    """Farmers' preferred language and channel. Defaults model western Kenya;
    set e.g. {"fr": 0.8, "en": 0.2} for a francophone deployment."""
    language_mix: Dict[str, float] = field(default_factory=lambda: {"sw": 0.65, "en": 0.35})
    voice_share_without_app: float = 0.25   # feature-phone users who prefer voice calls (IVR) to SMS


@dataclass
class AlertConfig:
    buffer_km: float = 3.0                  # warn farms this far beyond the cluster edge
    wind_stretch: float = 0.45              # downwind farms count as this much closer (0 = circular zones)
    max_alerts_per_14d: int = 3             # per farmer; HIGH escalations always go through
    all_clear_days: float = 7.0
    resend_hours: float = 72.0
    active_days: float = 7.0                # keep a cluster active this long after last detection


@dataclass
class ResponseConfig:
    """How farmers act, with and without PestWatch."""
    compliance: float = 0.75                # share of alerted farmers who inspect
    inspect_delay_days: Tuple[float, float, float] = (1.0, 2.0, 4.0)  # HIGH, MEDIUM, LOW
    scout_detect_base: float = 0.45         # P(find lesions) when inspecting an infected farm
    scout_detect_slope: float = 15.0        # + slope * damage share
    self_notice_per_day: float = 0.3        # P/day farmer notices visible damage unaided
    treat_delay_days: float = 3.0           # time to obtain and apply control after noticing
    treated_decline_per_day: float = 0.2    # fungicide + pruning: lesions decline slowly
    treated_emission: float = 0.3           # residual spore release after treatment
    treated_susceptibility: float = 0.25
    # Conventional (no-PestWatch) detection: extension learns of an outbreak once
    # this many farmers have noticed damage, after a reporting/verification lag.
    conventional_farms: int = 3
    conventional_lag_days: float = 5.0
    control_dose_l_per_ha: float = 2.0      # copper fungicide product per ha per round (for agro-dealer pre-positioning; assumption)
    finance_usd_per_ha: float = 15.0        # anticipatory-finance payout per ha at risk when triggered


@dataclass
class EconomicsConfig:
    # Assumptions, not quotes: smallholder Arabica parchment yield and farm-gate price.
    yield_t_per_ha: float = 0.6
    price_usd_per_t: float = 3000.0
    loss_per_damage: float = 0.6            # yield loss fraction per unit peak leaf-infection share
    treatment_cost_usd_per_ha: float = 30.0


@dataclass
class Config:
    days: float = 75.0
    dt_days: float = 0.25
    outbreak: bool = True
    region: RegionConfig = field(default_factory=RegionConfig)
    pest: PestConfig = field(default_factory=PestConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    nuisance: NuisanceConfig = field(default_factory=NuisanceConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    language: LanguageConfig = field(default_factory=LanguageConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    behaviour: BehaviourConfig = field(default_factory=BehaviourConfig)
    scouting: ScoutingConfig = field(default_factory=ScoutingConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    costs: CostConfig = field(default_factory=CostConfig)
    response: ResponseConfig = field(default_factory=ResponseConfig)
    economics: EconomicsConfig = field(default_factory=EconomicsConfig)

    @property
    def n_steps(self) -> int:
        return int(round(self.days / self.dt_days))
