"""Convert multispectral imagery to reflectance using calibration panel captures.

This script loads panel imagery and flight imagery for a configured date,
computes per-band radiance-to-reflectance calibration factors from panel
captures, interpolates those factors over time when multiple panel captures
are available, and writes reflectance GeoTIFFs for each non-thermal band.

Reflectance outputs are written to a dedicated output folder and inherit
EXIF/XMP metadata from the original source imagery via ExifTool.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Any, Callable
import re
import subprocess

import imageio.v3 as iio
import numpy as np
from numpy.typing import NDArray
from tqdm import tqdm

from micasense import imageset, panel

from config_loader import load_config, date_config


FloatArray = NDArray[np.floating[Any]]
CalibrationDict = dict[str, float]
CalibrationFunc = Callable[[datetime], CalibrationDict]


# ----- Config loading -----

DATE_KEY = "20260430"  # Change per flight/date as needed.

cfg = load_config()
date_cfg = date_config(cfg, DATE_KEY)
naming_cfg = cfg["naming"]

IMAGERY_ROOT = Path(date_cfg["imagery_root"])
multispectral_folder = IMAGERY_ROOT / "multispectral"
cal_panel_folder = IMAGERY_ROOT / "cal_panels"
reflectance_folder = IMAGERY_ROOT / "reflectance"

EXIFTOOL_PATH = Path(cfg["paths"]["exiftool"])

SOURCE_PATTERN: str = naming_cfg["source_pattern"]
REFLECTANCE_PATTERN: str = naming_cfg["reflectance_pattern"]

FILENAME_RE = re.compile(SOURCE_PATTERN, re.IGNORECASE)

THERMAL_BAND_ID = 7  # Thermal will be handled separately if needed.


# ----- Helpers for filenames and EXIF -----

def parse_capture_band(file_path: Path) -> tuple[int, int] | None:
    """Parse capture ID and band ID from a source image filename.

    The filename is matched against the configured source pattern. The first
    two capture groups are assumed to be capture ID and band ID.

    Args:
        file_path: Path to the image file whose filename should be parsed.

    Returns:
        A `(capture_id, band_id)` tuple if the filename matches the configured
        naming pattern, or None if the filename cannot be parsed.
    """
    match = FILENAME_RE.match(file_path.name)
    if not match:
        return None
    capture_id = int(match.group(1))
    band_id = int(match.group(2))
    return capture_id, band_id


def make_reflectance_path(capture_id: int, band_id: int) -> Path:
    """Build the output path for a reflectance image.

    Args:
        capture_id: Capture identifier parsed from the source filename.
        band_id: Band identifier parsed from the source filename.

    Returns:
        The full output path for the reflectance image in the reflectance
        folder using the configured reflectance naming pattern.
    """
    name = REFLECTANCE_PATTERN.format(capture_id=capture_id, band_id=band_id)
    return reflectance_folder / name


def copy_metadata(src: Path, dst: Path) -> None:
    """Copy EXIF and XMP metadata from a source image to an output image.

    Args:
        src: Source image path containing the metadata to copy.
        dst: Destination image path that will receive the copied metadata.

    Returns:
        None.

    Raises:
        subprocess.CalledProcessError: If ExifTool returns a non-zero exit code.
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

def load_panel_captures(panel_dir: Path) -> list[tuple[Any, panel.Panel]]:
    """Load calibration panel captures from a directory.

    One representative `panel.Panel` object is created from the first image in
    each capture for summary and inspection purposes.

    Args:
        panel_dir: Directory containing calibration panel imagery.

    Returns:
        A list of `(capture, panel_obj)` tuples, one per panel capture.

    Raises:
        FileNotFoundError: If the panel directory does not exist.
    """
    if not panel_dir.exists():
        raise FileNotFoundError(f"Panel directory does not exist: {panel_dir}")

    imgset = imageset.ImageSet.from_directory(panel_dir)
    captures = imgset.captures

    print(f"Found {len(captures)} panel captures in {panel_dir}")

    panel_list: list[tuple[Any, panel.Panel]] = []
    for cap in captures:
        img = cap.images[0]
        panel_obj = panel.Panel(img)
        panel_list.append((cap, panel_obj))

    return panel_list


def compute_k_for_panel(panel_obj: panel.Panel) -> CalibrationDict:
    """Compute a radiance-to-reflectance factor for one panel image band.

    The calibration factor is computed as panel reflectance divided by mean
    panel radiance for the image band represented by `panel_obj`.

    Args:
        panel_obj: A MicaSense panel object for a single image band.

    Returns:
        A dictionary containing one `{band_name: k_b}` entry.

    Raises:
        ValueError: If panel reflectance cannot be determined from the panel
            serial information.
    """
    rad_mean, _, _, _ = panel_obj.radiance()

    rho = panel_obj.reflectance_from_panel_serial()
    if rho is None:
        raise ValueError("Panel reflectance could not be determined from serial.")

    band_name = panel_obj.image.band_name
    k_b = rho / rad_mean

    return {band_name: k_b}


def compute_k_for_capture(cap: Any) -> CalibrationDict:
    """Compute calibration factors for all usable bands in one panel capture.

    Thermal or LWIR bands are skipped. Any band is also skipped if the panel
    cannot be detected or if radiance or reflectance values cannot be computed.

    Args:
        cap: A MicaSense capture object containing one image per band.

    Returns:
        A dictionary mapping band names to radiance-to-reflectance factors.

    Raises:
        RuntimeError: If no usable bands produce calibration factors.
    """
    k: CalibrationDict = {}

    for img in cap.images:
        if img.band_name.upper() == "LWIR" or img.band_index == THERMAL_BAND_ID:
            continue

        panel_obj = panel.Panel(img)

        if not panel_obj.panel_detected():
            print(f"Panel not detected for band {img.band_name}, skipping in this capture")
            continue

        try:
            rad_mean, _, _, _ = panel_obj.radiance()
        except Exception as exc:
            print(f"Failed to compute radiance for band {img.band_name}: {exc}, skipping")
            continue

        rho = panel_obj.reflectance_from_panel_serial()
        if rho is None:
            print(f"No reflectance from serial for band {img.band_name}, skipping")
            continue

        k[img.band_name] = rho / rad_mean

    if not k:
        raise RuntimeError("No bands produced calibration factors for this panel capture.")

    return k


def build_time_interpolator(
    panel_list: list[tuple[Any, panel.Panel]],
) -> CalibrationFunc:
    """Build a function that returns calibration factors for a given time.

    If a single panel capture is available, the returned function always
    returns the same calibration dictionary. If multiple panel captures are
    available, calibration factors are linearly interpolated in time between
    adjacent panel captures.

    Args:
        panel_list: List of `(capture, panel_obj)` tuples for available panel
            captures.

    Returns:
        A callable that accepts a `datetime` and returns a dictionary mapping
        band names to calibration factors.

    Raises:
        RuntimeError: If no panel captures are available.
    """
    if not panel_list:
        raise RuntimeError("No panel captures available for calibration.")

    entries: list[tuple[datetime, CalibrationDict]] = []
    for cap, _ in panel_list:
        capture_time = cap.utc_time()
        if not isinstance(capture_time, datetime):
            capture_time = datetime.fromisoformat(str(capture_time))
        k = compute_k_for_capture(cap)
        entries.append((capture_time, k))

    entries.sort(key=lambda item: item[0])

    if len(entries) == 1:
        single_k = entries[0][1]

        def k_func(_time: datetime) -> CalibrationDict:
            """Return a fixed calibration dictionary for any input time."""
            return single_k

        print("Using single panel calibration for all times.")
        return k_func

    times = [entry[0] for entry in entries]
    k_dicts = [entry[1] for entry in entries]
    band_names = list(k_dicts[0].keys())

    def k_func(time: datetime) -> CalibrationDict:
        """Interpolate calibration factors for the requested time."""
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

                k_interp: CalibrationDict = {}
                for band_name in band_names:
                    v0 = k0[band_name]
                    v1 = k1[band_name]
                    k_interp[band_name] = (1 - alpha) * v0 + alpha * v1
                return k_interp

        return k_dicts[-1]

    print(f"Using time-varying calibration from {len(entries)} panel captures.")
    return k_func


# ----- Flight image processing -----

def load_multispectral_images(ms_dir: Path) -> Any:
    """Load flight captures from the multispectral imagery folder.

    Args:
        ms_dir: Directory containing multispectral flight imagery.

    Returns:
        A MicaSense `ImageSet` object for the directory.

    Raises:
        FileNotFoundError: If the multispectral directory does not exist.
    """
    if not ms_dir.exists():
        raise FileNotFoundError(f"Multispectral directory does not exist: {ms_dir}")

    imgset = imageset.ImageSet.from_directory(ms_dir)
    print(f"Found {len(imgset.captures)} flight captures in {ms_dir}")
    return imgset


def process_capture_to_reflectance(cap: Any, get_k_for_time: CalibrationFunc) -> None:
    """Convert one capture's non-thermal bands to reflectance GeoTIFFs.

    Each non-thermal image in the capture is converted from radiance to
    reflectance using the calibration factor returned for the capture time.
    Output TIFF files are written to the reflectance folder, and EXIF/XMP
    metadata is copied from the original source file.

    Args:
        cap: A MicaSense capture object containing one image per band.
        get_k_for_time: Callable that returns per-band calibration factors for
            a given capture time.

    Returns:
        None.

    Raises:
        ValueError: If a source filename cannot be parsed into capture and
            band identifiers.
        subprocess.CalledProcessError: If metadata copying via ExifTool fails.
    """
    capture_time = cap.utc_time()
    k_dict = get_k_for_time(capture_time)

    for img in cap.images:
        if img.band_name.upper() == "LWIR" or img.band_index == THERMAL_BAND_ID:
            continue

        rad: FloatArray = img.radiance()

        band_name = img.band_name
        if band_name not in k_dict:
            print(f"No calibration factor for band {band_name} at time {capture_time}, skipping")
            continue

        factor = k_dict[band_name]
        refl: FloatArray = rad * factor

        src_path = Path(img.path)
        parsed = parse_capture_band(src_path)
        if parsed is None:
            raise ValueError(f"Cannot parse capture/band from filename: {src_path.name}")
        capture_id, band_id = parsed

        out_path = make_reflectance_path(capture_id, band_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        iio.imwrite(out_path, refl.astype(np.float32), extension=".tif")
        copy_metadata(src_path, out_path)


def main() -> None:
    """Run the reflectance-conversion workflow for one imagery date.

    The workflow loads panel captures, builds a time-based calibration
    interpolator, prints a panel calibration summary, loads multispectral
    flight imagery, and converts each non-thermal capture to reflectance.

    Returns:
        None.
    """
    print(f"Using config date key: {DATE_KEY}")
    print(f"Imagery root: {IMAGERY_ROOT}")
    print(f"Panel folder: {cal_panel_folder}")
    print(f"Multispectral folder: {multispectral_folder}")
    print(f"Reflectance folder: {reflectance_folder}")
    print(f"Exiftool path: {EXIFTOOL_PATH}")

    reflectance_folder.mkdir(parents=True, exist_ok=True)

    panel_list = load_panel_captures(cal_panel_folder)
    k_func = build_time_interpolator(panel_list)

    print("\nPanel calibration summary:")
    for cap, panel_obj in panel_list:
        capture_time = cap.utc_time()
        time_str = (
            capture_time.isoformat()
            if isinstance(capture_time, datetime)
            else str(capture_time)
        )
        k = compute_k_for_panel(panel_obj)
        print(f"  Panel at {time_str}:")
        for band_name, factor in k.items():
            print(f"    {band_name}: k = {factor:.6f}")

    imgset = load_multispectral_images(multispectral_folder)

    for cap in tqdm(imgset.captures, desc="Processing captures", unit="capture"):
        process_capture_to_reflectance(cap, k_func)

    print("\nDone. Reflectance images written to:")
    print(reflectance_folder)


if __name__ == "__main__":
    main()