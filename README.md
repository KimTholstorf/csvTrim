# csvTrim

Filter and trim large CSV files by column values — keep only the rows and columns you need.

csvTrim processes a single file or an entire folder of CSVs in one pass. It is optimised for large billing exports (e.g. Azure cost data) but works with any structured CSV. Results can also be exported to Excel.

---

## Features

- **Row filtering** — keep only rows whose filter column matches a list of values
- **Column trimming** — drop every column not in your keep list
- **Folder processing** — pass a folder path to process all `.csv` files at once
- **Preset system** — save named filter configurations to `presets.json` and load them by name
- **Auto-default preset** — run with just `--input` / `--output` to use the preset marked as default
- **Excel export** — optional `.xlsx` output; splits automatically across sheets if rows exceed Excel's worksheet limit
- **Memory-efficient** — reads files in 100 000-row chunks so large exports don't run out of RAM
- **Run summary** — shows row counts, reduction percentage, per-value breakdown, and elapsed time

---

## Requirements

- Python 3.9+
- `pandas`
- `openpyxl` (only needed for `--excel`)

### Install

```bash
# One-time setup (creates .venv with pandas + openpyxl)
bash setup_python_env.sh

# Activate the environment
source .venv/bin/activate
```

The setup script installs [uv](https://github.com/astral-sh/uv) if it isn't already present (via [Homebrew](https://formulae.brew.sh/formula/uv) if available, otherwise via curl).

---

## Install via pip

```bash
pip install csvtrim

# or, for an isolated install that won't affect your system Python:
pipx install csvtrim
```

After installation, `csvtrim` is available as a shell command — no venv activation needed:

```bash
csvtrim --input data.csv --output trimmed.csv
```

The default `presets.json` is bundled with the package. To use a custom presets file, pass `--preset-file /path/to/your_presets.json`.

---

## Docker

### Build

```bash
docker build -t csvtrim .
```

### Run

Pull the image from GitHub Container Registry, then mount a local folder to `/data` with `-v` to pass files in and retrieve output. All arguments work identically to the local script.

```bash
docker pull ghcr.io/kimtholstorf/csvtrim:latest

docker run --rm -it \
  -v /your/data:/data \
  ghcr.io/kimtholstorf/csvtrim:latest \
  --input /data/export.csv --output /data/trimmed.csv
```

The `-it` flag gives csvTrim a real terminal so the progress bar and ANSI output render correctly. `--rm` removes the container automatically when it exits.

---

## Quick start

```bash
# Use the default preset, trim a single file
python3 csvTrim.py --input data.csv --output trimmed.csv

# Process an entire folder, also produce Excel output
python3 csvTrim.py --input ./exports --output trimmed.csv --excel

# Use a named preset
python3 csvTrim.py --input data.csv --output trimmed.csv --preset Azure
```

---

## CLI reference

| Argument | Short | Description |
|---|---|---|
| `--input PATH` | `-i` | Single `.csv` file or folder of `.csv` files to process. Required unless `--preset-save` is used. |
| `--output FILE` | `-o` | Output CSV file path (e.g. `trimmed.csv`). Required unless `--preset-save` is used. |
| `--excel` | `-e` | Also write an `.xlsx` file alongside the output CSV. Splits into multiple sheets if the row count exceeds Excel's worksheet limit. |
| `--filter LIST` | `-f` | Python list of values to keep, matched against `--filter-column`. Omit to use the default preset. Example: `"['Compute', 'Storage']"` |
| `--filter-column COL` | `-fc` | Column name to match filter values against. Omit to use the default preset. |
| `--columns LIST` | `-c` | Python list of column names to keep in the output. Omit to use the default preset. Example: `"['meterCategory', 'quantity']"` |
| `--preset NAME` | `-p` | Load all filter settings from a named preset. Overrides `--filter`, `--filter-column`, and `--columns`. If no `--preset` and no individual flags are given, the `_default` preset is loaded automatically. |
| `--preset-file FILE` | `-pf` | Path to a custom JSON presets file. Defaults to `presets.json` next to the script. |
| `--preset-save NAME` | `-ps` | Save the current `--filter`, `--filter-column`, and `--columns` as a named preset (or overwrite an existing one). No CSV trimming is performed. |
| `--version` | `-v` | Print the version and exit. |

### Flag resolution order

When deciding which filter settings to use, csvTrim applies this priority:

1. **`--preset NAME`** — load everything from the named preset; individual flags are ignored.
2. **No flags at all** — auto-load the `_default` preset from `presets.json`.
3. **One or more individual flags** — load the `_default` preset as a base, then apply any explicitly passed flags on top.

---

## Preset system

Presets are stored in a JSON file (`presets.json` by default, next to the script). Each preset holds three values: the column to filter on, which values to keep, and which output columns to retain.
The `"_default"` key names which preset to load when no `--preset` or individual flags are given. To change the default, edit the string value — no other changes needed.

### File format

```json
{
  "_default": "Azure",
  "Azure": {
    "filter_column": "serviceFamily",
    "filter": ["Compute", "Networking", "Storage"],
    "columns": [
      "serviceFamily",
      "meterCategory",
      "meterSubCategory",
      "meterName",
      "ProductName",
      "productOrderName",
      "meterRegion",
      "quantity",
      "pricingModel",
      "term",
      "unitOfMeasure",
      "ResourceId",
      "date"
    ]
  }
}
```

### Using a preset

```bash
python3 csvTrim.py --input data.csv --output out.csv --preset Azure
```

### Saving a new preset

Use `--preset-save` together with the individual flags. No trimming is performed — the preset is written to `presets.json` and the script exits.

```bash
# Save a brand-new preset
python3 csvTrim.py --preset-save GCP \
  --filter-column "service.description" \
  --filter "['Compute Engine', 'Cloud Storage', 'BigQuery']" \
  --columns "['billing_account_id', 'service.description', 'cost', 'currency']"

# Copy an existing preset under a new name
python3 csvTrim.py --preset Azure --preset-save AzureBackup
```

If the preset name already exists it is overwritten. The script prints a confirmation showing what was saved.

### Using a custom presets file

```bash
python3 csvTrim.py --input data.csv --output out.csv \
  --preset MyPreset --preset-file /path/to/my_presets.json
```

`--preset-file` works with `--preset`, `--preset-save`, and the auto-default flow.

---

## Examples

```bash
# Default run — auto-loads the '_default' preset
python3 csvTrim.py --input data.csv --output trimmed.csv

# Named preset
python3 csvTrim.py --input data.csv --output trimmed.csv --preset Azure

# Folder of CSVs + Excel output
python3 csvTrim.py --input ./monthly_exports --output combined.csv --excel

# Override only the filter values; other settings come from the default preset
python3 csvTrim.py --input data.csv --output out.csv \
  --filter "['SaaS', 'Developer Tools', 'Containers', 'Databases']"

# Fully custom filter (no preset)
python3 csvTrim.py --input data.csv --output out.csv \
  --filter-column meterCategory \
  --filter "['Virtual Machines', 'Storage']" \
  --columns "['meterCategory', 'quantity', 'date']"

# Save a preset then use it
python3 csvTrim.py --preset-save Prod \
  --filter-column serviceFamily \
  --filter "['Compute', 'Networking']" \
  --columns "['serviceFamily', 'meterCategory', 'quantity', 'date']"

python3 csvTrim.py --input data.csv --output out.csv --preset Prod
```

---

## Docker examples

Same examples as above, run inside the container. Mount your data folder to `/data` and prefix paths accordingly. Use `--preset-file /data/presets.json` when saving or loading presets so changes persist to your local machine.

```bash
# Default run — auto-loads the '_default' preset
docker run --rm -it -v /your/data:/data ghcr.io/kimtholstorf/csvtrim:latest \
  --input /data/export.csv --output /data/trimmed.csv

# Named preset
docker run --rm -it -v /your/data:/data ghcr.io/kimtholstorf/csvtrim:latest \
  --input /data/export.csv --output /data/trimmed.csv --preset Azure

# Folder of CSVs + Excel output
docker run --rm -it -v /your/data:/data ghcr.io/kimtholstorf/csvtrim:latest \
  --input /data/monthly_exports --output /data/combined.csv --excel

# Override only the filter values; other settings come from the default preset
docker run --rm -it -v /your/data:/data ghcr.io/kimtholstorf/csvtrim:latest \
  --input /data/export.csv --output /data/out.csv \
  --filter "['SaaS', 'Developer Tools', 'Containers', 'Databases']"

# Fully custom filter (no preset)
docker run --rm -it -v /your/data:/data ghcr.io/kimtholstorf/csvtrim:latest \
  --input /data/export.csv --output /data/out.csv \
  --filter-column meterCategory \
  --filter "['Virtual Machines', 'Storage']" \
  --columns "['meterCategory', 'quantity', 'date']"

# Save a preset to the mounted folder, then use it
docker run --rm -it -v /your/data:/data ghcr.io/kimtholstorf/csvtrim:latest \
  --preset-save Prod \
  --filter-column serviceFamily \
  --filter "['Compute', 'Networking']" \
  --columns "['serviceFamily', 'meterCategory', 'quantity', 'date']" \
  --preset-file /data/presets.json

docker run --rm -it -v /your/data:/data ghcr.io/kimtholstorf/csvtrim:latest \
  --input /data/export.csv --output /data/out.csv \
  --preset Prod --preset-file /data/presets.json
```

---

## Output

After processing, csvTrim prints a summary:

```
  ══════════════════════════════════════════════════════════
  Files:    3       Rows in:     2,841,504   Elapsed: 8.3s
  ──────────────────────────────────────────────────────────
  Columns kept:        13
  Columns removed:     51     (79.7%)
  Rows out:           312,847
  Rows removed:     2,528,657  (89.0% reduction)
  ──────────────────────────────────────────────────────────
  Rows by serviceFamily:
    Compute      241,003
    Networking    48,201
    Storage       23,643
  ══════════════════════════════════════════════════════════
```

Skipped files (missing columns, encoding errors, etc.) are listed below the summary with the reason.
