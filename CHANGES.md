# CHANGES

## [Unreleased] — Code Quality & Correctness (session 84)

### Correctness fixes

- **Fix 1A** (`03_Analysis/_scoring.py`): Guard `idxmax()` against all-NaN close prices.
  Added `.dropna()` before `idxmax()` so a delisted ticker or data-feed gap no longer
  crashes with `ValueError` (pandas ≥ 2.1) or silently returns `NaN` (pandas ≤ 1.5).
  `peak_date` is correctly `None` for events with no valid close data.

- **Fix 1B** (`src/constants.py`, `03_Analysis/_scoring.py`): Renamed
  `EXERCISE_PEAK_WINDOW_DAYS` → `EXERCISE_PEAK_WINDOW_CALENDAR_DAYS` and added an
  explicit comment clarifying the intentional use of calendar days (not trading days)
  for the Flag 2 peak-proximity check. Prevents future contributors from misreading
  the unit and inadvertently changing flag counts.

- **Fix 1C** (`cli.py`): Removed hardcoded `BENEISH_THRESHOLD = -1.78` inside
  `batch_report` and replaced with `from src.constants import BENEISH_THRESHOLD`.
  Eliminates silent threshold drift if the canonical constant is updated.

### Performance improvements

- **Fix 2A** (`03_Analysis/_scoring.py`): Pre-grouped `officer_holdings` by `corp_code`
  into a dict before the CB/BW event loop. Replaces O(n×m) boolean filter (3,671 events
  × 6,957 rows) with a single `groupby` + O(1) dict lookups. Estimated 30–50% reduction
  in `score_events()` wall time.

- **Fix 2B** (`03_Analysis/_scoring.py`): Vectorized `score_disclosures()` — replaced
  the `iterrows` loop + per-row MultiIndex lookups with `merge()` on (ticker, check_date).
  Eliminates ~543K sequential Python-level index lookups at 271K disclosures × 2 offsets.
  Estimated 5–10× speedup in `score_disclosures()` wall time.

- **Fix 2C** (`03_Analysis/_scoring.py`): Replaced `iterrows()` with `itertuples()` in
  `score_events()`. Eliminates integer→float silent dtype upcast in mixed-type rows.
  Field access updated from `event["col"]` / `event.get(...)` to `event.col` /
  `getattr(event, "col", default)`.

### Infrastructure hygiene

- **Fix 3A** (`02_Pipeline/extract_price_volume.py`, `extract_dart.py`,
  `extract_cb_bw.py`): Moved Windows UTF-8 stdout fix from module-level side effect
  (`sys.stdout = open(...)`) into a `_configure_stdout()` helper called only inside
  `if __name__ == "__main__":`. Matches the pattern already in `cli.py`. Prevents
  breaking pytest `capsys` capture when these modules are imported in tests.

- **Fix 3B** (`src/constants.py`, `cli.py`): Added `VALID_OHLCV_BACKENDS` to
  `src/constants.py`. Both `run` and `refresh` commands in `cli.py` now import from
  this single source of truth instead of maintaining separate hardcoded tuples.

- **Fix 3C** (`cli.py`): Replaced `lock = __import__("threading").Lock()` with a
  standard `import threading; lock = threading.Lock()`.

### Tests added (`tests/test_pipeline_invariants.py` — Category 33)

| Test | Guards |
|------|--------|
| `test_score_events_all_nan_close_does_not_crash` | Fix 1A crash vector |
| `test_beneish_threshold_not_hardcoded_in_batch_report` | Fix 1C threshold drift |
| `test_score_disclosures_output_schema_and_flag_logic` | Fix 2B regression guard |
