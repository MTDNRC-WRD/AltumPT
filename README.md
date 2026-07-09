# Multispectral Imagery Processing Workflow

This project organizes MicaSense imagery, performs basic spatial quality assurance, and converts multispectral imagery to reflectance using calibration panel captures.

It is designed for a date-driven workflow where paths, naming patterns, and processing options are controlled through a shared configuration file and a date-specific lookup.

## Overview

The workflow is organized into three main scripts:

1. **Imagery organizer**: moves raw imagery into a standardized folder structure.
2. **Image GPS plotter**: reads image GPS metadata and plots capture locations for QA.
3. **Reflectance converter**: computes calibration factors from panel imagery and writes reflectance TIFFs.

The codebase is built around a configuration-driven pattern using `load_config()` and `date_config()` so the same scripts can be reused across flight dates with minimal edits.

## Project structure

A typical output structure looks like this:

```text
imagery_root/
├── multispectral/
├── thermal/
├── cal_panels/
├── exclusions/
└── reflectance/
```

Common directory roles:

- `multispectral/`: non-thermal imagery used for QA plotting and reflectance conversion.
- `thermal/`: thermal imagery separated during organization when enabled.
- `cal_panels/`: calibration panel captures used for radiometric calibration.
- `exclusions/`: captures removed based on configured exclusion ranges or calibration panel IDs.
- `reflectance/`: output reflectance TIFFs.

## Configuration

The scripts expect a shared configuration source exposed through `config_loader.py`.

At minimum, the configuration should provide:

- Date-specific roots such as `raw_root` and `imagery_root`.
- Naming settings such as `source_pattern`, `reflectance_pattern`, and `qa_band_to_plot`.
- Tool paths such as `exiftool`.
- Workflow options such as `combine_imagery`, `separate_thermal`, and `run_exclusions`.
- Exclusion rules such as `calibration_panel_ids` and `exclude_ranges`.

Each script uses a `DATE_KEY` value to select the active flight date.

## Scripts

### 1. Imagery organizer

This script scans the raw imagery root, parses capture and band IDs from filenames, and moves files into the new imagery structure.

Core behaviors:

- Creates the required destination folders.
- Moves files from numeric subfolders under the raw root.
- Sends thermal images to `thermal/` when separation is enabled.
- Optionally moves calibration panel captures to `cal_panels/`.
- Optionally moves excluded capture ranges to `exclusions/`.

Use this first to standardize imagery before QA or radiometric processing.

### 2. Image GPS plotter

This script reads GPS metadata from images in the `multispectral/` folder using ExifTool and plots image locations with Matplotlib.

Core behaviors:

- Filters files by a configured band ID.
- Extracts numeric GPS latitude and longitude from EXIF metadata.
- Plots locations in capture order.
- Colors points by capture ID.
- Labels points for quick spatial QA.

This is useful for checking flight continuity, ordering issues, and missing GPS tags.

### 3. Reflectance converter

This script loads calibration panel imagery and flight imagery, computes per-band radiance-to-reflectance calibration factors, and writes reflectance TIFF outputs.

Core behaviors:

- Reads panel captures from `cal_panels/`.
- Computes calibration factors from panel radiance and known panel reflectance.
- Interpolates calibration factors over time when multiple panel captures are available.
- Skips thermal/LWIR bands.
- Writes reflectance TIFFs to `reflectance/`.
- Copies EXIF/XMP metadata from source imagery using ExifTool.

This script should be run after imagery has been organized and calibration panel captures are available.

## Typical workflow

A common processing order is:

1. Set the correct `DATE_KEY` in the scripts.
2. Confirm the date-specific paths and naming rules in the config.
3. Run the imagery organizer.
4. Review the GPS plot for the configured QA band.
5. Confirm calibration panel images were moved into `cal_panels/`.
6. Run the reflectance conversion script.

## Dependencies

This project uses a mix of standard library and third-party packages.

Python packages used across the scripts include:

- `numpy`
- `matplotlib`
- `tqdm`
- `imageio`
- `micasense`

External tools:

- **ExifTool** for metadata extraction and metadata copying.

## Assumptions and conventions

The workflow assumes:

- Image filenames follow a predictable naming pattern that exposes capture ID and band ID.
- The configured regex patterns correctly match the source imagery naming scheme.
- Calibration panel images are available and identifiable.
- Thermal imagery uses band ID `7` or band name `LWIR`.
- ExifTool is installed and the configured path is valid.

## Notes for maintenance

A few practical points make the project easier to maintain:

- Keep all date-specific paths in config rather than hard-coding them in multiple places.
- Validate the filename regex pattern on a small sample before bulk moves.
- Run the GPS plotter after organization to catch missing or malformed imagery early.
- Keep thermal handling separate from multispectral reflectance processing unless a downstream thermal workflow is added.
- If imagery may be stored in nested subfolders, update the plotter and any other file scanners to use recursive search rather than `iterdir()`.

## Suggested next improvements

Potential enhancements for the project include:

- Adding a command-line interface so `DATE_KEY` and options can be passed at runtime.
- Writing logs to file instead of relying only on console output.
- Saving QA plots automatically to a `figures/` or `qa/` folder.
- Adding recursive search options for nested imagery directories.
- Adding unit tests for filename parsing and folder-routing logic.

## Troubleshooting

### No files are moved

Check that:

- `raw_root` points to the expected source directory.
- Source subfolders are numeric if the organizer expects numeric folder names.
- The configured filename regex matches your imagery filenames.

### GPS plot is empty

Check that:

- Images exist in `multispectral/`.
- The selected QA band exists in the dataset.
- ExifTool is available and readable at the configured path.
- The images actually contain GPS metadata.

### Reflectance outputs are missing

Check that:

- Calibration panel imagery exists in `cal_panels/`.
- Panel detection succeeds for the relevant bands.
- Source filenames can be parsed into capture and band IDs.
- ExifTool is installed so metadata copying does not fail.

## License and use

Add your preferred license and attribution terms here if this project will be shared outside your internal workflow.
