from pathlib import Path
import re
import subprocess
import json
from tqdm import tqdm
import matplotlib.pyplot as plt

IMAGE_FOLDER = Path(r"C:\Users\CND367\Documents\MicaSense\Gold_Creek\20260430\Imagery\multispectral")
EXIFTOOL_PATH = r"C:\Users\CND367\Documents\python_scripts\exiftool\exiftool.exe"
BAND_TO_PLOT = 1
POINT_SIZE = 18
CMAP = "viridis"

FILENAME_RE = re.compile(r"^IMG_(\d+)_(\d+)\.tif$", re.IGNORECASE)


def get_exiftool_metadata(image_path: Path) -> dict:
    result = subprocess.run(
        [
            EXIFTOOL_PATH,
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


def get_lat_lon(meta: dict):
    lat = meta.get("GPSLatitude")
    lon = meta.get("GPSLongitude")

    if lat is None or lon is None:
        return None, None

    return float(lat), float(lon)


def main():
    image_paths = []
    lats = []
    lons = []
    capture_ids = []

    band_files = []

    for image_path in sorted(IMAGE_FOLDER.iterdir()):
        if not image_path.is_file():
            continue

        match = FILENAME_RE.match(image_path.name)
        if not match:
            continue

        capture_id = int(match.group(1))
        band_id = int(match.group(2))

        if band_id == BAND_TO_PLOT:
            band_files.append((image_path, capture_id))

    print(f"Found {len(band_files)} band {BAND_TO_PLOT} images")

    for image_path, capture_id in tqdm(
        band_files,
        total=len(band_files),
        desc="Reading GPS metadata",
        unit="image"
    ):
        try:
            meta = get_exiftool_metadata(image_path)
            lat, lon = get_lat_lon(meta)
        except Exception as e:
            print(f"Failed to read metadata for {image_path.name}: {e}")
            continue

        if lat is None or lon is None:
            print(f"No GPS found for {image_path.name}")
            continue

        image_paths.append(image_path)
        lats.append(lat)
        lons.append(lon)
        capture_ids.append(capture_id)

    if not lats:
        raise RuntimeError("No GPS-enabled band 1 images found.")

    fig, ax = plt.subplots(figsize=(10, 8))

    # thin connecting line in capture order
    ax.plot(
        lons,
        lats,
        color="gray",
        linewidth=0.6,
        alpha=0.7,
        zorder=1
    )

    # larger scatter points with color ramp by capture ID
    sc = ax.scatter(
        lons,
        lats,
        c=capture_ids,
        cmap=CMAP,
        s=36,
        edgecolors="black",
        linewidths=0.25,
        zorder=2
    )

    # small labels next to each point
    for lon, lat, capture_id in zip(lons, lats, capture_ids):
        ax.annotate(
            str(capture_id),
            xy=(lon, lat),
            xytext=(3, 3),
            textcoords="offset points",
            fontsize=6,
            color="black",
            alpha=0.85,
            zorder=3
        )

    cbar = plt.colorbar(sc, ax=ax)
    cbar.set_label("Capture ID")

    ax.set_title("Band 1 GPS Locations")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()