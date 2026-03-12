#!/usr/bin/env python3
import argparse
import ast
import glob
import json
import os
import shutil
import sys
import time

import pandas as pd

VERSION = "1.0.4"
PRESETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets.json")
EXCEL_ROW_LIMIT = 1_048_576

# ---------------------------------------------------------------------------


def fatal(*lines):
    """Print one or more error lines and exit."""
    for line in lines:
        print(line)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------


def _term_width():
    return shutil.get_terminal_size((80, 24)).columns


def draw_progress(current, total, filename):
    """Redraw the progress bar line in place using carriage return."""
    width = _term_width()
    counter = f"  {current}/{total} "
    name_w = min(len(filename), 30)
    name = ("..." + filename[-(name_w - 3) :]) if len(filename) > name_w else filename
    bar_w = max(5, width - len(counter) - name_w - 4)  # 4 = "[", "]", " ", space
    filled = round(bar_w * current / total)
    bar = "█" * filled + "░" * (bar_w - filled)
    line = f"{counter}[{bar}] {name}"
    sys.stdout.write(f"\r\033[K{line[:width]}")
    sys.stdout.flush()


def end_progress():
    """Move past the progress bar line."""
    sys.stdout.write("\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------


def get_csv_files(input_path):
    if os.path.isfile(input_path):
        return [input_path]
    elif os.path.isdir(input_path):
        files = glob.glob(os.path.join(input_path, "*.csv"))
        if not files:
            fatal(f"No CSV files found in folder: {input_path}")
        return sorted(files)
    else:
        fatal(f"Input path not found: {input_path}")


def parse_list_arg(value, flag_name, example):
    try:
        result = ast.literal_eval(value)
        if not isinstance(result, list):
            raise ValueError("Must be a list")
        return result
    except (ValueError, SyntaxError):
        fatal(
            f"ERROR: {flag_name} must be a valid Python list.",
            f"       Example: {example}",
            f"       Got: {value}",
        )


def save_preset(name, filter_col, filter_vals, columns, preset_file):
    """Save or overwrite a named preset in the JSON presets file."""
    presets = {}
    if os.path.isfile(preset_file):
        with open(preset_file, "r", encoding="utf-8") as f:
            presets = json.load(f)
    overwriting = name in presets
    presets[name] = {
        "filter_column": filter_col,
        "filter": filter_vals,
        "columns": columns,
    }
    with open(preset_file, "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=2)
    action = "Overwritten" if overwriting else "Saved"
    print(f"  {action} preset '{name}' → {preset_file}")
    print(f"  Filter column: {filter_col}")
    print(f"  Filter values: {', '.join(filter_vals)}")
    print(f"  Columns:       {', '.join(columns)}")


def load_preset(name, preset_file):
    """Load a named preset from a JSON presets file. Returns (filter_col, filter_vals, columns)."""
    if not os.path.isfile(preset_file):
        fatal(f"ERROR: Presets file not found: {preset_file}")
    with open(preset_file, "r", encoding="utf-8") as f:
        presets = json.load(f)
    if name not in presets:
        available = ", ".join(k for k in presets if k != "_default") or "(none)"
        fatal(
            f"ERROR: Preset '{name}' not found in {os.path.basename(preset_file)}",
            f"       Available presets: {available}",
        )
    p = presets[name]
    for key in ("filter_column", "filter", "columns"):
        if key not in p:
            fatal(f"ERROR: Preset '{name}' is missing required key: '{key}'")
    if not isinstance(p["filter"], list):
        fatal(f"ERROR: Preset '{name}': 'filter' must be a list.")
    if not isinstance(p["columns"], list):
        fatal(f"ERROR: Preset '{name}': 'columns' must be a list.")
    return p["filter_column"], p["filter"], p["columns"]


def load_default_preset(preset_file):
    """Load the preset named by the '_default' key in the presets file."""
    if not os.path.isfile(preset_file):
        fatal(f"ERROR: Presets file not found: {preset_file}")
    with open(preset_file, "r", encoding="utf-8") as f:
        presets = json.load(f)
    default_name = presets.get("_default")
    if not default_name:
        fatal(
            f"ERROR: No '_default' key found in {os.path.basename(preset_file)}.",
            f'       Add \'"_default": "PresetName"\' or use --preset to specify one.',
        )
    return default_name, *load_preset(default_name, preset_file)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Filter and trim CSV files by column values, keeping only the columns you need.\n"
            "Process a single file or an entire folder of CSVs in one pass.\n"
            "Supports named presets for reusable filter configurations.\n"
            "Optionally exports to Excel. Warns if row count exceeds Excel worksheet limits."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Single file, default filters:\n"
            "  csvtrim --input data.csv --output trimmed.csv\n\n"
            "  # Folder of CSVs, convert to Excel too:\n"
            "  csvtrim --input ./csv_folder --output trimmed.csv --excel\n\n"
            "  # Custom filter values as a Python list:\n"
            "  csvtrim --input data.csv --output out.csv \\\n"
            "    --filter \"['SaaS', 'Developer Tools', 'Containers', 'Databases']\"\n\n"
            "  # Custom filter column and values:\n"
            "  csvtrim --input data.csv --output out.csv \\\n"
            "    --filter-column meterCategory --filter \"['Virtual Machines', 'Storage']\"\n\n"
            "  # Custom columns to keep:\n"
            "  csvtrim --input data.csv --output out.csv \\\n"
            "    --columns \"['meterCategory', 'quantity']\"\n\n"
            "  # Use a named preset from the default presets.json:\n"
            "  csvtrim --input data.csv --output out.csv --preset Azure\n\n"
            "  # Use a named preset from a custom presets file:\n"
            "  csvtrim --input data.csv --output out.csv \\\n"
            "    --preset Azure --preset-file /path/to/my_presets.json\n\n"
            "  # Save current flags as a new preset (no trimming performed):\n"
            "  csvtrim --preset-save MyPreset \\\n"
            "    --filter \"['Compute']\" --filter-column serviceFamily\n\n"
            "  # Copy an existing preset under a new name:\n"
            "  csvtrim --preset Azure --preset-save AzureCopy\n"
        ),
    )
    parser.add_argument(
        "--version", "-v", action="version", version=f"csvTrim {VERSION}"
    )
    parser.add_argument(
        "--input",
        "-i",
        default=None,
        metavar="PATH",
        help="Single CSV file or folder containing CSV files (required unless --preset-save)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        metavar="FILE",
        help="Output CSV file path (e.g. trimmed.csv) (required unless --preset-save)",
    )
    parser.add_argument(
        "--excel",
        "-e",
        action="store_true",
        help="Also convert the output CSV to Excel (.xlsx)",
    )
    parser.add_argument(
        "--filter",
        "-f",
        default=None,
        metavar="LIST",
        help=(
            "Values to keep as a Python list, matched against --filter-column. "
            "Omit to use the default preset. "
            "Example: \"['SaaS', 'Developer Tools', 'Containers', 'Databases']\""
        ),
    )
    parser.add_argument(
        "--filter-column",
        "-fc",
        default=None,
        metavar="COL",
        help=("Column to filter values against. Omit to use the default preset."),
    )
    parser.add_argument(
        "--columns",
        "-c",
        default=None,
        metavar="LIST",
        help=(
            "Columns to keep as a Python list. "
            "Omit to use the default preset. "
            "Example: \"['meterCategory', 'quantity']\""
        ),
    )
    parser.add_argument(
        "--preset",
        "-p",
        default=None,
        metavar="NAME",
        help=(
            f"Load filter-column, filter values, and columns from a named preset "
            f"in the presets file (default: {os.path.basename(PRESETS_FILE)}). "
            f"When --preset is used, --filter, --filter-column, and --columns are ignored. "
            f"If no --preset and no individual flags are given, the '_default' preset "
            f"is loaded automatically. Example: --preset Azure"
        ),
    )
    parser.add_argument(
        "--preset-file",
        "-pf",
        default=None,
        metavar="FILE",
        help=(
            f"Path to the JSON presets file. "
            f"Default: {PRESETS_FILE}. "
            f"Example: --preset-file /path/to/my_presets.json"
        ),
    )
    parser.add_argument(
        "--preset-save",
        "-ps",
        default=None,
        metavar="NAME",
        help=(
            f"Save current --filter, --filter-column, and --columns as a named preset "
            f"in the presets file (or overwrite if it already exists). "
            f"No CSV trimming is performed. "
            f"Example: --preset-save MyPreset"
        ),
    )
    args = parser.parse_args()

    # Resolve filter config: explicit preset, auto-default, or individual flags
    preset_file = args.preset_file if args.preset_file else PRESETS_FILE
    if args.preset is not None:
        # Explicit --preset: all-or-nothing, individual flags are ignored
        resolved_preset = args.preset
        filter_col, filter_vals, columns = load_preset(args.preset, preset_file)
    elif args.filter is None and args.filter_column is None and args.columns is None:
        # No flags at all: auto-load the _default preset
        resolved_preset, filter_col, filter_vals, columns = load_default_preset(
            preset_file
        )
    else:
        # One or more individual flags given: load _default as base, apply overrides
        resolved_preset = None
        _, base_col, base_vals, base_cols = load_default_preset(preset_file)
        filter_vals = (
            parse_list_arg(
                args.filter,
                "--filter",
                "\"['SaaS', 'Developer Tools', 'Containers', 'Databases']\"",
            )
            if args.filter is not None
            else base_vals
        )
        filter_col = args.filter_column if args.filter_column is not None else base_col
        columns = (
            parse_list_arg(
                args.columns, "--columns", "\"['meterCategory', 'quantity']\""
            )
            if args.columns is not None
            else base_cols
        )

    # Strip whitespace from all config values to avoid silent mismatches
    filter_vals = [v.strip() for v in filter_vals]
    filter_col = filter_col.strip()
    columns = [c.strip() for c in columns]

    # Save preset and exit — no CSV trimming performed
    if args.preset_save is not None:
        save_preset(args.preset_save, filter_col, filter_vals, columns, preset_file)
        exit(0)

    # --input and --output are required for trimming
    if not args.input:
        parser.error("--input is required")
    if not args.output:
        parser.error("--output is required")

    csv_files = get_csv_files(args.input)
    total_files = len(csv_files)
    W = _term_width()

    # Clear screen then print header
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
    input_label = os.path.basename(os.path.abspath(args.input))
    preset_label = f"  ·  preset: {resolved_preset}" if resolved_preset else ""
    print()
    print(
        f"  csvTrim {VERSION}  ·  {input_label}  ·  {total_files} file{'s' if total_files > 1 else ''}{preset_label}"
    )
    print(f"  {'─' * (W - 4)}")
    print(f"  Output:        {args.output}")
    print(f"  Filter column: {filter_col}")
    print(f"  Filter values: {', '.join(filter_vals)}")

    # Print column names wrapped to terminal width
    prefix = "  Columns:      "
    line = prefix
    for j, col in enumerate(columns):
        part = col + (", " if j < len(columns) - 1 else "")
        if len(line) + len(part) > W and line != prefix:
            print(line.rstrip(", "))
            line = " " * len(prefix) + part
        else:
            line += part
    print(line)
    print()

    # Filter and write output CSV — accumulate all counts in a single pass
    total_input_rows = 0
    total_output_rows = 0
    filter_counts = {v: 0 for v in filter_vals}
    first_chunk_written = False
    skipped = []
    input_col_count = 0  # total columns in source files (from first valid file)
    output_col_count = 0  # columns kept in output (from first valid file)
    start_time = time.time()

    for i, csv_file in enumerate(csv_files, 1):
        filename = os.path.basename(csv_file)
        file_input_rows = 0
        draw_progress(i, total_files, filename)
        try:
            # Peek at the file header to determine which requested columns are available.
            # This is a near-zero cost read (header line only) and lets us safely use
            # usecols, so pandas skips loading unwanted columns entirely.
            file_header = (
                pd.read_csv(csv_file, nrows=0, encoding="utf-8", on_bad_lines="skip")
                .columns.str.strip()
                .tolist()
            )

            if filter_col not in file_header:
                skipped.append((filename, f"'{filter_col}' column not found"))
                continue

            header_set = set(file_header)
            cols_to_write = [c for c in columns if c in header_set]
            missing_cols = [c for c in columns if c not in header_set]
            if missing_cols:
                skipped.append(
                    (filename, f"columns not found, skipped: {missing_cols}")
                )

            # Prepend filter_col to load list if it isn't already in the columns to write
            cols_to_load = (
                [filter_col] + cols_to_write
                if filter_col not in cols_to_write
                else cols_to_write
            )

            # Capture column counts from the first valid file
            if input_col_count == 0:
                input_col_count = len(file_header)
                output_col_count = len(cols_to_write)

            for chunk in pd.read_csv(
                csv_file,
                chunksize=100_000,
                encoding="utf-8",
                on_bad_lines="skip",
                low_memory=False,
                usecols=cols_to_load,
            ):
                file_input_rows += len(chunk)
                chunk[filter_col] = chunk[
                    filter_col
                ].str.strip()  # strip whitespace from CSV values to match filter values reliably
                filtered = chunk[chunk[filter_col].isin(filter_vals)]
                if len(filtered) > 0:
                    for val, cnt in filtered[filter_col].value_counts().items():
                        filter_counts[val] += cnt
                    filtered[cols_to_write].to_csv(
                        args.output,
                        mode="w" if not first_chunk_written else "a",
                        index=False,
                        header=not first_chunk_written,
                    )
                    first_chunk_written = True
                    total_output_rows += len(filtered)
        except Exception as ex:
            skipped.append((filename, str(ex)))

        total_input_rows += file_input_rows

    end_progress()

    if not first_chunk_written:
        print("\n  ⚠  No matching rows found. Output file was not created.")
        exit(0)

    elapsed = time.time() - start_time
    reduction = (
        (1 - total_output_rows / total_input_rows) * 100 if total_input_rows else 0
    )
    count_w = len(f"{total_input_rows:,}")
    val_w = max(len(v) for v in filter_vals)

    # -----------------------------------------------------------------------
    # Compact summary — fits in 80x24
    # -----------------------------------------------------------------------
    dw = W - 2

    def sep(char="═"):
        print(f"  {char * dw}")

    def rule():
        print(f"  {'─' * dw}")

    print()
    sep()
    print(
        f"  {'Files:':10}{total_files:<6}  "
        f"{'Rows in:':10}{total_input_rows:>{count_w},}  "
        f"Elapsed: {elapsed:.1f}s"
    )
    rule()
    cols_removed = input_col_count - output_col_count
    cols_removed_pct = cols_removed / input_col_count * 100 if input_col_count else 0
    print(f"  {'Columns kept:':<16}{output_col_count:>{count_w},}")
    print(
        f"  {'Columns removed:':<16}{cols_removed:>{count_w},}   ({cols_removed_pct:.1f}%)"
    )
    print(f"  {'Rows out:':<16}{total_output_rows:>{count_w},}")
    print(
        f"  {'Rows removed:':<16}{total_input_rows - total_output_rows:>{count_w},}   ({reduction:.1f}% reduction)"
    )
    rule()
    print(f"  Rows by {filter_col}:")
    for val in filter_vals:
        cnt = filter_counts[val]
        warn = "  ⚠  no rows!" if cnt == 0 else ""
        print(f"    {val:<{val_w}}  {cnt:>{count_w},}{warn}")
    sep()
    print()

    # Skipped file warnings
    for fname, reason in skipped:
        print(f"  ⚠  {fname}: {reason}")
    if skipped:
        print()

    # Excel limit warning
    if total_output_rows > EXCEL_ROW_LIMIT:
        print(
            f"  ⚠  {total_output_rows:,} rows exceeds Excel's worksheet limit of {EXCEL_ROW_LIMIT:,}."
        )
        if args.excel:
            print(f"  ⚠  Excel conversion will split data across multiple sheets.")
        print()

    # Optional Excel conversion
    if args.excel:
        excel_path = os.path.splitext(args.output)[0] + ".xlsx"
        print(f"  Writing Excel: {excel_path}")
        chunks = pd.read_csv(args.output, chunksize=1_000_000, low_memory=False)
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            for i, chunk in enumerate(chunks):
                sheet_name = f"Sheet{i + 1}"
                chunk.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"    {sheet_name} written ({len(chunk):,} rows)")
        print(f"  Done  →  {excel_path}")
        print()


if __name__ == "__main__":
    main()
