# Google Hotels Scraper: Prices, OTA Rates and Rate Parity

A Google Hotels scraper and API for hotel prices by date. Scrape Google Hotels (Google Travel) prices for any city search across a calendar of check-in dates, in the currency you choose. Every row carries the stay length and occupancy. Turn on offers to add the price each booking site shows (Booking.com, Expedia, Agoda, the hotel's own site and more) for rate parity checks. I built it to read the data Google embeds in its own pages with plain HTTP requests, with no browser.

## At a glance

| | |
|---|---|
| Use it when | You need Google Hotels nightly prices for a city over one or many check-in dates, optionally with each booking site's price: as a hotel price tracker, for hotel rate shopping and rate monitoring, or for hotel price comparison across booking sites. |
| Input you need | A search in `queries`, for example "hotels in Paris" (required); dates as `checkInFrom`/`checkInTo` or `checkInFromDaysAhead`/`checkInToDaysAhead`; `nights`, `adults`, `currency`, `country`. |
| What you get | One row per hotel and check-in date with nightly price, stay and occupancy echoed, and the place Google matched; with `includeOffers`, one row per booking site. |
| Cost | $1.99 per 1,000 hotel prices; $1.00 per 1,000 booking-site offers. |
| Limits | The result list can differ from what a browser shows. Named-hotel tracking by link is coming soon. |

I built this Actor and test it against what the live site shows. The check runs again every week.

## What you get

One row per hotel and check-in date (`type: "hotel"`):

| Field | Example |
|---|---|
| `name`, `hotelId`, `url` | `Oden Hôtel Paris Ivry`, Google Hotels link |
| `checkInDate`, `checkOutDate`, `nights` | `2026-11-12`, `2026-11-14`, `2` (as Google reported them) |
| `adults`, `children`, `childAges` | `2`, `0`, `[]` |
| `currency`, `pricePerNight`, `pricePerNightText`, `totalPriceText` | `USD`, `97.95`, `$98`, `$196` |
| `hotelClass`, `rating`, `reviewCount` | `4`, `4.7`, `158` |
| `latitude`, `longitude`, `countryCode`, `website`, `description` | |
| `dealText` | `16% less than usual` |
| `query`, `resolvedPlace`, `country` | The search, the place Google read it as (for example `Paris`), and the searcher country |

With **Include per-site offers** on, one extra row per booking site (`type: "offer"`) with `provider`, `pricePerNight` and `totalPrice` for that hotel and date.

<!-- sections from scripts/gtm/readme_sections.py -->
## Sample output

One real row from a run on 11 Oct 2026 (selected fields):

```json
{
  "name": "Court Square Hotel Long Island City New York, Belvilla District 6 Formerly Sonder",
  "hotelClass": 4,
  "rating": 4,
  "reviewCount": 429,
  "checkInDate": "2026-11-10",
  "checkOutDate": "2026-11-12",
  "nights": 2,
  "adults": 2,
  "currency": "USD",
  "pricePerNight": 140.27,
  "totalPriceText": "$281",
  "country": "US",
  "url": "https://www.google.com/travel/hotels/entity/ChoIn8Lf9cvOi9DxARoNL2cvMTFycThtZ3g0bRAB"
}
```

<!-- sections from scripts/gtm/readme_sections.py -->

## What this Actor handles for you

1. **Blocked pages are retried, never charged.** Google sometimes answers with a rate-limit page. This Actor detects it, switches to a fresh IP and retries, and it never saves or charges a blocked page. Each run writes `RUN_STATS` with pages fetched, blocked responses and failed requests, so you can see the success rate yourself.
2. **The stay length you asked for.** `nights` is sent to Google the same way Google's own date picker does, and the check-in date, check-out date and nights Google actually priced are read back from Google's data and echoed on every row.
3. **Filters.** Hotel class (for example 4 stars) and amenities (for example free Wi-Fi) are applied by Google itself, the same way its own filter panel applies them. Minimum rating and price range are applied to the results. There is no free-cancellation filter.
4. **A full date calendar, checked up front.** One search runs for every check-in date between `checkInFrom` and `checkInTo`, so the calendar has no gaps. Past dates are rejected before the run starts, with a clear message, so they cost nothing. For scheduled runs, set `checkInFromDaysAhead` and `checkInToDaysAhead` (days after the run date) instead of fixed dates, so the calendar moves forward with every run and never expires.
5. **Your currency.** Set a three-letter ISO 4217 currency code; measured with USD, INR, IDR and AED.

## Input

| Field | Default | What it does |
|---|---|---|
| `queries` | | City searches as typed into Google, for example `hotels in Paris` |
| `hotelUrls` | `[]` | Coming soon: named-hotel tracking. A run with hotel links stops at once and costs nothing |
| `checkInFrom`, `checkInTo` | tomorrow | Price calendar range as fixed dates |
| `checkInFromDaysAhead`, `checkInToDaysAhead` | | The same range as days after the run date, for scheduled runs |
| `nights` | 1 | Stay length |
| `adults`, `children`, `childrenAges` | 2, 0 | Occupancy |
| `currency` | `USD` | Three-letter ISO 4217 code, for example USD |
| `country` | `US` | Searcher country; also pins the proxy country, because Google's prices follow the requester's country |
| `filters` | `{}` | `hotelClass`, `amenities`, `minRating`, `priceMin`, `priceMax` |
| `includeOffers` | false | Per-site offers as a separate paid event |
| `includeUnavailable` | false | Also return hotels without a price for that date (`pricePerNight` null, `available` false). These rows are not charged |
| `maxHotelsPerQuery` | 50 | Hard cost cap per search and date |

## Pricing

Pay per result: **$1.99 per 1,000 hotel prices** and **$1.00 per 1,000 booking-site offers**. You pay only for rows saved to your dataset, never for errors, blocked pages or duplicates. If you set a maximum cost per run, the Actor stops cleanly when it is reached.

## Reliability

Each search is first matched to a place by Google, and every row carries that place as `resolvedPlace`. If Google does not recognise a search, it is skipped and not charged, and the run's status message lists it. Add a region to choose between places with the same name, for example "hotels in Springfield, Massachusetts".

The result list can differ from what a browser shows. Google changes its hotel list between visits: in our test on 8 October 2026, two browser visits 12 minutes apart had 87% of their hotels in common. Prices for the hotels returned matched the browser to within 10% in 99% of cases in the same test.

A restarted or migrated run resumes where it stopped and does not deliver or charge the same row twice.

## Named-hotel tracking: coming soon

Tracking specific hotels by their Google Hotels link (`hotelUrls`) is coming soon. Until then, run a city search with **Include per-site offers** on and `checkInFromDaysAhead` and `checkInToDaysAhead` set, schedule it daily, and compare `pricePerNight` per `provider` for the hotels you follow.

## Use it as an API

Every run can be started and read through the Apify API. One request runs the Actor with your input as the JSON body and returns the hotel prices as JSON, or as CSV or Excel with `format=csv` or `format=xlsx`:

`POST https://api.apify.com/v2/acts/garje~google-hotels-scraper/run-sync-get-dataset-items`

Send your Apify API token in the `Authorization` header. A worked example in Python: [Google Hotels API: hotel prices by date as JSON](https://dev.to/garje/google-hotels-api-hotel-prices-by-date-as-json-123).

<!-- sections from scripts/gtm/readme_sections.py -->
## Use it in n8n, Make or Zapier

Apify has integrations for n8n, Make and Zapier. In each, add a step that runs an Actor, choose `garje/google-hotels-scraper`, paste your input as JSON, and wait for the run to finish; the next step reads the dataset items. A schedule step in front of it turns this into a daily or weekly feed into Google Sheets, Slack or email.

## Use it from an AI agent (MCP)

Add `https://mcp.apify.com?tools=garje/google-hotels-scraper` as an MCP server in Claude, Cursor, VS Code or any other MCP client. The agent then has this Actor as one tool and runs it with your Apify account, for example: "What does a 2-night stay for 2 adults in Lisbon cost for each check-in date next month?"

<!-- sections from scripts/gtm/readme_sections.py -->

## Use cases

Each one is a ready task you can open, run and copy:

- [Hotel price calendar for one city](https://apify.com/garje/google-hotels-scraper/examples/hotel-price-calendar-one-city): one price per check-in date from 7 to 31 days ahead. The dates move with every run.
- [Hotel prices with booking-site offers](https://apify.com/garje/google-hotels-scraper/examples/hotel-prices-booking-sites): hotel rate shopping, each booking site's price for the same hotel and night, for rate parity checks.
- [Scheduled hotel price check, 14 days ahead](https://apify.com/garje/google-hotels-scraper/examples/scheduled-hotel-price-check): a daily hotel price tracker for the same stay, with no dates to update.
- [Google Hotels prices by date for 10 hotels](https://apify.com/garje/google-hotels-scraper/examples/google-hotels-api-prices-by-date): call it from your own code on a schedule and read the prices as JSON or CSV.
- [Daily hotel rate monitoring, 30 days out](https://apify.com/garje/google-hotels-scraper/examples/hotel-rate-monitoring-30-days-out): watch how a market's rates move day by day for stays a month out.
- [Google Travel hotel prices in Dubai, in AED](https://apify.com/garje/google-hotels-scraper/examples/google-travel-hotels-dubai-aed): hotel prices in the currency your market uses.
- [Hotel prices in two cities on the same night](https://apify.com/garje/google-hotels-scraper/examples/hotel-price-scraper-two-cities): hotel price comparison between two cities, in one table with the search on every row.

You can also take weekly price snapshots across several cities in one run.

<!-- sections from scripts/gtm/readme_sections.py -->
## FAQ

**Why is the hotel list different from what my browser shows?**
Google changes which hotels it lists between searches, so the list can differ from a browser. Compare the same `hotelId` from run to run.

**Why do prices change with the country?**
Google shows each country its own price for the same hotel. For the same hotels and night, country JP showed a median 10% more than country US, and country AE 19% more. Set `country` to where your customers book.

**Can I see each booking site's price?**
Yes. Turn on `includeOffers` for one row per booking site, at $1.00 per 1,000 offers.

**Does it work on Apify's free plan?**
Yes. On a free account, a run asked for 40 Tokyo hotels returned all 40.

<!-- sections from scripts/gtm/readme_sections.py -->

## Read more

I write up what the data shows, with the run ID behind every number:

- [Google Hotels API: hotel prices by date as JSON](https://dev.to/garje/google-hotels-api-hotel-prices-by-date-as-json-123)

- [Copenhagen hotel prices across 30 check-in dates](https://garje-data-notes.laude--pify.workers.dev/articles/copenhagen-hotel-prices-30-check-in-dates.html)
- [Booking-site prices for 290 Berlin hotels: how far apart they are](https://garje-data-notes.laude--pify.workers.dev/articles/berlin-hotel-booking-site-price-spread.html)
- [The cheapest night of the week in five cities](https://garje-data-notes.laude--pify.workers.dev/articles/cheapest-night-of-the-week-in-five-cities.html)
- [Facts and limits of this Actor](https://garje-data-notes.laude--pify.workers.dev/facts/google-hotels-scraper.html) and its [changelog](https://garje-data-notes.laude--pify.workers.dev/changelog/google-hotels-scraper.html)
