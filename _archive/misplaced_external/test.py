#!/usr/bin/env python3
"""
Inference: IMERG 10 → 4p4km / 5km via DDPM.super_resolution.
Multi-GPU (torchrun): split dataset by rank under save_path/<target>/rank_<id>/ — each file is only that rank's timesteps; merge by day across ranks if needed.
One NetCDF per calendar day: <prefix>_YYYYMMDD.nc (e.g. sr_imerg10_to_4p4km_20240601.nc).
Inference does NOT use DDP — avoids NCCL hang when some batches are skipped (month filter).
"""
from __future__ import annotations

import argparse
import glob
import os
import random
import re

import numpy as np
import pandas as pd
import torch
import torch.distributed as dist
import xarray as xr
from torch.utils.data import Dataset, Subset

from model.diffusion import DDPM


def distributed_env():
    if "LOCAL_RANK" in os.environ and "WORLD_SIZE" in os.environ:
        return (
            True,
            int(os.environ["LOCAL_RANK"]),
            int(os.environ["RANK"]),
            int(os.environ["WORLD_SIZE"]),
        )
    return False, 0, 0, 1


def ddp_setup(local_rank: int):
    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend="nccl")


def parse_int_tuple(s: str):
    return tuple(int(x.strip()) for x in s.split(",") if x.strip())


def pick_lat_lon_names(ds: xr.Dataset):
    lat_n = "lat" if "lat" in ds.coords else ("latitude" if "latitude" in ds.coords else None)
    lon_n = "lon" if "lon" in ds.coords else ("longitude" if "longitude" in ds.coords else None)
    if lat_n is None or lon_n is None:
        raise KeyError(f"Missing lat/lon: {list(ds.coords)}")
    return lat_n, lon_n


def norm_to_minus_1(a, a_min, a_max):
    d = (a_max - a_min) if a_max != a_min else 1.0
    return ((a - a_min) * 2.0 / d).astype(np.float32)


def inverse_precip_norm(x: torch.Tensor, pre: str, cmin: float, cmax: float) -> torch.Tensor:
    if pre == "log10":
        mu = x.new_tensor(-0.710)
        std = x.new_tensor(0.505 + 1e-6)
        y = torch.clamp(x, cmin, cmax)
        return torch.pow(10.0, y * std + mu) - 0.1
    if pre == "log1p":
        return torch.expm1(x)
    return x


class DayNCWriter:
    """One file per calendar day: <prefix>_YYYYMMDD.nc"""

    def __init__(self, out_dir, lat, lon, var_name="precip", file_prefix="sr"):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.lat = lat
        self.lon = lon
        self.vname = var_name
   390|        self.prefix = file_prefix
        self.buf: dict[str, list] = {}

    def add(self, pred: np.ndarray, times):
        t = pd.to_datetime(times, unit="s")
        for b in range(pred.shape[0]):
            ts = pd.Timestamp(t[b])
            dk = ts.strftime("%Y%m%d")  # YYYYMMD
            self.buf.setdefault(dk, []).append((ts, pred[b].astype(np.float32)))

    def flush_day(self, dk: str):
        items = self.buf.get(dk, [])
        if not items:
            return
        items.sort(key=lambda x: x[0])
        times = pd.to_datetime([t for t, _ in items])
        data = np.stack([d for _, d in items], 0)
        da = xr.DataArray(
            data,
            dims=("time", "lat", "lon"),
            coords={"time": times, "lat": self.lat, "lon": self.lon,
            name=self.vname,
        )
        fp = os.path.join(self.out_dir, f"{self.prefix}_{dk}.nc")
        enc = {self.vname: {"zlib": True, "complevel": 4, "dtype": "float32"}}
        da.to_dataset().to_netcdf(fp, encoding=enc)
        print(f"Saved daily: {fp}", flush=True)
        del self.buf[dk]

    def flush_all(self):
        for dk in sorted(self.buf.keys()):
            self.flush_day(dk)


def slice_bounds(ds, lat_n, lon_n, la0, la1, lo0, lo1):
    lv = ds[lat_n].values
    lov = ds[lon_n].values
    ls = slice(la0, la1) if lv[0] <= lv[-1] else slice(la1, la0)
    lo = slice(lo0, lo1) if lov[0] <= lov[-1] else slice(lo1, lo0)

Actually the issue with DDP: In eval mode, DDP still do collective ops? In eval mode, DDP typically doesn't run backward, but I think DDP might still broadcast buffers** at forward in sync - actually in eval, I need to check.

Actually the real issue we fixed was skip forward - but we removed DDP.

Current code says "Inference does NOT use DDP" - let me read the file again - the user's file might still have DDP - my write removed DDP - the read shows corrupted file - let me read actual test.py from workspace again

Actually the Write I did was corrupted. Let me read current test.py state from grep

The user message says they want daily save - I need to fix the whole test.py file.

Let me read test.py from glob - maybe a clean version exists now
</think>


<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>
Grep