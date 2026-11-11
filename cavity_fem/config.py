from typing import List, Optional
import math
from pydantic import BaseModel, Field, model_validator, ConfigDict


class Port(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=80)
    faces: List[int] = Field(min_length=1)
    impedance_ohm: float = Field(default=376.730313668, gt=0, le=1e12)
    kind: str = "surface_impedance"
    radiation_epsr: float = Field(default=1.0, ge=1, le=1000)

    @model_validator(mode="after")
    def boundary_type(self):
        if self.kind not in ("surface_impedance", "radiation"):
            raise ValueError("外部境界はsurface_impedanceまたはradiationです。")
        if self.kind == "radiation":
            # Wave impedance of a homogeneous nonmagnetic exterior medium.
            # This is not the 50-ohm characteristic impedance of a coax cable.
            self.impedance_ohm = 376.730313668 / math.sqrt(self.radiation_epsr)
        return self


class Antenna(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=80)
    face: int = Field(ge=1)
    length_mm: float = Field(default=0, ge=0)
    pin_radius_mm: float = Field(default=0.5, gt=0, le=30)
    coax_outer_radius_mm: float = Field(default=1.67, gt=0, le=100)
    dielectric_epsr: float = Field(default=2.1, ge=1, le=100)
    dielectric_tand: float = Field(default=0, ge=0, le=0.1)
    feed_length_mm: float = Field(default=3, gt=0, le=100)
    center_mm: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)
    inward_axis: Optional[List[float]] = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def dimensions(self):
        if self.coax_outer_radius_mm <= self.pin_radius_mm:
            raise ValueError("同軸の外導体内半径は中心導体半径より大きくしてください。")
        if self.inward_axis is not None and math.sqrt(sum(x*x for x in self.inward_axis)) < 1e-12:
            raise ValueError("アンテナの挿入方向はゼロベクトルにできません。")
        return self


class AntennaSweep(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    antenna: str = Field(min_length=1, max_length=80)
    lengths_mm: List[float] = Field(min_length=1, max_length=21)

    @model_validator(mode="after")
    def lengths(self):
        if any(x < 0 for x in self.lengths_mm) or len(set(self.lengths_mm)) != len(self.lengths_mm):
            raise ValueError("挿入長のスイープには重複しない0以上の値を指定してください。")
        return self


class SolveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    geometry_id: Optional[str] = None
    geometry_mode: str = "vacuum_solid"
    solid_index: int = Field(default=0, ge=0)
    maxh_mm: float = Field(default=2.0, ge=0.1, le=30)
    order: int = Field(default=2, ge=1, le=3)
    n_modes: int = Field(default=3, ge=1, le=12)
    target_ghz: float = Field(default=8.0, gt=0, le=1000)
    threads: int = Field(default=2, ge=1, le=16)
    rs_nohm: Optional[float] = Field(default=10.0, ge=0)
    bulk_epsr: float = Field(default=1.0, ge=1, le=1000)
    bulk_tand: float = Field(default=0, ge=0, le=0.1)
    oxide_thickness_nm: float = Field(default=0.0, ge=0, le=1000)
    oxide_epsr: float = Field(default=1.0, ge=1, le=1000)
    oxide_tand: float = Field(default=0.0, ge=0, le=1)
    loss_model: str = "surface_rs"
    temperature_k: float = Field(default=0.02, gt=0, le=300)
    photon_number: float = Field(default=1, ge=0)
    tls_nc: float = Field(default=1.0, gt=0)
    tls_beta: float = Field(default=0.5, gt=0, le=2)
    q_res: Optional[float] = Field(default=None, gt=0)
    analysis_mode: str = "resonance"
    all_artificial_caps: bool = False
    excluded_artificial_cap_faces: List[int] = Field(default_factory=list)
    ports: List[Port] = Field(default_factory=list)
    antennas: List[Antenna] = Field(default_factory=list, max_length=8)
    antenna_sweep: Optional[AntennaSweep] = None
    mode_search: str = "target"
    spectrum_enabled: bool = False
    spectrum_start_ghz: float = Field(default=0.1, gt=0, le=1000)
    spectrum_stop_ghz: float = Field(default=18, gt=0, le=1000)
    spectrum_points: int = Field(default=41, ge=5, le=401)
    driven_port: Optional[str] = None
    field_resolution: int = Field(default=41, ge=11, le=101)

    @model_validator(mode="after")
    def validate_choices(self):
        if self.analysis_mode not in ("resonance", "all_openings_qc"):
            raise ValueError("analysis_modeはresonanceまたはall_openings_qcです。")
        if self.analysis_mode == "all_openings_qc":
            self.spectrum_enabled = False
        if self.geometry_mode not in ("metal_void", "vacuum_solid"):
            raise ValueError("geometry_mode must be metal_void or vacuum_solid")
        excluded = self.excluded_artificial_cap_faces
        if any(face < 1 for face in excluded) or len(excluded) != len(set(excluded)):
            raise ValueError("自動開口から除外する面IDは正の整数で、重複させないでください。")
        if self.loss_model not in ("tls_qres", "surface_rs", "none"):
            raise ValueError("loss_model must be tls_qres, surface_rs, or none")
        if self.loss_model == "surface_rs" and self.rs_nohm is None:
            raise ValueError("表面抵抗モデルにはRsの入力が必要です。")
        faces = [f for p in self.ports for f in p.faces]
        if len(faces) != len(set(faces)) or any(f < 1 for f in faces):
            raise ValueError("ポート面IDは正の整数で、重複させないでください。")
        if len({p.name for p in self.ports}) != len(self.ports):
            raise ValueError("ポート名は重複させないでください。")
        names = [p.name for p in self.ports] + [a.name for a in self.antennas]
        if len(set(names)) != len(names):
            raise ValueError("アンテナ名とポート名は重複させないでください。")
        if len({a.face for a in self.antennas}) != len(self.antennas):
            raise ValueError("同じ面に複数アンテナは配置できません。")
        if any(a.face in faces for a in self.antennas):
            raise ValueError("アンテナの取付面を別の外部開口境界にも指定しないでください。")
        if self.mode_search not in ("target", "lowest"):
            raise ValueError("mode_searchはtargetまたはlowestです。")
        if self.spectrum_start_ghz >= self.spectrum_stop_ghz:
            raise ValueError("広帯域スキャンの上限は下限より大きくしてください。")
        if self.driven_port is not None and self.driven_port not in [a.name for a in self.antennas]:
            raise ValueError("S11の励振ポートには定義済みのアンテナ名を指定してください。")
        if self.antenna_sweep and self.antenna_sweep.antenna not in [a.name for a in self.antennas]:
            raise ValueError("スイープ対象のアンテナがありません。")
        return self
