from pathlib import Path
import re
import shutil

ROOT_FOLDER = Path(r'C:\Users\CND367\Documents\MicaSense\Gold_Creek\20260430\0000SET')
NEW_FOLDER = Path(r'C:\Users\CND367\Documents\MicaSense\Gold_Creek\20260430\Imagery')

SEPARATE_THERMAL = True
CALIBRATION_PANEL_IDS = {0, 1, 2, 1399}
EXCLUDE_RANGES = [(0, 54), (627, 651), (1218, 1252), (1374, 1399)]

multispectral_folder = NEW_FOLDER / 'multispectral'
thermal_folder = NEW_FOLDER / 'thermal'
cal_panel_folder = NEW_FOLDER / 'cal_panels'
exclusion_folder = NEW_FOLDER / 'exclusions'

FILENAME_RE = re.compile(r'^IMG_(\d+)_(\d+)\.tif$', re.IGNORECASE)


def in_ranges(value: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= value <= end for start, end in ranges)


def classify_file(capture_id: int, band_id: int) -> Path:
    if capture_id in CALIBRATION_PANEL_IDS:
        return cal_panel_folder
    if in_ranges(capture_id, EXCLUDE_RANGES):
        return exclusion_folder
    if SEPARATE_THERMAL and band_id == 7:
        return thermal_folder
    return multispectral_folder


def ensure_folders() -> None:
    NEW_FOLDER.mkdir(parents=True, exist_ok=True)
    multispectral_folder.mkdir(parents=True, exist_ok=True)
    thermal_folder.mkdir(parents=True, exist_ok=True)
    cal_panel_folder.mkdir(parents=True, exist_ok=True)
    exclusion_folder.mkdir(parents=True, exist_ok=True)


def move_file_safely(file_path: Path, destination_folder: Path) -> str:
    if not file_path.exists():
        return 'missing'

    destination_path = destination_folder / file_path.name

    if destination_path.exists():
        return 'already_exists'

    shutil.move(str(file_path), str(destination_path))
    return 'moved'


def main() -> None:
    ensure_folders()

    moved_counts = {
        'multispectral': 0,
        'thermal': 0,
        'cal_panels': 0,
        'exclusions': 0,
        'skipped_name': 0,
        'missing': 0,
        'already_exists': 0,
    }

    for subfolder in sorted(ROOT_FOLDER.iterdir()):
        if not subfolder.is_dir() or not subfolder.name.isdigit():
            continue

        for file_path in sorted(subfolder.iterdir()):
            if not file_path.is_file():
                continue

            match = FILENAME_RE.match(file_path.name)
            if not match:
                moved_counts['skipped_name'] += 1
                print(f"Skipping unrecognized filename: {file_path}")
                continue

            capture_id = int(match.group(1))
            band_id = int(match.group(2))

            destination_folder = classify_file(capture_id, band_id)
            result = move_file_safely(file_path, destination_folder)

            if result == 'moved':
                if destination_folder == multispectral_folder:
                    moved_counts['multispectral'] += 1
                elif destination_folder == thermal_folder:
                    moved_counts['thermal'] += 1
                elif destination_folder == cal_panel_folder:
                    moved_counts['cal_panels'] += 1
                elif destination_folder == exclusion_folder:
                    moved_counts['exclusions'] += 1
            elif result == 'missing':
                moved_counts['missing'] += 1
                print(f"Missing, skipped: {file_path}")
            elif result == 'already_exists':
                moved_counts['already_exists'] += 1
                print(f"Already exists, skipped: {destination_folder / file_path.name}")

    print("\nDone.")
    for key, value in moved_counts.items():
        print(f"{key}: {value}")


if __name__ == '__main__':
    main()