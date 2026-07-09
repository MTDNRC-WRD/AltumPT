"""Organize multispectral and thermal imagery into a standard folder layout.

This script reads project configuration from a TOML file, scans source
imagery folders, moves files into a standardized destination structure, and
optionally separates thermal imagery, calibration panel captures, and
excluded capture ranges.

The workflow is:

1. Create the destination folder structure.
2. Move source imagery into the multispectral folder.
3. When combining imagery from multiple SET folders, renumber later SETs so
   capture IDs form one continuous sequence with no filename collisions.
4. Optionally separate thermal imagery into a dedicated folder.
5. Optionally move calibration panel and excluded captures into dedicated
   folders.

The script expects filenames that match the configured regular expression
pattern and uses capture ID and band ID values parsed from each filename.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator
import re
import shutil

from config_loader import folder_config, load_config


cfg = load_config()
folders_cfg = folder_config(cfg)
naming_cfg = cfg["naming"]

ROOT_FOLDER = Path(folders_cfg["raw_root"])
NEW_FOLDER = Path(folders_cfg["imagery_root"])

COMBINE_IMAGERY: bool = cfg["options"]["combine_imagery"]
SEPARATE_THERMAL: bool = cfg["options"]["separate_thermal"]
RUN_EXCLUSIONS: bool = cfg["options"]["run_exclusions"]

CALIBRATION_PANEL_IDS: set[int] = set(cfg["exclusions"]["calibration_panel_ids"])
EXCLUDE_RANGES: list[tuple[int, int]] = [
    tuple(item) for item in cfg["exclusions"]["exclude_ranges"]
]

multispectral_folder = NEW_FOLDER / "multispectral"
thermal_folder = NEW_FOLDER / "thermal"
cal_panel_folder = NEW_FOLDER / "cal_panels"
exclusion_folder = NEW_FOLDER / "exclusions"

SOURCE_PATTERN: str = naming_cfg["source_pattern"]
FILENAME_RE = re.compile(SOURCE_PATTERN, re.IGNORECASE)
SET_FOLDER_RE = re.compile(r"^(\d+)SET$", re.IGNORECASE)

THERMAL_BAND_ID = 7


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


def format_output_name(file_path: Path, capture_id: int, band_id: int) -> str:
    """Create an output filename preserving the original extension.

    Args:
        file_path: Original source file path.
        capture_id: Capture ID to write into the destination filename.
        band_id: Band ID to write into the destination filename.

    Returns:
        A renamed output filename using the adjusted capture ID and original
        file extension.
    """
    return f"IMG_{capture_id}_{band_id}{file_path.suffix}"


def move_file_safely(
    file_path: Path,
    destination_folder: Path,
    destination_name: str | None = None,
) -> str:
    """Move a file to a destination folder without overwriting existing files.

    Args:
        file_path: Source file to move.
        destination_folder: Destination directory.
        destination_name: Optional destination filename. When omitted, the
            original filename is preserved.

    Returns:
        A status string describing the result. One of:
        - "missing" if the source file does not exist.
        - "already_exists" if the destination file already exists.
        - "moved" if the file was successfully moved.
    """
    if not file_path.exists():
        return "missing"

    destination_folder.mkdir(parents=True, exist_ok=True)
    final_name = destination_name or file_path.name
    destination_path = destination_folder / final_name

    if destination_path.exists():
        return "already_exists"

    shutil.move(str(file_path), str(destination_path))
    return "moved"


def initial_destination_for_source_file() -> Path:
    """Return the initial destination for source imagery.

    Returns:
        The multispectral folder, which serves as the initial staging area
        before optional thermal separation and exclusion handling.
    """
    return multispectral_folder


def iter_set_folders() -> list[Path]:
    """Return SET folders under the raw root sorted by numeric prefix.

    Expected SET folder names follow the pattern `####SET`, such as
    `0001SET` or `0002SET`.

    Returns:
        A list of SET folder paths sorted by their numeric prefix.

    Raises:
        FileNotFoundError: If the raw imagery root does not exist.
    """
    if not ROOT_FOLDER.exists():
        raise FileNotFoundError(f"Raw imagery directory does not exist: {ROOT_FOLDER}")

    set_folders: list[tuple[int, Path]] = []
    for path in ROOT_FOLDER.iterdir():
        if not path.is_dir():
            continue
        match = SET_FOLDER_RE.match(path.name)
        if not match:
            continue
        set_number = int(match.group(1))
        set_folders.append((set_number, path))

    set_folders.sort(key=lambda item: item[0])
    return [path for _, path in set_folders]


def iter_source_files(root: Path) -> Generator[Path, None, None]:
    """Yield source files recursively from a root folder.

    Args:
        root: Folder to scan recursively.

    Yields:
        File paths for all files found beneath the provided root folder.
    """
    for file_path in sorted(root.rglob("*")):
        if file_path.is_file():
            yield file_path


def move_source_files_to_new_folder() -> dict[str, int]:
    """Move source files into the new folder structure.

    When combining imagery from multiple SET folders, this function processes
    SET folders in ascending numeric order and renumbers capture IDs in later
    SET folders so destination filenames remain unique and continuous.

    Returns:
        A dictionary of summary counts for moved, skipped, missing,
        already-existing, and renumbered files.
    """
    counts: dict[str, int] = {
        "initial_multispectral": 0,
        "skipped_name": 0,
        "missing": 0,
        "already_exists": 0,
        "renamed_with_offset": 0,
        "set_folders_processed": 0,
    }

    destination_folder = initial_destination_for_source_file()
    capture_offset = 0

    set_folders = iter_set_folders()

    if set_folders:
        for set_folder in set_folders:
            counts["set_folders_processed"] += 1
            files_in_set = list(iter_source_files(set_folder))

            parsed_files: list[tuple[Path, int, int]] = []
            max_capture_id_in_set = -1

            for file_path in files_in_set:
                parsed = parse_filename(file_path)
                if parsed is None:
                    counts["skipped_name"] += 1
                    print(f"Skipping unrecognized filename: {file_path}")
                    continue

                capture_id, band_id = parsed
                parsed_files.append((file_path, capture_id, band_id))
                max_capture_id_in_set = max(max_capture_id_in_set, capture_id)

            print(
                f"Processing SET folder {set_folder.name} with capture offset {capture_offset}"
            )

            for file_path, capture_id, band_id in parsed_files:
                adjusted_capture_id = capture_id + capture_offset
                destination_name = format_output_name(
                    file_path=file_path,
                    capture_id=adjusted_capture_id,
                    band_id=band_id,
                )
                result = move_file_safely(
                    file_path,
                    destination_folder,
                    destination_name=destination_name,
                )

                if result == "moved":
                    counts["initial_multispectral"] += 1
                    if adjusted_capture_id != capture_id:
                        counts["renamed_with_offset"] += 1
                else:
                    counts[result] += 1
                    print(f"{result}, skipped: {file_path}")

            if COMBINE_IMAGERY and max_capture_id_in_set >= 0:
                capture_offset += max_capture_id_in_set + 1
    else:
        print("No SET folders found; scanning raw root recursively.")
        for file_path in iter_source_files(ROOT_FOLDER):
            parsed = parse_filename(file_path)
            if parsed is None:
                counts["skipped_name"] += 1
                print(f"Skipping unrecognized filename: {file_path}")
                continue

            capture_id, band_id = parsed
            destination_name = format_output_name(file_path, capture_id, band_id)
            result = move_file_safely(
                file_path,
                destination_folder,
                destination_name=destination_name,
            )

            if result == "moved":
                counts["initial_multispectral"] += 1
            else:
                counts[result] += 1
                print(f"{result}, skipped: {file_path}")

    return counts


def separate_thermal_images() -> dict[str, int]:
    """Move thermal-band images from multispectral to thermal.

    Returns:
        A dictionary summarizing how many files were moved or skipped.
    """
    counts: dict[str, int] = {
        "moved_to_thermal": 0,
        "skipped_name": 0,
        "missing": 0,
        "already_exists": 0,
    }

    for file_path in sorted(multispectral_folder.iterdir()):
        if not file_path.is_file():
            continue

        parsed = parse_filename(file_path)
        if parsed is None:
            counts["skipped_name"] += 1
            print(f"Skipping unrecognized filename in multispectral folder: {file_path}")
            continue

        _, band_id = parsed
        if band_id != THERMAL_BAND_ID:
            continue

        result = move_file_safely(file_path, thermal_folder)
        if result == "moved":
            counts["moved_to_thermal"] += 1
        else:
            counts[result] += 1
            print(f"{result}, skipped: {file_path}")

    return counts


def files_to_review_in_new_folder() -> Generator[Path, None, None]:
    """Yield files in destination folders that should be checked for exclusion.

    Yields:
        File paths from the destination folders that should be checked for
        calibration panel or exclusion handling.
    """
    folders = [multispectral_folder]
    if SEPARATE_THERMAL:
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
    move, optionally separates thermal imagery, optionally applies
    calibration panel and exclusion handling, and prints summary counts for
    each processing step.

    Returns:
        None.
    """
    ensure_folders()

    print(f"Raw imagery root: {ROOT_FOLDER}")
    print(f"Organized imagery root: {NEW_FOLDER}")

    print("\nStep 1: Moving source files into the new folder structure...")
    step1_counts = move_source_files_to_new_folder()

    thermal_counts: dict[str, int] = {}
    if SEPARATE_THERMAL:
        print("\nStep 2: Separating thermal imagery...")
        thermal_counts = separate_thermal_images()
    else:
        print("\nStep 2 skipped because SEPARATE_THERMAL = False")

    step3_counts: dict[str, int] = {}
    if RUN_EXCLUSIONS:
        print("\nStep 3: Removing calibration panels and excluded capture ranges...")
        step3_counts = apply_exclusions_and_cal_panels()
    else:
        print("\nStep 3 skipped because RUN_EXCLUSIONS = False")

    print("\nDone.\n")

    print("Initial move summary:")
    for key, value in step1_counts.items():
        print(f"{key}: {value}")

    if SEPARATE_THERMAL:
        print("\nThermal separation summary:")
        for key, value in thermal_counts.items():
            print(f"{key}: {value}")

    if RUN_EXCLUSIONS:
        print("\nExclusion/panel summary:")
        for key, value in step3_counts.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()