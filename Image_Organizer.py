"""Organize multispectral and thermal imagery into a standard folder layout.

This script reads a date-specific configuration, scans source imagery folders,
moves files into a standardized destination structure, and optionally separates
thermal imagery, calibration panel captures, and excluded capture ranges.

The workflow is:

1. Create the destination folder structure.
2. Move source imagery into multispectral and/or thermal folders.
3. Optionally move calibration panel and excluded captures into dedicated
   folders.

The script expects filenames that match the configured regular expression
pattern and uses capture ID and band ID values parsed from each filename.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator
import re
import shutil

from config_loader import load_config, date_config


DATE_KEY = "20260430"  # Change per flight/date as needed.

cfg = load_config()
date_cfg = date_config(cfg, DATE_KEY)
naming_cfg = cfg["naming"]

ROOT_FOLDER = Path(date_cfg["raw_root"])
NEW_FOLDER = Path(date_cfg["imagery_root"])

COMBINE_IMAGERY: bool = cfg["options"]["combine_imagery"]
SEPARATE_THERMAL: bool = cfg["options"]["separate_thermal"]
RUN_EXCLUSIONS: bool = cfg["options"]["run_exclusions"]

CALIBRATION_PANEL_IDS: set[int] = set(cfg["exclusions"]["calibration_panel_ids"])
EXCLUDE_RANGES: list[tuple[int, int]] = [tuple(r) for r in cfg["exclusions"]["exclude_ranges"]]

multispectral_folder = NEW_FOLDER / "multispectral"
thermal_folder = NEW_FOLDER / "thermal"
cal_panel_folder = NEW_FOLDER / "cal_panels"
exclusion_folder = NEW_FOLDER / "exclusions"

SOURCE_PATTERN: str = naming_cfg["source_pattern"]
FILENAME_RE = re.compile(SOURCE_PATTERN, re.IGNORECASE)


def in_ranges(value: int, ranges: list[tuple[int, int]]) -> bool:
    """Return whether a value falls inside any inclusive integer range.

    Args:
        value: Capture ID or other integer value to test.
        ranges: Inclusive ranges expressed as `(start, end)` tuples.

    Returns:
        True if `value` lies within at least one range in `ranges`;
        otherwise False.
    """
    return any(start <= value <= end for start, end in ranges)


def ensure_folders() -> None:
    """Create the destination folder structure if it does not exist.

    This includes the top-level imagery folder and subfolders for
    multispectral imagery, thermal imagery, calibration panels, and
    excluded files.

    Returns:
        None.
    """
    NEW_FOLDER.mkdir(parents=True, exist_ok=True)
    multispectral_folder.mkdir(parents=True, exist_ok=True)
    thermal_folder.mkdir(parents=True, exist_ok=True)
    cal_panel_folder.mkdir(parents=True, exist_ok=True)
    exclusion_folder.mkdir(parents=True, exist_ok=True)


def parse_filename(file_path: Path) -> tuple[int, int] | None:
    """Parse capture ID and band ID from an image filename.

    The filename must match the configured regular expression pattern. The
    function assumes the first two capture groups correspond to capture ID
    and band ID, respectively.

    Args:
        file_path: Path to the file whose name will be parsed.

    Returns:
        A `(capture_id, band_id)` tuple if the filename matches the expected
        pattern, or None if the filename does not match.
    """
    match = FILENAME_RE.match(file_path.name)
    if not match:
        return None
    capture_id = int(match.group(1))
    band_id = int(match.group(2))
    return capture_id, band_id


def move_file_safely(file_path: Path, destination_folder: Path) -> str:
    """Move a file to a destination folder without overwriting existing files.

    Args:
        file_path: Source file to move.
        destination_folder: Destination directory.

    Returns:
        A status string describing the result. One of:
        - `"missing"` if the source file does not exist.
        - `"already_exists"` if the destination file already exists.
        - `"moved"` if the file was successfully moved.
    """
    if not file_path.exists():
        return "missing"

    destination_folder.mkdir(parents=True, exist_ok=True)
    destination_path = destination_folder / file_path.name

    if destination_path.exists():
        return "already_exists"

    shutil.move(str(file_path), str(destination_path))
    return "moved"


def initial_destination_for_source_file(band_id: int) -> Path:
    """Determine the initial destination folder for a source image file.

    Args:
        band_id: Band ID parsed from the filename.

    Returns:
        The destination folder path for the file. If imagery is combined,
        all files go to the multispectral folder. If thermal separation is
        enabled and `band_id == 7`, the file goes to the thermal folder.
        Otherwise, the file goes to the multispectral folder.
    """
    if COMBINE_IMAGERY:
        return multispectral_folder
    if SEPARATE_THERMAL and band_id == 7:
        return thermal_folder
    return multispectral_folder


def move_source_files_to_new_folder() -> dict[str, int]:
    """Move source files from numeric subfolders into the new folder structure.

    The function scans subdirectories under `ROOT_FOLDER`, parses filenames,
    chooses a destination folder based on configuration and band ID, and
    moves files while tracking summary counts.

    Returns:
        A dictionary of summary counts for moved, skipped, missing, and
        already-existing files.
    """
    counts: dict[str, int] = {
        "initial_multispectral": 0,
        "initial_thermal": 0,
        "skipped_name": 0,
        "missing": 0,
        "already_exists": 0,
    }

    for subfolder in sorted(ROOT_FOLDER.iterdir()):
        if not subfolder.is_dir() or not subfolder.name.isdigit():
            continue

        for file_path in sorted(subfolder.iterdir()):
            if not file_path.is_file():
                continue

            parsed = parse_filename(file_path)
            if parsed is None:
                counts["skipped_name"] += 1
                print(f"Skipping unrecognized filename: {file_path}")
                continue

            _, band_id = parsed
            destination_folder = initial_destination_for_source_file(band_id)
            result = move_file_safely(file_path, destination_folder)

            if result == "moved":
                if destination_folder == thermal_folder:
                    counts["initial_thermal"] += 1
                else:
                    counts["initial_multispectral"] += 1
            else:
                counts[result] += 1
                print(f"{result}, skipped: {file_path}")

    return counts


def files_to_review_in_new_folder() -> Generator[Path, None, None]:
    """Yield files in destination folders that should be checked for exclusion.

    The folders reviewed depend on the imagery organization settings. When
    imagery is combined, only the multispectral folder is reviewed. When
    thermal imagery is separated, both multispectral and thermal folders are
    reviewed.

    Yields:
        File paths from the destination folders that should be checked for
        calibration panel or exclusion handling.
    """
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
    """Move calibration panel and excluded captures into dedicated folders.

    Files already placed in the new folder structure are re-checked by
    capture ID. Files matching configured calibration panel IDs are moved
    to the calibration panel folder. Files whose capture IDs fall within
    configured exclusion ranges are moved to the exclusions folder.

    Returns:
        A dictionary of summary counts for moved, skipped, missing, and
        already-existing files.
    """
    counts: dict[str, int] = {
        "moved_to_cal_panels": 0,
        "moved_to_exclusions": 0,
        "skipped_name": 0,
        "missing": 0,
        "already_exists": 0,
    }

    for file_path in list(files_to_review_in_new_folder()):
        parsed = parse_filename(file_path)
        if parsed is None:
            counts["skipped_name"] += 1
            print(f"Skipping unrecognized filename in new folder: {file_path}")
            continue

        capture_id, _ = parsed

        if capture_id in CALIBRATION_PANEL_IDS:
            result = move_file_safely(file_path, cal_panel_folder)
            if result == "moved":
                counts["moved_to_cal_panels"] += 1
            else:
                counts[result] += 1
                print(f"{result}, skipped: {file_path}")
            continue

        if in_ranges(capture_id, EXCLUDE_RANGES):
            result = move_file_safely(file_path, exclusion_folder)
            if result == "moved":
                counts["moved_to_exclusions"] += 1
            else:
                counts[result] += 1
                print(f"{result}, skipped: {file_path}")

    return counts


def main() -> None:
    """Run the imagery organization workflow.

    This creates the destination folder structure, performs the initial file
    move, optionally applies exclusions and calibration panel separation,
    and prints summary counts for each processing step.

    Returns:
        None.
    """
    ensure_folders()

    print("Step 1: Moving source files into the new folder structure...")
    step1_counts = move_source_files_to_new_folder()

    step2_counts: dict[str, int] = {}
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


if __name__ == "__main__":
    main()