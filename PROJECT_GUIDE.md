# Project guide: three Apify Actors

Author: Aniruddha Garje. This guide explains what each part of this repository does. It is written for two readers: a person who wants to understand or extend the code, and an AI assistant such as Claude that is handed this repository and needs to make sense of it quickly. If you are an assistant, read sections 1 to 4 first, then section 7 before you change anything.

## 1. What this repository is

Three web data tools ("Actors") that run on the Apify platform. Each one collects public data with plain HTTP requests, no browser, and saves one row per result to an Apify dataset. Each charges the user per result saved (Apify's pay-per-event model).

| Folder | What it collects | Source | Charged event (price per result) |
|---|---|---|---|
| `app-store-play-reviews-scraper/` | App reviews: rating, text, date, app version, country, developer reply | Apple App Store public feeds and Google Play's public web endpoint | `review` ($0.00012) |
| `google-ads-transparency-scraper/` | Ads an advertiser has run: format, first and last shown dates, image and video links, headline and text where available | Google Ads Transparency Center public endpoints | `ad` ($0.0015), `asset` ($0.003 per downloaded image) |
| `google-hotels-scraper/` | Hotel prices for a city search across a calendar of check-in dates, plus each booking site's offer | Google Hotels pages (data Google embeds in the HTML) | `hotel` ($0.00199), `offer` ($0.001) |

Stack: Python 3.13, Apify SDK 4.0.2 and Crawlee 1.10.4 (`requirements.txt` in each folder), Docker image `apify/actor-python:3.13`.

## 2. Layout of each Actor folder

All three Actors share the same shape, so learning one teaches you the others.

| Path | Purpose |
|---|---|
| `.actor/actor.json` | Actor name, title, version, default memory (Reviews and Ads 1 GB, Hotels 2 GB) and links to the schemas below |
| `.actor/input_schema.json` | Every input field, with a description, an example and limits. This is what users see as the input form |
| `.actor/dataset_schema.json`, `output_schema.json` | The output fields and the views shown in Apify Console |
| `.actor/pay_per_event.json` | The chargeable events and their prices. Tests check the code charges only these |
| `src/main.py` | Entry point: reads and validates input, builds requests, runs the Crawlee HTTP crawler, delivers rows, writes a run summary |
| `src/parsers.py` | Pure functions that turn a saved response into rows. No network calls, so they are easy to test |
| `src/delivery.py` | The only place rows are saved and charged: removes duplicates, enforces caps, pushes to the dataset, then charges for exactly what was pushed |
| `src/invariants.py` | Rules every delivered row must satisfy (for example: a date is ISO 8601, a price is positive) |
| `src/run_checks.py` | Runs the invariants at the end of every run, plus an optional "drift canary" that compares field profiles with a saved baseline and fails the run if they change too much |
| `src/metrics.py` | Counts every HTTP attempt, including retries, so each run reports its real success rate |
| `src/limits.py` | Ads only: the exact per-domain cap |
| `tests/` | Unit tests that run offline against saved real responses in `tests/fixtures/` |
| `tests/golden/` | Golden sets: what a real browser showed on the live site, used by the accuracy audit in `scripts/` |
| `README.md` | The public page users read on the Apify Store |
| `Dockerfile`, `requirements.txt`, `pytest.ini` | Build and test setup |

## 3. What each Actor does, in more detail

### App Store and Google Play Reviews (`app-store-play-reviews-scraper`)

- **Input:** `apps` (App Store IDs or links, Google Play package names or links, mixed), plus optional `stores`, `countries`, `languages`, `sort`, `maxReviewsPerApp`, `since` (only reviews on or after a date), `ratings` (for example only 1 and 2 stars).
- **How:** each app, store and country is fetched separately and every row carries its `country`. Both stores share one flat schema, and a field a store does not have is `null`, never missing.
- **Things to know:** Apple's public feed stops at 10 pages of 50 reviews (500) per country and sort order. The `appStoreSource` input chooses between that feed (`auto`, the default, with app version and vote counts) and the App Store website (`website`, which goes past 500 but has no app version or vote counts); `auto` switches to the website when the feed serves no reviews. Apple sometimes serves an empty page that still holds reviews, so empty pages are retried, the walk carries on past them, and pages that stay empty are listed in the `RUN_SUMMARY` record. Reviewer names are off by default (`includeReviewerInfo`).

### Google Ads Transparency (`google-ads-transparency-scraper`)

- **Input:** at least one of `domains`, `advertiserIds` or `searchTerms`, plus `region`, `dateFrom`, `dateTo`, `formats`, `platforms`, `maxAdsPerAdvertiser`, `maxAdsPerDomain`, `onlyNewSince` (monitoring mode) and `downloadAssets`.
- **How:** calls the same public endpoints the Transparency Center website uses, then reads each ad's preview for its content.
- **Things to know:** a name search scores every advertiser Google suggests (`match_score` in `parsers.py`: 1.0 for an exact name after dropping legal forms such as Inc. or GmbH) and by default scrapes only exact matches. Every candidate is saved in the `SEARCH_MATCHES` record. Dates come both as UTC timestamps and as the site displays them (US Pacific time). Most text ads are published as a single image, so `contentStatus` on every ad says why a text field is empty.

### Google Hotels (`google-hotels-scraper`)

- **Input:** `queries` (for example "hotels in Paris"), a date range as fixed dates (`checkInFrom`, `checkInTo`) or as days after the run date (`checkInFromDaysAhead`, `checkInToDaysAhead`, best for scheduled runs), plus `nights`, `adults`, `children`, `currency`, `country`, `filters`, `includeOffers`, `includeUnavailable`, `maxHotelsPerQuery`.
- **How:** each search is first resolved to the place Google understood, then every date is searched with that place. Every row carries `resolvedPlace`.
- **Things to know:** a search Google does not recognise is skipped and not charged. Hotels without a price are free and only returned with `includeUnavailable`. Offers are fetched only for hotels actually delivered. Google's hotel list changes between visits, so results can differ from what a browser shows. Named-hotel tracking (`hotelUrls`) is switched off behind the `HOTELS_NAMED_MODE` environment variable.

## 4. How to run it

```bash
# once, from the repository root
python -m venv .venv
.venv/Scripts/pip install -r google-hotels-scraper/requirements.txt pytest pytest-asyncio   # on macOS or Linux: .venv/bin/pip

# offline unit tests for one Actor (no account, no network)
cd google-hotels-scraper
../.venv/Scripts/python -m pytest -q
```

To run an Actor for real you need a free Apify account and the Apify CLI (`npm install -g apify-cli`), then `apify login` and, inside an Actor folder, `apify run` (local) or `apify push` (build on the platform). Never put an API token in a file in this repository; the CLI keeps it for you.

## 5. Scripts (`scripts/`)

These need an Apify account and, for the browser captures, Playwright. They write their results to a `reports/` folder they create.

| Script | What it does |
|---|---|
| `pw_capture.py` | Opens the live sites in a real browser (Playwright) and records what a person sees: the independent truth for the accuracy check |
| `build_golden.py` | Turns those browser captures into `tests/golden/golden.json` |
| `audit.py` | Runs an Actor on the golden inputs on the platform and compares its output with the golden set: recall, exact fields, prices |
| `accuracy_check.py` and `weekly_accuracy.cmd` | Capture, run and compare in one command; the weekly check |
| `hotels_baseline.py` | Measures how much two browser visits agree with each other, which sets a fair pass mark for Hotels |
| `audit_inputs.py` | Checks every input field has an example and every input error message shows a valid example |
| `default_runs.py` | Runs each Actor with the input a new user sees first and checks it succeeds quickly with results |
| `spike.py`, `probe_runs.py` | Measure success rate, cost and field completeness over many runs |
| `run_ppe_local.py` | Runs an Actor locally in pay-per-event test mode |
| `setup_canaries.py` | Creates one daily drift-canary task per Actor |
| `replay_ads_rpc.py` | Serves saved Ads responses on localhost for offline local runs |
| `user_test.py` and `user_test_*.cmd` | Runs the published Actors from a second test account, the way a new user would |

## 6. How quality is checked

1. **Unit tests** run offline on saved real responses. A test never uses made-up output.
2. **Invariants** run in the tests and at the end of every platform run. A broken rule fails the run with "INVARIANT CHECK FAILED" in its status message.
3. **Accuracy audits** compare the Actor with a real browser on the live site (scripts above). Pass marks: recall at least 95% (Hotels uses 84.2%, the measured browser-to-browser agreement minus 3 points), exact fields at least 98%, hotel prices within 10% for at least 90% of hotels.
4. **Drift canaries** run daily with a fixed input and fail when the share of empty fields rises past a threshold.

## 7. Rules of this codebase (read before changing code)

1. Never fake or hardcode output to make a test pass. Tests use saved real responses in `tests/fixtures/`.
2. Charge only through `src/delivery.py`, and only after the row is saved. Never charge for errors, retries, duplicates or rows the user did not receive.
3. Change prices only in `.actor/pay_per_event.json`, and keep `tests/test_prices.py` in step.
4. Every input field needs a description and an example; every input error message shows a valid example (`scripts/audit_inputs.py` checks this).
5. A row field a source does not provide is `null`, never missing, and the schema version stays in `schemaVersion`.
6. Personal data stays out by default (reviewer names are opt-in). The fixtures in this repository have reviewer, contributor and individual advertiser names replaced with placeholders.
7. Keys and tokens live only in the Apify CLI or environment variables, never in files.

## 8. Good first tasks

- Run all three test suites and read one test file per Actor next to the parser it tests.
- Add a new field to one Actor end to end: parser, row, invariant, dataset schema, test with a saved response.
- Add a new input check with an error message that shows a valid example, and a test for it.
- Read `google-hotels-scraper/src/run_checks.py` and its tests to see how the drift canary decides what counts as drift.
