# App Store & Google Play Reviews Scraper

Scrape, export and download app reviews from the Apple App Store (iOS apps) and the Google Play Store for any app, country and language, in one table. Use it as an App Store reviews scraper, a Google Play reviews scraper or an app reviews API. I built it on the stores' public JSON endpoints with plain HTTP requests, so there is no browser to slow it down.

## At a glance

| | |
|---|---|
| Use it when | You need to export Google Play reviews, download App Store reviews of iOS apps, or both, for one or more apps and countries: for app review analysis, review monitoring or competitor research. |
| Input you need | The App Store or Google Play link of each app in `apps` (required; IDs and package names also work, but you never need to look them up); optionally `countries`, `sort`, `maxReviewsPerApp`, `since`. |
| What you get | One row per review with rating, text, ISO date, app version, country and, on Google Play, the developer reply. |
| Cost | $0.12 per 1,000 reviews saved. |
| Limits | Apple's public feed stops at 500 reviews per country and sort order; set App Store source to `website` to go past it. Reviewer names are off by default. |

I built this Actor and test it against what the live site shows. The check runs again every week.

## What you get

One row per review, with the same fields for both stores:

| Field | Example | Notes |
|---|---|---|
| `store` | `googlePlay` | `appStore` or `googlePlay` |
| `appId` | `com.whatsapp` | App Store ID or Play package name |
| `country` | `us` | Every row carries the country it came from |
| `language` | `en` | Google Play only; Apple's feed follows the country |
| `reviewId` | `d3bda379-...` | Stable ID from the store |
| `rating` | `4` | 1 to 5 |
| `title` | `Great app` | App Store only |
| `text` | `I love this app` | |
| `date` | `2026-10-06T15:54:48.000Z` | ISO 8601, UTC, always present |
| `appVersion` | `2.26.38.73` | When the store provides it |
| `thumbsUpCount` | `12` | Helpful votes |
| `developerReply`, `developerReplyDate` | | Google Play (Apple's public feed has no replies) |
| `reviewUrl` | | A link that opens: the review on Google Play, the app's reviews page on the App Store |
| `schemaVersion` | `1` | Changes only if the schema ever changes |

Reviewer names and profile links are **off by default**. Turn on **Include reviewer names and profile links** to add `userName`, `userUrl` (App Store) and `userImage` (Google Play).

<!-- sections from scripts/gtm/readme_sections.py -->
## Sample output

One real row from a run on 11 Oct 2026 (selected fields):

```json
{
  "store": "appStore",
  "appId": "389801252",
  "country": "us",
  "rating": 5,
  "title": "Marvelous",
  "text": "Great to my build my community",
  "date": "2026-10-09T18:14:22.000Z",
  "appVersion": "450.1.0",
  "reviewUrl": "https://apps.apple.com/us/app/id389801252?see-all=reviews"
}
```

<!-- sections from scripts/gtm/readme_sections.py -->

## What this Actor handles for you

1. **Complete paging.** This Actor pages through everything each store returns. Google Play pages run until the store has no more reviews. Apple's public feed has a hard limit of 10 pages of 50 reviews (500) per country and sort order; page 11 answers HTTP 400. Apple's feed also returns some pages empty even though they hold reviews (seen on 8 October 2026); the Actor retries each empty page, carries on to the next one, and lists any page that stayed empty in `RUN_SUMMARY`, so you know exactly what may be missing. If Apple's feed serves no reviews at all, which it sometimes does for every app at once, the Actor checks a control app with a very large number of reviews and, when that is empty too, collects that app and country from the App Store website instead, which has no app version or vote counts. To go past the feed's 500 reviews per country and sort order, set App Store source to `website`: one run returned 1,000 reviews for one app and country. The Actor tells you when the 500 limit is reached, and you can collect more App Store reviews by adding countries, each of which has its own limit. `maxReviewsPerApp` is honoured exactly, and the run summary says why a store ran out if it did.
2. **Many countries in one run.** Every country you list is fetched separately, and every row carries its `country`.
3. **A stable schema.** One flat schema for both stores, versioned with `schemaVersion`. Fields a store does not have are present as `null`, never missing.
4. **Dates and links on every row.** `date` is always an ISO timestamp. `reviewUrl` always opens a real page: the review itself on Google Play, and the app's reviews page on the App Store (Apple has no per-review page).
5. **Errors do not stop the run, and any app format works.** Pages that fail are retried, and an app a store does not have is reported in the run summary while the run continues (`continueOnError`, on by default). Paste store links as they are; App Store IDs and Play package names also work, mixed in one list.

## Input

| Field | Default | What it does |
|---|---|---|
| `apps` | | Store links (IDs and package names are optional alternatives), both stores mixed |
| `stores` | both | Which stores to read |
| `countries` | `["us"]` | Two-letter country codes |
| `languages` | `["en"]` | Google Play languages |
| `sort` | `newest` | `newest`, `mostHelpful` or `rating` (Apple has no rating sort and uses newest) |
| `appStoreSource` | `auto` | `auto`: Apple's public feed, with app version and vote counts, and the App Store website when the feed serves no reviews. `website`: always the App Store website, past 500 reviews per country, without app version or vote counts |
| `maxReviewsPerApp` | 500 | Per app, store and country (and language on Play). 0 means no limit |
| `since` | | Only reviews on or after this date |
| `ratings` | all | Keep only these stars |
| `includeDeveloperReplies` | true | Reply text and date |
| `includeReviewerInfo` | false | Reviewer names and profile links |
| `continueOnError` | true | Skip and report a failing app instead of stopping |

## Pricing

Pay per result: **$0.12 per 1,000 reviews**. You are charged only for reviews saved to your dataset, never for errors, retries or duplicates. If you set a maximum cost per run, the Actor stops cleanly when it is reached.

## Reliability

If a run is restarted or migrated, it resumes where it stopped and does not deliver or charge the same review twice. Each run saves a `RUN_SUMMARY` record with counts per app and country, why each store stopped, and any errors.

## Scheduled monitoring

Schedule the Actor weekly with `since` set to last week's date to receive only reviews from that date on.

## Use it as an API

Every run can be started and read through the Apify API. One request runs the Actor with your input as the JSON body and returns the reviews as JSON, or as CSV or Excel with `format=csv` or `format=xlsx`:

`POST https://api.apify.com/v2/acts/garje~app-store-play-reviews-scraper/run-sync-get-dataset-items?format=csv`

Send your Apify API token in the `Authorization` header. A worked example in Python: [How to export Google Play reviews to CSV for any app and country](https://dev.to/garje/how-to-export-google-play-reviews-to-csv-for-any-app-and-country-g8f).

<!-- sections from scripts/gtm/readme_sections.py -->
## Use it in n8n, Make or Zapier

Apify has integrations for n8n, Make and Zapier. In each, add a step that runs an Actor, choose `garje/app-store-play-reviews-scraper`, paste your input as JSON, and wait for the run to finish; the next step reads the dataset items. A schedule step in front of it turns this into a daily or weekly feed into Google Sheets, Slack or email.

## Use it from an AI agent (MCP)

Add `https://mcp.apify.com?tools=garje/app-store-play-reviews-scraper` as an MCP server in Claude, Cursor, VS Code or any other MCP client. The agent then has this Actor as one tool and runs it with your Apify account, for example: "Get the newest 200 Google Play reviews of com.whatsapp and tell me what people complain about."

<!-- sections from scripts/gtm/readme_sections.py -->

## Use cases

Each one is a ready task you can open, run and copy:

- [Export app reviews from several countries](https://apify.com/garje/app-store-play-reviews-scraper/examples/app-reviews-several-countries): App Store reviews by country, four countries in one table with the country on every row.
- [Monitor new app reviews since a date](https://apify.com/garje/app-store-play-reviews-scraper/examples/app-reviews-new-since-date): only reviews from a date on. Schedule it daily and move the date forward.
- [Export only 1 and 2 star app reviews](https://apify.com/garje/app-store-play-reviews-scraper/examples/app-reviews-one-two-stars): read the complaints first, with app version and developer reply.
- [Newest iOS App Store reviews for one app](https://apify.com/garje/app-store-play-reviews-scraper/examples/ios-app-reviews-newest): the 100 newest US App Store reviews of one iPhone app.
- [One app's reviews from both stores in one call](https://apify.com/garje/app-store-play-reviews-scraper/examples/app-store-reviews-api-both-stores): App Store and Google Play reviews of the same app in one schema, with the store on every row.
- [Download reviews of three competing apps](https://apify.com/garje/app-store-play-reviews-scraper/examples/download-app-reviews-three-apps): recent Google Play reviews of three apps in one category, with the app on every row, ready as CSV or Excel.
- [400 app reviews ready for analysis](https://apify.com/garje/app-store-play-reviews-scraper/examples/app-review-analysis-400-reviews): rating, text, app version and developer reply, for sentiment or topic analysis.

<!-- sections from scripts/gtm/readme_sections.py -->
## FAQ

**Do I need the app's ID?**
No. Paste the App Store or Google Play link of each app. IDs and package names work too, but you never need to look them up.

**Does it work on Apify's free plan?**
Yes, with no item limit from this Actor. On a free account, a run asked for 100 App Store reviews returned all 100 in 9 seconds.

**How many App Store reviews can I get per country?**
Apple's public feed stops at 500 reviews per country and sort order. With `appStoreSource` set to `website`, the Actor reads the App Store website instead and goes past that: 1,000 reviews for one country in one test run.

**Are reviewer names included?**
Reviewer names are off by default. Turn on `includeReviewerInfo` only if you need them.

**Can I get only new reviews?**
Yes. Set `since` to a date and `sort` to `newest`, then schedule the run.

<!-- sections from scripts/gtm/readme_sections.py -->

## Read more

I write up what the data shows, with the run ID behind every number:

- [How to export Google Play reviews to CSV for any app and country](https://dev.to/garje/how-to-export-google-play-reviews-to-csv-for-any-app-and-country-g8f)

- [Collect only new app reviews each week](https://garje-data-notes.laude--pify.workers.dev/articles/collect-only-new-app-reviews-each-week.html)
- [How often developers reply to Google Play reviews](https://garje-data-notes.laude--pify.workers.dev/articles/developer-replies-in-google-play-reviews.html)
- [What 4,608 app reviews look like across the App Store and Google Play](https://garje-data-notes.laude--pify.workers.dev/articles/star-ratings-in-4608-app-reviews.html)
- [Facts and limits of this Actor](https://garje-data-notes.laude--pify.workers.dev/facts/app-store-play-reviews-scraper.html) and its [changelog](https://garje-data-notes.laude--pify.workers.dev/changelog/app-store-play-reviews-scraper.html)
