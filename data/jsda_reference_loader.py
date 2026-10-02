"""
jsda_reference_loader.py

Special Phase B: reads one day of JSDA's "Reference Statistical Prices for
OTC Bond Transactions" (公社債店頭売買参考統計値) and keeps the fixed-coupon
JGBs -- the real bonds behind MOF's curve.

NOT COMMITTED, NOT FETCHED. JSDA's site terms forbid reuse or copying
without permission (https://www.jsda.or.jp/menseki/), so the raw file is
never committed and this module never downloads it. Download one file by
hand into data/jsda/ (gitignored):

    https://market.jsda.or.jp/shijyo/saiken/baibai/baisanchi/files/<YYYY>/S<YYMMDD>.csv

DATED THE NEXT BUSINESS DAY. A file holds the PREVIOUS business day's 3pm
quotes: S260901.csv matches MOF's 2026-08-31 curve. Checked against MOF:
S260901 sits 0.2bp from MOF 08-31 on average, S260831 sits 1.0bp away
(but 0.4bp from MOF 08-28). See docs/special_phase_b_documentation.md §3.

COLUMNS USED (0-based, Shift-JIS file, no header row): 0 file date,
1 category ("02" = JGBs), 2 issue code, 3 issue name, 4 maturity
(YYYYMMDD), 5 coupon (% p.a.), 6 average compound yield (%), 7 average
clean price, 9 coupon months ("03/09"), 10 coupon day, 14 average simple
yield (%). Checked, not assumed: recomputing simple yield from price and
coupon matches column 14 to 0.09bp (median, all JGBs over 1Y).
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

import pandas as pd

JSDA_DIR = Path(__file__).resolve().parent / "jsda"
JGB_CATEGORY = "02"

# Issue-name prefix -> the original-maturity class MOF's methodology
# groups bonds by (docs/phase_4_5a_documentation.md §1). GX (climate
# transition) bonds and when-issued (WI) lines are left out.
_NAME_CLASS = [
    (re.compile(r"^中期国債 ?\d+\((\d+)\)$"), None),  # class in brackets: 2 or 5
    (re.compile(r"^長期国債 ?\d+$"), 10),
    (re.compile(r"^超長期国債\((\d+)\) ?\d+$"), None),  # 30 or 40
    (re.compile(r"^超長期国債 ?\d+$"), 20),
]


def _original_maturity_class(name: str) -> int | None:
    for pattern, fixed_class in _NAME_CLASS:
        match = pattern.match(name)
        if match:
            return fixed_class if fixed_class is not None else int(match.group(1))
    return None


def _issue_number(name: str) -> int:
    # The number after the class prefix, e.g. "長期国債 383" -> 383,
    # "中期国債 186(5)" -> 186, "超長期国債(40)19" -> 19.
    without_class = re.sub(r"^[^\d(]*(\(\d+\))?", "", name)
    return int(re.match(r"\s*(\d+)", without_class).group(1))


def _parse_date(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def load_jsda_jgbs(path: str | Path) -> pd.DataFrame:
    """Fixed-coupon JGBs from one JSDA reference file.

    Columns: issue_code, name, maturity_class (2/5/10/20/30/40),
    issue_number, maturity_date, coupon (decimal), clean_price (per 100),
    compound_yield (decimal), simple_yield (decimal), coupon_months
    (tuple of two ints), coupon_day.
    """
    rows = []
    with open(path, encoding="shift_jis", newline="") as handle:
        for record in csv.reader(handle):
            if len(record) < 15 or record[1] != JGB_CATEGORY:
                continue
            name = record[3].strip()
            maturity_class = _original_maturity_class(name)
            if maturity_class is None:
                continue
            months = tuple(int(m) for m in record[9].split("/"))
            rows.append({
                "issue_code": record[2],
                "name": name,
                "maturity_class": maturity_class,
                "issue_number": _issue_number(name),
                "maturity_date": _parse_date(record[4]),
                "coupon": float(record[5]) / 100.0,
                "clean_price": float(record[7]),
                "compound_yield": float(record[6]) / 100.0,
                "simple_yield": float(record[14]) / 100.0,
                "coupon_months": months,
                "coupon_day": int(record[10]),
            })
    if not rows:
        raise ValueError(f"no fixed-coupon JGBs found in {path}")
    return pd.DataFrame(rows).sort_values("maturity_date").reset_index(drop=True)
