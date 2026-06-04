from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import subprocess

import numpy as np

from micasense import imageset, imageutils, panel

from config_loader import load_config, date_config

# ----- Config loading -----

DATE_KEY = "20260430"  # change per flight/date as needed

cfg = load_config()
date_cfg = date_config(cfg, DATE_KEY)
naming_cfg = cfg["naming"]

IMAGERY_ROOT = Path(date_cfg["imagery_root"])
multispectral_folder = IMAGERY_ROOT / "multispectral"
cal_panel_folder = IMAGERY_ROOT / "cal_panels"
reflectance_folder = IMAGERY_ROOT / "reflectance"

EXIFTOOL_PATH = Path(cfg["paths"]["exiftool"])

SOURCE_PATTERN = naming_cfg["source_pattern"]
REFLECTANCE_PATTERN = naming_cfg["reflectance_pattern"]

FILENAME_RE = re.compile(SOURCE_PATTERN, re.IGNORECASE)

THERMAL_BAND_ID = 7  # thermal will be handled separately if needed


# ----- Helpers for filenames and EXIF -----

def parse_capture_band(file_path: Path):
    """
    Parse capture_id and band_id from a filename using SOURCE_PATTERN.
    Returns (capture_id, band_id) or None if no match.
    """
    m = FILENAME_RE.match(file_path.name)
    if not m:
        return None
    capture_id = int(m.group(1))
    band_id = int(m.group(2))
    return capture_id, band_id


def make_reflectance_path(capture_id: int, band_id: int) -> Path:
    name = REFLECTANCE_PATTERN.format(capture_id=capture_id, band_id=band_id)
    return reflectance_folder / name


def copy_metadata(src: Path, dst: Path):
    """
    Copy EXIF/XMP tags from original image src to dst using exiftool.
    """
    subprocess.run(
        [
            str(EXIFTOOL_PATH),
            "-overwrite_original",
            "-tagsFromFile",
            str(src),
            str(dst),
        ],
        check=True,
        capture_output=False,
    )


# ----- Panel handling -----

def load_panel_captures(panel_dir: Path):
    """
    Load all panel captures from cal_panels as (capture, panel_obj) pairs.
    """
    if not panel_dir.exists():
        raise FileNotFoundError(f"Panel directory does not exist: {panel_dir}")

    imgset = imageset.ImageSet.from_directory(panel_dir)
    captures = imgset.captures

    print(f"Found {len(captures)} panel captures in {panel_dir}")

    panel_list: list[tuple[imageset.Capture, panel.Panel]] = []
    for cap in captures:
        p = panel.Panel(cap)
        panel_list.append((cap, p))
    return panel_list


def compute_k_for_panel(p: panel.Panel) -> dict[str, float]:
    """
    Compute per-band radiance->reflectance factors k_b for a single panel.
    k_b = panel_reflectance_b / panel_mean_radiance_b
    """
    refl = p.reflectance_by_band()
    rad = p.radiance_by_band()

    k: dict[str, float] = {}
    for band_name in refl.keys():
        rad_val = rad[band_name]
        if rad_val != 0:
            k[band_name] = refl[band_name] / rad_val
    return k


def build_time_interpolator(panel_list):
    """
    Given a list of (capture, panel_obj), build a function f(time) -> k_dict
    that interpolates k_b over time if multiple panels exist.
    If there is only one panel, f(time) always returns the same k_dict.
    """
    if not panel_list:
        raise RuntimeError("No panel captures available for calibration.")

    # Build sorted list of (timestamp, k_dict)
    entries: list[tuple[datetime, dict[str, float]]] = []
    for cap, p in panel_list:
        t = cap.center_time
        if not isinstance(t, datetime):
            # Convert if needed, but typically center_time is already datetime
            t = datetime.fromisoformat(str(t))
        k = compute_k_for_panel(p)
        entries.append((t, k))

    entries.sort(key=lambda x: x[0])

    if len(entries) == 1:
        # Single panel: constant k_dict
        single_k = entries[0][1]

        def k_func(_time: datetime) -> dict[str, float]:
            return single_k

        print("Using single panel calibration for all times.")
        return k_func

    # Multiple panels: linear interpolation in time
    times = [e[0] for e in entries]
    k_dicts = [e[1] for e in entries]

    band_names = list(k_dicts[0].keys())

    def k_func(time: datetime) -> dict[str, float]:
        # Before first panel: use first
        if time <= times[0]:
            return k_dicts[0]
        # After last panel: use last
        if time >= times[-1]:
            return k_dicts[-1]

        # Find panel times bracketing 'time'
        for i in range(len(times) - 1):
            t0 = times[0 + i]
            t1 = times[1 + i]
            if t0 <= time <= t1:
                k0 = k_dicts[0 + i]
                k1 = k_dicts[1 + i]
                # Fraction of time between t0 and t1
                total = (t1 - t0).total_seconds()
                alpha = (time - t0).total_seconds() / total if total > 0 else 0.0
                # Interpolate each band
                k_interp: dict[str, float] = {}
                for b in band_names:
                    v0 = k0[b]
                    v1 = k1[b]
                    k_interp[b] = (1 - alpha) * v0 + alpha * v1
                return k_interp

        # Fallback (should not be reached)
        return k_dicts[-1]

    print(f"Using time-varying calibration from {len(entries)} panel captures.")
    return k_func


# ----- Flight image processing -----

def load_multispectral_images(ms_dir: Path):
    """
    Load flight captures from the multispectral folder.
    Returns an ImageSet.
    """
    if not ms_dir.exists():
        raise FileNotFoundError(f"Multispectral directory does not exist: {ms_dir}")
    imgset = imageset.ImageSet.from_directory(ms_dir)
    print(f"Found {len(imgset.captures)} flight captures in {ms_dir}")
    return imgset


def process_capture_to_reflectance(cap, get_k_for_time):
    """
    Convert all non-thermal bands for a single capture to reflectance and write
    ref_IMG_<capture>_<band>.tif to reflectance_folder, copying metadata from original.
    """
    capture_time = cap.center_time
    k_dict = get_k_for_time(capture_time)

    for img in cap.images:
        # Skip thermal band here; you might handle it separately
        if img.band_name.lower() == "thermal" or img.band_index == THERMAL_BAND_ID:
            continue

        # Convert raw DN to radiance
        rad = imageutils.raw_image_to_radiance(img)

        # Determine band key used in k_dict
        band_name = img.band_name
        if band_name not in k_dict:
            # Some setups use band_index as key; adjust if needed
            raise KeyError(f"Band {band_name} not found in calibration factors.")

        factor = k_dict[band_name]
        refl = rad * factor  # reflectance

        # Build output filename based on capture_id and band_id from original
        src_path = Path(img.path)
        parsed = parse_capture_band(src_path)
        if parsed is None:
            raise ValueError(f"Cannot parse capture/band from filename: {src_path.name}")
        capture_id, band_id = parsed

        out_path = make_reflectance_path(capture_id, band_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Save as float32 GeoTIFF; you can adjust dtype or scaling as needed
        imageutils.save_image(out_path, refl.astype(np.float32), img, dtype=np.float32)

        # Copy EXIF/XMP from original
        copy_metadata(src_path, out_path)


def main():
    print(f"Using config date key: {DATE_KEY}")
    print(f"Imagery root: {IMAGERY_ROOT}")
    print(f"Panel folder: {cal_panel_folder}")
