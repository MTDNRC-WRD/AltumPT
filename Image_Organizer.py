from pathlib import Path
import re
import shutil

ROOT_FOLDER = Path(r'C:\Users\CND367\Documents\MicaSense\Gold_Creek\20260430\0000SET')
NEW_FOLDER = Path(r'C:\Users\CND367\Documents\MicaSense\Gold_Creek\20260430\Imagery')

COMBINE_IMAGERY = True
SEPARATE_THERMAL = True

RUN_EXCLUSIONS = True
CALIBRATION_PANEL_IDS = {0, 1, 2, 1399}
EXCLUDE_RANGES = [(0, 54), (627, 651), (1218, 1252), (1374, 1399)]

multispectral_folder = NEW_FOLDER / 'multispectral'
thermal_folder = NEW_FOLDER / 'thermal'
cal_panel_folder = NEW_FOLDER / 'cal_panels'
exclusion_folder = NEW_FOLDER / 'exclusions'

FILENAME_RE = re.compile(r'^IMG_(\d+)_(\d+)\.tif$', re.IGNORECASE)


def in_ranges(value: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= value <= end for start, end in ranges)


def ensure_folders() -> None:
    NEW_FOLDER.mkdir(parents=True, exist_ok=True)
    multispectral_folder.mkdir(parents=True, exist_ok=True)
    thermal_folder.mkdir(parents=True, exist_ok=True)
    cal_panel_folder.mkdir(parents=True, exist_ok=True)
    exclusion_folder.mkdir(parents=True, exist_ok=True)


def parse_filename(file_path: Path):
    match = FILENAME_RE.match(file_path.name)
    if not match:
        return None
    capture_id = int(match.group(1))
    band_id = int(match.group(2))
    return capture_id, band_id


def move_file_safely(file_path: Path, destination_folder: Path) -> str:
    if not file_path.exists():
        return 'missing'

    destination_folder.mkdir(parents=True, exist_ok=True)
    destination_path = destination_folder / file_path.name

    if destination_path.exists():
        return 'already_exists'

    shutil.move(str(file_path), str(destination_path))
    return 'moved'


def initial_destination_for_source_file(band_id: int) -> Path:
    if COMBINE_IMAGERY:
        return multispectral_folder
    if SEPARATE_THERMAL and band_id == 7:
        return thermal_folder
    return multispectral_folder


def move_source_files_to_new_folder() -> dict[str, int]:
    counts = {
        'initial_multispectral': 0,
        'initial_thermal': 0,
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

            parsed = parse_filename(file_path)
            if parsed is None:
                counts['skipped_name'] += 1
                print(f"Skipping unrecognized filename: {file_path}")
                continue

            _, band_id = parsed
            destination_folder = initial_destination_for_source_file(band_id)
            result = move_file_safely(file_path, destination_folder)

            if result == 'moved':
                if destination_folder == thermal_folder:
                    counts['initial_thermal'] += 1
                else:
                    counts['initial_multispectral'] += 1
            else:
                counts[result] += 1
                print(f"{result}, skipped: {file_path}")

    return counts


def files_to_review_in_new_folder():
    folders = [multispectral_folder]
    if not COMBINE_IMAGERY and SEPARATE_THERMAL:
        folders.append(thermal_folder)

    for folder in folders:
        if not folder.exists():
            continue
        for file_path in sorted(folder.iterdir()):
            if file_path.is_file():
                yield file_path


def apply_exclusions_and_cal_panels() -> dict[str, int]:
    counts = {
        'moved_to_cal_panels': 0,
        'moved_to_exclusions': 0,
        'skipped_name': 0,
        'missing': 0,
        'already_exists': 0,
    }

    for file_path in list(files_to_review_in_new_folder()):
        parsed = parse_filename(file_path)
        if parsed is None:
            counts['skipped_name'] += 1
            print(f"Skipping unrecognized filename in new folder: {file_path}")
            continue

        capture_id, _ = parsed

        if capture_id in CALIBRATION_PANEL_IDS:
            result = move_file_safely(file_path, cal_panel_folder)
            if result == 'moved':
                counts['moved_to_cal_panels'] += 1
            else:
                counts[result] += 1
                print(f"{result}, skipped: {file_path}")
            continue

        if in_ranges(capture_id, EXCLUDE_RANGES):
            result = move_file_safely(file_path, exclusion_folder)
            if result == 'moved':
                counts['moved_to_exclusions'] += 1
            else:
                counts[result] += 1
                print(f"{result}, skipped: {file_path}")

    return counts


def main() -> None:
    ensure_folders()

    print("Step 1: Moving source files into the new folder structure...")
    step1_counts = move_source_files_to_new_folder()

    step2_counts = {}
    if RUN_EXCLUSIONS:
        print("\nStep 2: Removing calibration panels and excluded capture ranges...")
        step2_counts = apply_exclusions_and_cal_panels()
    else:
        print("\nStep 2 skipped because RUN_EXCLUSIONS = False")

    print("\nDone.\n")

    print("Initial move summary:")
    for key, value in step1_counts.items():
        print(f"{key}: {value}")

    if RUN_EXCLUSIONS:
        print("\nExclusion/panel summary:")
        for key, value in step2_counts.items():
            print(f"{key}: {value}")


if __name__ == '__main__':
    main()