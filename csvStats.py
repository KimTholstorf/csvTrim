#!/usr/bin/env python3
import argparse
import collections
import datetime
import glob
import json
import os
import shutil
import sys
import time
import pandas as pd

VERSION      = '1.0.0'
PRESETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'csvStats_presets.json')

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
    width    = _term_width()
    counter  = f"  {current}/{total} "
    name_w   = min(len(filename), 30)
    name     = ("..." + filename[-(name_w - 3):]) if len(filename) > name_w else filename
    bar_w    = max(5, width - len(counter) - name_w - 4)  # 4 = "[", "]", " ", space
    filled   = round(bar_w * current / total)
    bar      = "█" * filled + "░" * (bar_w - filled)
    line     = f"{counter}[{bar}] {name}"
    sys.stdout.write(f"\r\033[K{line[:width]}")
    sys.stdout.flush()

def end_progress():
    """Move past the progress bar line."""
    sys.stdout.write("\n")
    sys.stdout.flush()

# ---------------------------------------------------------------------------
# Preset loading
# ---------------------------------------------------------------------------

def load_preset(name, preset_file):
    """Load a named preset from the JSON presets file. Returns the groups list."""
    if not os.path.isfile(preset_file):
        fatal(f"ERROR: Presets file not found: {preset_file}")
    with open(preset_file, 'r', encoding='utf-8') as f:
        presets = json.load(f)
    if name not in presets:
        available = ', '.join(k for k in presets if k != '_default') or '(none)'
        fatal(
            f"ERROR: Preset '{name}' not found in {os.path.basename(preset_file)}",
            f"       Available presets: {available}",
        )
    p = presets[name]
    if 'groups' not in p:
        fatal(f"ERROR: Preset '{name}' is missing required key: 'groups'")
    if not isinstance(p['groups'], list) or len(p['groups']) == 0:
        fatal(f"ERROR: Preset '{name}': 'groups' must be a non-empty list.")
    VALID_OPS = {'equals', 'in', 'contains', 'not_contains'}
    for grp in p['groups']:
        if 'name' not in grp:
            fatal(f"ERROR: Preset '{name}': a group is missing the 'name' key.")
        if 'conditions' not in grp or not isinstance(grp['conditions'], list):
            fatal(
                f"ERROR: Preset '{name}', group '{grp.get('name', '?')}': "
                f"'conditions' must be a list."
            )
        for cond in grp['conditions']:
            if 'column' not in cond:
                fatal(
                    f"ERROR: Preset '{name}', group '{grp['name']}': "
                    f"a condition is missing the 'column' key."
                )
            ops = [k for k in cond if k != 'column']
            if len(ops) != 1 or ops[0] not in VALID_OPS:
                fatal(
                    f"ERROR: Preset '{name}', group '{grp['name']}': "
                    f"each condition must have exactly one operator from {sorted(VALID_OPS)}.",
                    f"       Got keys: {list(cond.keys())}",
                )
    return p['groups']

def load_default_preset(preset_file):
    """Load the preset named by the '_default' key in the presets file."""
    if not os.path.isfile(preset_file):
        fatal(f"ERROR: Presets file not found: {preset_file}")
    with open(preset_file, 'r', encoding='utf-8') as f:
        presets = json.load(f)
    default_name = presets.get('_default')
    if not default_name:
        fatal(
            f"ERROR: No '_default' key found in {os.path.basename(preset_file)}.",
            f"       Add '\"_default\": \"PresetName\"' or use --preset to specify one.",
        )
    return default_name, load_preset(default_name, preset_file)

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def derive_classify_cols(groups):
    """Return the deduplicated, ordered list of column names needed for classification."""
    seen = {}
    for grp in groups:
        for cond in grp['conditions']:
            col = cond['column']
            if col not in seen:
                seen[col] = None
    return list(seen.keys())

def build_cat_to_group_from_preset(groups):
    """
    Build a meterCategory → group name mapping from the preset's meterCategory conditions.
    Used for display labeling in the console summary and Excel report — mirrors the old
    category_rule_label() behaviour where the group label is assigned at the category level,
    not the per-row level.
    """
    mapping = {}
    for grp in groups:
        for cond in grp['conditions']:
            if cond['column'] == 'meterCategory':
                if 'equals' in cond:
                    mapping[cond['equals']] = grp['name']
                elif 'in' in cond:
                    for val in cond['in']:
                        mapping[val] = grp['name']
    return mapping

def classify_chunk(chunk, groups):
    """
    Classify each row against the loaded groups.

    Returns (include_mask, group_series):
      include_mask : boolean Series — True if any group matched
      group_series : str Series — group name for matched rows, 'NOT USED' otherwise

    Conditions within a group are AND'd; groups are OR'd (first-match wins).
    """
    include_mask = pd.Series(False, index=chunk.index)
    group_series = pd.Series('NOT USED', index=chunk.index, dtype=object)

    for grp in groups:
        grp_mask = pd.Series(True, index=chunk.index)
        for cond in grp['conditions']:
            col = cond['column']
            if   'equals'       in cond: grp_mask &= (chunk[col] == cond['equals'])
            elif 'in'           in cond: grp_mask &= chunk[col].isin(cond['in'])
            elif 'contains'     in cond: grp_mask &= chunk[col].str.contains(cond['contains'], na=False)
            elif 'not_contains' in cond: grp_mask &= ~chunk[col].str.contains(cond['not_contains'], na=False)

        unassigned = grp_mask & ~include_mask   # first-match wins
        group_series[unassigned] = grp['name']
        include_mask |= grp_mask

    return include_mask, group_series

# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def build_breakdown_df(counts, cat_to_group, group_order):
    """Build the flat meterCategory/meterSubCategory breakdown as a DataFrame."""
    def sort_key_local(cat):
        grp = cat_to_group.get(cat, 'NOT USED')
        try:    return (group_order.index(grp), cat)
        except ValueError: return (len(group_order), cat)

    rows = []
    for cat in sorted(counts, key=sort_key_local):
        for sub in sorted(counts[cat]):
            incl  = counts[cat][sub]['included']
            excl  = counts[cat][sub]['excluded']
            total = incl + excl
            pct   = incl / total * 100 if total else 0
            rule  = cat_to_group.get(cat, 'NOT USED')
            rows.append([rule, cat, sub, total, incl, excl, f'{pct:.0f}%'])

    return pd.DataFrame(rows, columns=[
        'Group', 'meterCategory', 'meterSubCategory',
        'Total Rows', 'Included', 'Excluded', '% Included'
    ])

def write_excel(excel_path, counts, total_rows, total_included, input_path, csv_files,
                groups, cat_to_group, group_order):
    """Write 2-sheet Excel report: Summary and Breakdown."""

    total_excluded = total_rows - total_included
    incl_pct = total_included / total_rows * 100 if total_rows else 0

    info_df = pd.DataFrame([
        ['Generated',           datetime.datetime.now().strftime('%Y-%m-%d %H:%M')],
        ['Input path',          input_path],
        ['CSV files processed', len(csv_files)],
        ['Total rows',          total_rows],
        ['Included rows',       total_included],
        ['Excluded rows',       total_excluded],
        ['% Included',          f'{incl_pct:.1f}%'],
        ['% Excluded',          f'{100 - incl_pct:.1f}%'],
    ], columns=['Metric', 'Value'])

    # Build human-readable rules table dynamically from preset groups
    rules_rows = []
    for grp in groups:
        parts = []
        for cond in grp['conditions']:
            col = cond['column']
            if   'equals'       in cond: parts.append(f'{col} = "{cond["equals"]}"')
            elif 'in'           in cond: parts.append(f'{col} in [{", ".join(cond["in"])}]')
            elif 'contains'     in cond: parts.append(f'{col} contains "{cond["contains"]}"')
            elif 'not_contains' in cond: parts.append(f'{col} does NOT contain "{cond["not_contains"]}"')
        rules_rows.append([grp['name'], '  AND  '.join(parts)])
    rules_df = pd.DataFrame(rules_rows, columns=['Group', 'Filter Logic'])

    def sort_key_local(cat):
        grp = cat_to_group.get(cat, 'NOT USED')
        try:    return (group_order.index(grp), cat)
        except ValueError: return (len(group_order), cat)

    cat_rows = []
    for cat in sorted(counts, key=sort_key_local):
        sub_counts = counts[cat]
        cat_incl   = sum(v['included'] for v in sub_counts.values())
        cat_excl   = sum(v['excluded'] for v in sub_counts.values())
        cat_total  = cat_incl + cat_excl
        rule       = cat_to_group.get(cat, 'NOT USED')
        pct        = cat_incl / cat_total * 100 if cat_total else 0
        cat_rows.append([rule, cat, cat_total, cat_incl, cat_excl, f'{pct:.1f}%'])

    cat_summary_df = pd.DataFrame(cat_rows, columns=[
        'Group', 'meterCategory', 'Total Rows', 'Included', 'Excluded', '% Included'
    ])

    breakdown_df = build_breakdown_df(counts, cat_to_group, group_order)

    print(f"  Writing Excel: {excel_path}")
    with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
        row = 0
        for title, df in [
            ('Report Info',        info_df),
            ('Filter Rules',       rules_df),
            ('Category Breakdown', cat_summary_df),
        ]:
            pd.DataFrame([[title]]).to_excel(
                writer, sheet_name='Summary',
                startrow=row, index=False, header=False)
            row += 1
            df.to_excel(writer, sheet_name='Summary',
                        startrow=row, index=False, header=True)
            row += len(df) + 1 + 1  # +1 header, +1 blank spacer

        breakdown_df.to_excel(writer, sheet_name='Breakdown', index=False)

    print(f"  Done  →  {excel_path}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def get_csv_files(input_path):
    if os.path.isfile(input_path):
        return [input_path]
    elif os.path.isdir(input_path):
        files = glob.glob(os.path.join(input_path, '*.csv'))
        if not files:
            fatal(f"No CSV files found in folder: {input_path}")
        return sorted(files)
    else:
        fatal(f"Input path not found: {input_path}")

def main():
    parser = argparse.ArgumentParser(
        description=(
            'Analyse CSV files and show a row-count breakdown by filter group.\n'
            'Optionally exports a 2-sheet Excel report (Summary, Breakdown).\n'
            'Classification rules are loaded from a JSON presets file.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  # Auto-load the default preset:\n'
            '  python3 csvStats.py --input data.csv\n\n'
            '  # Use a named preset:\n'
            '  python3 csvStats.py --input data.csv --preset Azure\n\n'
            '  # Folder of CSVs + Excel report:\n'
            '  python3 csvStats.py --input ./csv_folder --excel --output report.xlsx\n\n'
            '  # Use a custom presets file:\n'
            '  python3 csvStats.py --input data.csv --preset-file /path/to/csvStats_presets.json\n'
        )
    )
    parser.add_argument('--input',       '-i',  required=True,
                        metavar='PATH',
                        help='Single CSV file or folder containing CSV files')
    parser.add_argument('--excel',       '-e',  action='store_true',
                        help='Export a 2-sheet Excel report (Summary, Breakdown)')
    parser.add_argument('--output',      '-o',  default='csvStats_report.xlsx',
                        metavar='FILE',
                        help='Excel output path (default: csvStats_report.xlsx)')
    parser.add_argument('--preset',      '-p',  default=None,
                        metavar='NAME',
                        help=(
                            "Load classification groups from a named preset in the presets file. "
                            "If omitted, the '_default' preset is loaded automatically. "
                            "Example: --preset Azure"
                        ))
    parser.add_argument('--preset-file', '-pf', default=None,
                        metavar='FILE',
                        help=(
                            f"Path to the JSON presets file. "
                            f"Default: {PRESETS_FILE}. "
                            f"Example: --preset-file /path/to/csvStats_presets.json"
                        ))
    parser.add_argument('--version',     '-v',  action='version', version=f'csvStats {VERSION}')
    args = parser.parse_args()

    # Load preset — explicit name or auto-load _default
    preset_file = args.preset_file if args.preset_file else PRESETS_FILE
    if args.preset is not None:
        resolved_preset = args.preset
        groups = load_preset(args.preset, preset_file)
    else:
        resolved_preset, groups = load_default_preset(preset_file)

    # Derive classification columns and category→group display map from preset
    classify_cols = derive_classify_cols(groups)
    cat_to_group  = build_cat_to_group_from_preset(groups)

    # Build group ordering and totals from preset groups
    group_names  = [g['name'] for g in groups]
    group_order  = group_names + ['NOT USED']
    group_totals = collections.OrderedDict(
        [(name, {'included': 0, 'excluded': 0}) for name in group_order]
    )

    csv_files   = get_csv_files(args.input)
    total_files = len(csv_files)
    W           = _term_width()

    # Clear screen then print header
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
    print()
    input_label  = os.path.basename(os.path.abspath(args.input))
    preset_label = f"  ·  preset: {resolved_preset}"
    print(f"  csvStats {VERSION}  ·  {input_label}  ·  {total_files} file{'s' if total_files > 1 else ''}{preset_label}")
    print(f"  {'─' * (W - 4)}")
    print()

    # counts[meterCategory][meterSubCategory] = {'included': n, 'excluded': n}
    counts = collections.defaultdict(
        lambda: collections.defaultdict(lambda: {'included': 0, 'excluded': 0})
    )
    total_rows     = 0
    total_included = 0
    skipped        = []

    start_time = time.time()

    for i, csv_file in enumerate(csv_files, 1):
        filename  = os.path.basename(csv_file)
        file_rows = 0
        draw_progress(i, total_files, filename)
        try:
            # Peek at header to verify required columns exist
            header = pd.read_csv(csv_file, nrows=0, encoding='utf-8',
                                 on_bad_lines='skip').columns.str.strip().tolist()
            missing = [c for c in classify_cols if c not in header]
            if missing:
                skipped.append((filename, f"missing columns: {missing}"))
                continue

            for chunk in pd.read_csv(csv_file, chunksize=100_000,
                          encoding='utf-8', on_bad_lines='skip',
                          low_memory=False, usecols=classify_cols):

                # Strip whitespace; fill empty cells with a readable placeholder
                for col in classify_cols:
                    chunk[col] = chunk[col].str.strip().fillna('(blank)')

                # Classify each row — get both include mask and group assignment
                mask, group_series = classify_chunk(chunk, groups)
                total_included += int(mask.sum())

                chunk['_included'] = mask
                chunk['_group']    = group_series

                # Accumulate counts per category / subcategory
                for (cat, sub, inc), cnt in (
                    chunk.groupby(['meterCategory', 'meterSubCategory', '_included']).size().items()
                ):
                    counts[cat][sub]['included' if inc else 'excluded'] += cnt

                # Accumulate per-group totals directly from group_series
                for (grp_name, inc), cnt in (
                    chunk.groupby(['_group', '_included']).size().items()
                ):
                    if grp_name in group_totals:
                        group_totals[grp_name]['included' if inc else 'excluded'] += cnt

                file_rows += len(chunk)

        except Exception as ex:
            skipped.append((filename, str(ex)))

        total_rows += file_rows

    end_progress()

    if not counts:
        print("  WARNING: No data found.")
        exit(0)

    elapsed        = time.time() - start_time
    total_excluded = total_rows - total_included
    incl_pct       = total_included / total_rows * 100 if total_rows else 0
    excl_pct       = 100 - incl_pct
    elapsed_str    = f"{elapsed:.1f}s"

    # -----------------------------------------------------------------------
    # Compact summary — fits in 80x24
    # -----------------------------------------------------------------------
    W  = _term_width()
    dw = W - 2

    def sep(char='═'): print(f"  {char * dw}")
    def rule():        print(f"  {'─' * dw}")
    def blank():       print()

    count_w = len(f"{total_rows:,}")

    blank()
    sep()
    print(f"  {'Files:':10}{total_files:<6}  "
          f"{'Rows:':8}{total_rows:>{count_w},}  "
          f"Elapsed: {elapsed_str}")
    rule()
    print(f"  {'Included':<12}{total_included:>{count_w},}   {incl_pct:5.1f}%")
    print(f"  {'Excluded':<12}{total_excluded:>{count_w},}   {excl_pct:5.1f}%")
    rule()

    # Group breakdown table
    grp_w = max(len(g) for g in group_order) + 2
    print(f"  {'Group':<{grp_w}}  {'Total':>{count_w}}    {'Included':>{count_w}}    {'%':>6}")
    rule()
    for grp, v in group_totals.items():
        g_total = v['included'] + v['excluded']
        g_pct   = v['included'] / g_total * 100 if g_total else 0
        print(f"  {grp:<{grp_w}}  {g_total:>{count_w},}    {v['included']:>{count_w},}    {g_pct:5.1f}%")

    sep()
    blank()

    # Skipped file warnings
    for fname, reason in skipped:
        print(f"  Skipped {fname}: {reason}")
    if skipped:
        blank()

    # -----------------------------------------------------------------------
    # Excel export
    # -----------------------------------------------------------------------
    if args.excel:
        write_excel(args.output, counts, total_rows, total_included,
                    args.input, csv_files, groups, cat_to_group, group_order)
        blank()

if __name__ == '__main__':
    main()
