from pathlib import Path
import re
import subprocess
import json

import matplotlib.pyplot as plt

IMAGE_FOLDER = Path(r"C:\Users\CND367\Documents\MicaSense\Gold_Creek\20260430\Imagery\multispectral")
BAND_TO_PLOT = 1
POINT_SIZE = 18
CMAP = "viridis"

FILENAME_RE = re.compile(r"^IMG_(\d+)_(\d+)\.tif$", re.IGNORECASE)


def get_exiftool_metadata(image_path: Path) -> dict:
    result = subprocess.run(
        ["exiftool", "-j", str(image_path)],
        capture_output=True,
        text=True,
        check=True
    )
    data = json.loads(result.stdout)
    return data[0]


def get_lat_lon(meta: dict):
    lat = meta.get("GPSLatitude")
    lon = meta.get("GPSLongitude")

    if lat is None or lon is None:
        return None, None

    lat_ref = meta.get("GPSLatitudeRef", "N")
    lon_ref = meta.get("GPSLongitudeRef", "E")

    if lat_ref.upper() == "S":
        lat = -abs(float(lat))
    else:
        lat = float(lat)

    if lon_ref.upper() == "W":
        lon = -abs(float(lon))
    else:
        lon = float(lon)

    return lat, lon


def main():
    image_paths = []
    lats = []
    lons = []
    capture_ids = []

    for image_path in sorted(IMAGE_FOLDER.iterdir()):
        if not image_path.is_file():
            continue

        match = FILENAME_RE.match(image_path.name)
        if not match:
            continue

        capture_id = int(match.group(1))
        band_id = int(match.group(2))

        if band_id != BAND_TO_PLOT:
            continue

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
    sc = ax.scatter(
        lons,
        lats,
        c=capture_ids,
        cmap=CMAP,
        s=POINT_SIZE,
        edgecolors="black",
        linewidths=0.2
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