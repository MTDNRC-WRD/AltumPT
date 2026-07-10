"""Plot QA GPS locations for a selected multispectral band.

This script scans a multispectral imagery folder, selects files matching a
configured band ID, extracts GPS metadata using ExifTool, and plots the image
locations in capture order using Matplotlib.

Each plotted point is colored by capture ID, connected by a thin line in
capture order, and labeled with its capture ID for visual QA of image
positioning and sequencing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
import subprocess

import matplotlib.pyplot as plt
from tqdm import tqdm

from config_loader import folder_config, load_config


cfg = load_config()
folders_cfg = folder_config(cfg)
naming_cfg = cfg["naming"]

IMAGERY_ROOT = Path(folders_cfg["imagery_root"])
IMAGE_FOLDER = IMAGERY_ROOT / "multispectral"

EXIFTOOL_PATH = Path(cfg["paths"]["exiftool"])
BAND_TO_PLOT: int = naming_cfg["qa_band_to_plot"]
POINT_SIZE: int = 18
CMAP: str = "viridis"

SOURCE_PATTERN: str = naming_cfg["source_pattern"]
FILENAME_RE = re.compile(SOURCE_PATTERN, re.IGNORECASE)


def get_exiftool_metadata(image_path: Path) -> dict[str, Any]:
    """Extract selected GPS metadata from an image using ExifTool.

    The function calls ExifTool with numeric output enabled and requests
    latitude and longitude in JSON format.

    Args:
        image_path: Path to the image file to inspect.

    Returns:
        A metadata dictionary for the image. The dictionary may include
        `GPSLatitude` and `GPSLongitude` keys if GPS metadata is present.

    Raises:
        subprocess.CalledProcessError: If ExifTool returns a non-zero exit code.
        json.JSONDecodeError: If ExifTool output cannot be parsed as JSON.
        IndexError: If the JSON output is empty.
    """
    result = subprocess.run(
        [
            str(EXIFTOOL_PATH),
            "-n",
            "-GPSLatitude",
            "-GPSLongitude",
            "-j",
            str(image_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data[0]


def get_lat_lon(meta: dict[str, Any]) -> tuple[float | None, float | None]:
    """Extract latitude and longitude from an ExifTool metadata dictionary.

    Args:
        meta: Metadata dictionary returned by ExifTool.

    Returns:
        A `(latitude, longitude)` tuple. If either GPS value is missing,
        the function returns `(None, None)`.
    """
    lat = meta.get("GPSLatitude")
    lon = meta.get("GPSLongitude")

    if lat is None or lon is None:
        return None, None

    return float(lat), float(lon)


def parse_capture_and_band(image_path: Path) -> tuple[int, int] | None:
    """Parse capture ID and band ID from an image filename.

    Args:
        image_path: Path to the image file whose name should be parsed.

    Returns:
        A `(capture_id, band_id)` tuple if the filename matches the configured
        source pattern, or None if the filename does not match.
    """
    match = FILENAME_RE.match(image_path.name)
    if not match:
        return None

    capture_id = int(match.group(1))
    band_id = int(match.group(2))
    return capture_id, band_id


def iter_band_files() -> list[tuple[Path, int]]:
    """Collect files for the configured QA band from the multispectral folder.

    The search is recursive so files in nested subfolders are included.

    Returns:
        A list of `(image_path, capture_id)` tuples for images whose band ID
        matches the configured QA band, sorted by numeric capture ID.

    Raises:
        FileNotFoundError: If the multispectral folder does not exist.
    """
    if not IMAGE_FOLDER.exists():
        raise FileNotFoundError(f"Multispectral folder does not exist: {IMAGE_FOLDER}")

    band_files: list[tuple[Path, int]] = []

    for image_path in IMAGE_FOLDER.rglob("*"):
        if not image_path.is_file():
            continue

        parsed = parse_capture_and_band(image_path)
        if parsed is None:
            continue

        capture_id, band_id = parsed
        if band_id == BAND_TO_PLOT:
            band_files.append((image_path, capture_id))

    band_files.sort(key=lambda item: item[1])
    return band_files


def main() -> None:
    """Read image GPS metadata and plot capture locations for QA review.

    The script filters imagery to the configured band, reads GPS metadata
    with ExifTool, and creates a scatter plot with a color ramp by capture
    ID. Points are connected in capture order and labeled with capture IDs
    to help verify image sequencing and spatial continuity.

    Returns:
        None.

    Raises:
        RuntimeError: If no GPS-enabled images are found for the selected band.
    """
    records: list[tuple[int, float, float, Path]] = []

    band_files = iter_band_files()

    print(f"Multispectral folder: {IMAGE_FOLDER}")
    print(f"Found {len(band_files)} band {BAND_TO_PLOT} images")

    for image_path, capture_id in tqdm(
        band_files,
        total=len(band_files),
        desc="Reading GPS metadata",
        unit="image",
    ):
        try:
            meta = get_exiftool_metadata(image_path)
            lat, lon = get_lat_lon(meta)
        except Exception as exc:
            print(f"Failed to read metadata for {image_path.name}: {exc}")
            continue

        if lat is None or lon is None:
            print(f"No GPS found for {image_path.name}")
            continue

        records.append((capture_id, lon, lat, image_path))

    if not records:
        raise RuntimeError(f"No GPS-enabled band {BAND_TO_PLOT} images found.")

    records.sort(key=lambda item: item[0])

    capture_ids = [item[0] for item in records]
    lons = [item[1] for item in records]
    lats = [item[2] for item in records]
    image_paths = [item[3] for item in records]

    fig, ax = plt.subplots(figsize=(10, 8))

    ax.plot(
        lons,
        lats,
        color="gray",
        linewidth=0.6,
        alpha=0.7,
        zorder=1,
    )

    sc = ax.scatter(
        lons,
        lats,
        c=capture_ids,
        cmap=CMAP,
        s=POINT_SIZE * 2,
        edgecolors="black",
        linewidths=0.25,
        zorder=2,
    )

    for capture_id, lon, lat, _image_path in records:
        ax.annotate(
            str(capture_id),
            xy=(lon, lat),
            xytext=(3, 3),
            textcoords="offset points",
            fontsize=6,
            color="black",
            alpha=0.85,
            zorder=3,
        )

    cbar = plt.colorbar(sc, ax=ax)
    cbar.set_label("Capture ID")

    ax.set_title(f"Band {BAND_TO_PLOT} GPS Locations")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()