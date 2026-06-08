from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import subprocess
import gc

import numpy as np
from tqdm import tqdm
import imageio.v3 as iio

from micasense import imageset, panel
from config_loader import load_config, date_config

# ----- Config loading -----

DATE_KEY = "20260430"  # change per flight/date as needed

cfg = load_config()
date_cfg = date_config(cfg, DATE_KEY)
naming_cfg = cfg["naming"]
range_cfg = cfg.get("range", {})

RANGE_ENABLED = range_cfg.get("enabled", False)
RANGE_START = int(range_cfg.get("start", 0))
RANGE_END = range_cfg.get("end", None)
if RANGE_END is not None:
    RANGE_END = int(RANGE_END)

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
    if not panel_dir.exists():
        raise FileNotFoundError(f"Panel directory does not exist: {panel_dir}")

    imgset = imageset.ImageSet.from_directory(panel_dir)
    captures = imgset.captures

    print(f"Found {len(captures)} panel captures in {panel_dir}")

    panel_list = []
    for cap in captures:
        img = cap.images[0]  # representative band
        p = panel.Panel(img)
        panel_list.append((cap, p))
    return panel_list


def compute_k_for_panel(p: panel.Panel) -> dict[str, float]:
    """
    Compute radiance->reflectance factor k_b for the band of this Panel's image.
    Returns a dict with a single entry {band_name: k_b}.
    """
    rad_mean, _, _, _ = p.radiance()
    rho = p.reflectance_from_panel_serial()
    if rho is None:
        raise ValueError("Panel reflectance could not be determined from serial.")

    band_name = p.image.band_name
    k_b = rho / rad_mean

    return {band_name: k_b}


def compute_k_for_capture(cap) -> dict[str, float]:
    """
    Compute k_b for all usable bands in a single panel capture.
    Skips thermal/LWIR and any band where a panel cannot be detected
    or reflectance is unavailable.
    Returns {band_name: k_b}.
    """
    k: dict[str, float] = {}
    for img in cap.images:
        if img.band_name.upper() == "LWIR" or img.band_index == THERMAL_BAND_ID:
            continue

        p = panel.Panel(img)

        if not p.panel_detected():
            print(f"Panel not detected for band {img.band_name}, skipping in this capture")
            continue

        try:
            rad_mean, _, _, _ = p.radiance()
        except Exception as e:
            print(f"Failed to compute radiance for band {img.band_name}: {e}, skipping")
            continue

        rho = p.reflectance_from_panel_serial()
        if rho is None:
            print(f"No reflectance from serial for band {img.band_name}, skipping")
            continue

        k[img.band_name] = rho / rad_mean

    if not k:
        raise RuntimeError("No bands produced calibration factors for this panel capture.")

    return k


def build_time_interpolator(panel_list):
    """
    Given a list of (capture, panel_obj), build a function f(time) -> k_dict
    that interpolates k_b over time if multiple panels exist.
    If there is only one panel, f(time) always returns the same k_dict.
    """
    if not panel_list:
        raise RuntimeError("No panel captures available for calibration.")

    entries: list[tuple[datetime, dict[str, float]]] = []
    for cap, _ in panel_list:
        t = cap.utc_time()
        if not isinstance(t, datetime):
            t = datetime.fromisoformat(str(t))
        k = compute_k_for_capture(cap)
        entries.append((t, k))

    entries.sort(key=lambda x: x[0])

    if len(entries) == 1:
        single_k = entries[0][1]

        def k_func(_time: datetime) -> dict[str, float]:
            return single_k

        print("Using single panel calibration for all times.")
        return k_func

    times = [e[0] for e in entries]
    k_dicts = [e[1] for e in entries]
    band_names = list(k_dicts[0].keys())

    def k_func(time: datetime) -> dict[str, float]:
        if time <= times[0]:
            return k_dicts[0]
        if time >= times[-1]:
            return k_dicts[-1]

        for i in range(len(times) - 1):
            t0 = times[i]
            t1 = times[i + 1]
            if t0 <= time <= t1:
                k0 = k_dicts[i]
                k1 = k_dicts[i + 1]
                total = (t1 - t0).total_seconds()
                alpha = (time - t0).total_seconds() / total if total > 0 else 0.0
                k_interp: dict[str, float] = {}
                for b in band_names:
                    v0 = k0[b]
                    v1 = k1[b]
                    k_interp[b] = (1 - alpha) * v0 + alpha * v1
                return k_interp

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
    capture_time = cap.utc_time()
    k_dict = get_k_for_time(capture_time)

    for img in cap.images:
        # Skip thermal / LWIR band
        if img.band_name.upper() == "LWIR" or img.band_index == THERMAL_BAND_ID:
            continue

        # DN -> radiance
        rad = img.radiance()

        band_name = img.band_name
        if band_name not in k_dict:
            print(f"No calibration factor for band {band_name} at time {capture_time}, skipping")
            continue

        factor = k_dict[band_name]
        refl = rad * factor  # reflectance array

        src_path = Path(img.path)
        parsed = parse_capture_band(src_path)
        if parsed is None:
            raise ValueError(f"Cannot parse capture/band from filename: {src_path.name}")
        capture_id, band_id = parsed

        out_path = make_reflectance_path(capture_id, band_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Write reflectance as float32 GeoTIFF (no metadata yet)
        iio.imwrite(out_path, refl.astype(np.float32), extension=".tif")

        # Copy EXIF/XMP from original
        copy_metadata(src_path, out_path)

        # Help GC by dropping big arrays
        del rad, refl


def main():
    print(f"Using config date key: {DATE_KEY}")
    print(f"Imagery root: {IMAGERY_ROOT}")
    print(f"Panel folder: {cal_panel_folder}")
    print(f"Multispectral folder: {multispectral_folder}")
    print(f"Reflectance folder: {reflectance_folder}")
    print(f"Exiftool path: {EXIFTOOL_PATH}")

    reflectance_folder.mkdir(parents=True, exist_ok=True)

    # 1) Load panels and build time->k interpolator
    panel_list = load_panel_captures(cal_panel_folder)
    k_func = build_time_interpolator(panel_list)

    # Panel summary (just shows the band of the representative image)
    print("\nPanel calibration summary:")
    for cap, p in panel_list:
        t = cap.utc_time()
        t_str = t.isoformat() if isinstance(t, datetime) else str(t)
        k = compute_k_for_panel(p)
        print(f"  Panel at {t_str}:")
        for band_name, factor in k.items():
            print(f"    {band_name}: k = {factor:.6f}")

    # 2) Load flight captures
    imgset = load_multispectral_images(multispectral_folder)
    captures = imgset.captures
    n_total = len(captures)
    print(f"Total captures: {n_total}")

    # Helper to get capture_id from a Capture
    def capture_id_of(cap):
        first_path = Path(cap.images[0].path)
        parsed = parse_capture_band(first_path)
        if parsed is None:
            raise ValueError(f"Cannot parse capture/band from filename: {first_path.name}")
        capture_id, _ = parsed
        return capture_id

    # Apply optional range restriction by capture_id
    if RANGE_ENABLED:
        start_id = RANGE_START
        end_id = RANGE_END if RANGE_END is not None else start_id

        captures_to_process = [
            cap for cap in captures
            if start_id <= capture_id_of(cap) <= end_id
        ]

        print(f"\nProcessing capture IDs in [{start_id}, {end_id}] "
              f"({len(captures_to_process)} captures selected)")
    else:
        captures_to_process = captures

    # 3) Process each capture to reflectance with progress bar + explicit GC
    for cap in tqdm(captures_to_process, desc="Processing captures", unit="capture"):
        process_capture_to_reflectance(cap, k_func)
        gc.collect()

    print("\nDone. Reflectance images written to:")
    print(reflectance_folder)


if __name__ == "__main__":
    main()