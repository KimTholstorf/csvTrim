#!/usr/bin/env python3
import argparse
import os
import glob
import collections
import datetime
import shutil
import sys
import time
import pandas as pd

# ---------------------------------------------------------------------------
# Filter rules that mirror actual calculation logic
# ---------------------------------------------------------------------------
# Compute:    meterCategory == "Virtual Machines"
# Storage:    meterCategory == "Storage"
#               AND meterSubCategory contains "Managed Disks"
#               AND meterName does NOT contain "Disk Operations"
# Networking: meterCategory is one of the values below
NETWORKING_CATEGORIES = {
    'Virtual Network', 'Bandwidth', 'Load Balancer', 'Azure DNS',
    'NAT Gateway', 'VPN Gateway', 'Application Gateway', 'Traffic Manager'
}

FILTER_RULES = [
    ('Compute',
     'meterCategory = "Virtual Machines"'),
    ('Storage',
     'meterCategory = "Storage"  AND  meterSubCategory contains "Managed Disks"'
     '  AND  meterName does NOT contain "Disk Operations"'),
    ('Networking',
     'meterCategory in [' + ', '.join(sorted(NETWORKING_CATEGORIES)) + ']'),
]

# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

def _term_width():
    return shutil.get_terminal_size((80, 24)).columns

def draw_progress(current, total, filename):
    """Redraw the progress bar line in place using carriage return."""
    width    = _term_width()
    counter  = f"  {current}/{total} "
    # Reserve up to 30 chars for the filename, truncate if longer
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
# Sorting / classification helpers
# ---------------------------------------------------------------------------

def category_rule_label(cat):
    """Return the filter-group label for a meterCategory, or None if not used."""
    if cat == 'Virtual Machines':    return 'Compute'
    if cat == 'Storage':             return 'Storage'
    if cat in NETWORKING_CATEGORIES: return 'Networking'
    return None

def sort_key(cat):
    order = {'Compute': 0, 'Storage': 1, 'Networking': 2, None: 3}
    return (order[category_rule_label(cat)], cat)

def classify_chunk(chunk):
    """Return a boolean Series: True = included by filter logic, False = excluded."""
    cat  = chunk['meterCategory']
    sub  = chunk['meterSubCategory']
    name = chunk['meterName']

    compute = cat == 'Virtual Machines'
    storage = (
        (cat == 'Storage') &
        sub.str.contains('Managed Disks', na=False) &
        ~name.str.contains('Disk Operations', na=False)
    )
    network = cat.isin(NETWORKING_CATEGORIES)

    return compute | storage | network

# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def build_breakdown_df(counts):
    """Build the flat meterCategory/meterSubCategory breakdown as a DataFrame."""
    rows = []
    for cat in sorted(counts, key=sort_key):
        for sub in sorted(counts[cat]):
            incl  = counts[cat][sub]['included']
            excl  = counts[cat][sub]['excluded']
            total = incl + excl
            pct   = incl / total * 100 if total else 0
            rule  = category_rule_label(cat) or 'NOT USED'
            rows.append([rule, cat, sub, total, incl, excl, f'{pct:.0f}%'])

    return pd.DataFrame(rows, columns=[
        'Group', 'meterCategory', 'meterSubCategory',
        'Total Rows', 'Included', 'Excluded', '% Included'
    ])

def write_excel(excel_path, counts, total_rows, total_included, input_path, csv_files):
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

    rules_df = pd.DataFrame(FILTER_RULES, columns=['Group', 'Filter Logic'])

    cat_rows = []
    for cat in sorted(counts, key=sort_key):
        sub_counts = counts[cat]
        cat_incl   = sum(v['included'] for v in sub_counts.values())
        cat_excl   = sum(v['excluded'] for v in sub_counts.values())
        cat_total  = cat_incl + cat_excl
        rule       = category_rule_label(cat) or 'NOT USED'
        pct        = cat_incl / cat_total * 100 if cat_total else 0
        cat_rows.append([rule, cat, cat_total, cat_incl, cat_excl, f'{pct:.1f}%'])

    cat_summary_df = pd.DataFrame(cat_rows, columns=[
        'Group', 'meterCategory', 'Total Rows', 'Included', 'Excluded', '% Included'
    ])

    breakdown_df = build_breakdown_df(counts)

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
            print(f"No CSV files found in folder: {input_path}")
            exit(1)
        return sorted(files)
    else:
        print(f"Input path not found: {input_path}")
        exit(1)

def main():
    parser = argparse.ArgumentParser(
        description=(
            'Analyse CSV files and show a row-count breakdown by filter group.\n'
            'Optionally exports a 2-sheet Excel report (Summary, Breakdown).'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python3 csvStats.py --input data.csv\n'
            '  python3 csvStats.py --input ./csv_folder\n'
            '  python3 csvStats.py --input ./csv_folder --excel --output report.xlsx\n'
        )
    )
    parser.add_argument('--input',  '-i', required=True,
                        metavar='PATH',
                        help='Single CSV file or folder containing CSV files')
    parser.add_argument('--excel',  '-e', action='store_true',
                        help='Export a 2-sheet Excel report (Summary, Breakdown)')
    parser.add_argument('--output', '-o', default='csvStats_report.xlsx',
                        metavar='FILE',
                        help='Excel output path (default: csvStats_report.xlsx)')
    args = parser.parse_args()

    csv_files  = get_csv_files(args.input)
    total_files = len(csv_files)
    W = _term_width()

    # Clear screen then print header
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
    print()
    input_label = os.path.basename(os.path.abspath(args.input))
    print(f"  csvStats  ·  {input_label}  ·  {total_files} file{'s' if total_files > 1 else ''}")
    print(f"  {'─' * (W - 4)}")
    print()

    # counts[meterCategory][meterSubCategory] = {'included': n, 'excluded': n}
    counts = collections.defaultdict(
        lambda: collections.defaultdict(lambda: {'included': 0, 'excluded': 0})
    )
    total_rows     = 0
    total_included = 0
    skipped        = []

    # Only the 3 columns needed for classification are loaded — fast on large files
    classify_cols = ['meterCategory', 'meterSubCategory', 'meterName']

    start_time = time.time()

    for i, csv_file in enumerate(csv_files, 1):
        filename = os.path.basename(csv_file)
        draw_progress(i, total_files, filename)
        file_rows = 0
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
                chunk['meterCategory']    = chunk['meterCategory'].str.strip().fillna('(blank)')
                chunk['meterSubCategory'] = chunk['meterSubCategory'].str.strip().fillna('(blank)')
                chunk['meterName']        = chunk['meterName'].str.strip().fillna('(blank)')

                # Classify each row as included or excluded
                mask = classify_chunk(chunk)
                total_included += int(mask.sum())

                # Accumulate counts per category / subcategory
                chunk['_included'] = mask
                for (cat, sub, inc), cnt in (
                    chunk.groupby(['meterCategory', 'meterSubCategory', '_included']).size().items()
                ):
                    counts[cat][sub]['included' if inc else 'excluded'] += cnt

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

    # Aggregate totals by group (Compute / Storage / Networking / NOT USED)
    group_totals = collections.OrderedDict([
        ('Compute',    {'included': 0, 'excluded': 0}),
        ('Storage',    {'included': 0, 'excluded': 0}),
        ('Networking', {'included': 0, 'excluded': 0}),
        ('NOT USED',   {'included': 0, 'excluded': 0}),
    ])
    for cat in counts:
        key = category_rule_label(cat) or 'NOT USED'
        for sub in counts[cat]:
            group_totals[key]['included'] += counts[cat][sub]['included']
            group_totals[key]['excluded'] += counts[cat][sub]['excluded']

    # -----------------------------------------------------------------------
    # Compact summary — fits in 80x24
    # -----------------------------------------------------------------------
    W  = _term_width()
    dw = W - 2  # inner width (2 for leading spaces)

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
    grp_w   = max(len(g) for g in group_totals) + 2
    print(f"  {'Group':<{grp_w}}  {'Total':>{count_w}}    {'Included':>{count_w}}    {'%':>6}")
    rule()
    for grp, v in group_totals.items():
        g_total = v['included'] + v['excluded']
        g_pct   = v['included'] / g_total * 100 if g_total else 0
        warn    = ""
        print(f"  {grp:<{grp_w}}  {g_total:>{count_w},}    {v['included']:>{count_w},}    {g_pct:5.1f}%{warn}")

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
                    args.input, csv_files)
        blank()

if __name__ == '__main__':
    main()
