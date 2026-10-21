# Google Ads Transparency Scraper: Ads and Creatives

Scrape and export ads from the Google Ads Transparency Center, Google's public ads library, by website domain, advertiser ID or advertiser name. Use it as a Google Ads Transparency Center scraper or API to see competitor Google ads, including YouTube video ads, and to download ad creatives. You get the ad content itself where Google provides it. I built it on the same public endpoints the Transparency Center website uses, with plain HTTP requests and no login.

## At a glance

| | |
|---|---|
| Use it when | You need the ads a competitor's website or advertiser runs on Google, from the Google Ads Transparency Center: Google ads by domain, a Google ads library export, or the job people often look for an ad spy tool to do. |
| Input you need | At least one of `domains`, `advertiserIds` or `searchTerms` (advertiser names); optionally `region`, `dateFrom`, `dateTo`, `onlyNewSince` and exact limits. |
| What you get | One row per ad with advertiser, format, first and last shown dates, image and video links, and a `contentStatus`. |
| Cost | $1.50 per 1,000 ads; $3 per 1,000 downloaded images. |
| Limits | Most text ads come back as images, not separate words; the Actor does not read text out of images. |

I built this Actor and test it against what the live site shows. The check runs again every week.

## What you get

One row per ad (creative):

| Field | Example |
|---|---|
| `advertiserId`, `advertiserName` | `AR16735076323512287233`, `Nike, Inc.` |
| `creativeId`, `adUrl` | `CR00485485175946346497`, link to the ad in the Transparency Center |
| `format` | `text`, `image` or `video`, as the Transparency Center classifies it |
| `firstShown`, `lastShown`, `daysShown` | ISO 8601 UTC timestamps, for example `2026-07-16T14:20:10Z` |
| `firstShownDate`, `lastShownDate` | The calendar dates exactly as the Transparency Center displays them (US Pacific time), for example `2026-07-16` |
| `targetDomain`, `region` | `nike.com`, `US` |
| `searchTerm`, `matchScore` | `nike`, `1.0` | For ads found by an advertiser name: the name and how well the advertiser matched it |
| `platform` | `youtube` (the platform filter that found the ad, when you select specific platforms) |
| `headline`, `description`, `texts` | The words shown in the ad |
| `displayUrl` | `www.nike.com/` |
| `imageUrl`, `imageUrls` | The ad images (most text ads are published as one rendered image) |
| `videoIds`, `videoUrls` | YouTube links for video ads |
| `contentStatus` | `ok`, `imageOnly`, `externallyHosted`, `notAvailable`, `previewFailed` or `none`, so you always know what content came back |
| `assetKeys` | Keys of saved images in the run's storage, when **Download images** is on |

## What content to expect

**Most text ads come back as images.** Google publishes most text ads in the Transparency Center as one rendered image, not as separate words. For those ads the Actor returns the image URL and sets `contentStatus` to `imageOnly`. In a measured run of 4,578 ads on 7 October 2026:

| contentStatus | Share of ads |
|---|---|
| `imageOnly` | 79.6% |
| `ok` (text, images or video links read from the preview) | 14.3% |
| `notAvailable` (a preview type the Actor does not read yet) | 6.1% |
| `externallyHosted` (served by an outside ad server; preview link returned) | under 0.1% |

Of the 2,958 text ads in that run, 2,866 (96.9%) came back as images. Headline or ad text came back for about 7% of all ads (303 of 4,578), and image URLs for 83%. The Actor does not read text out of images.

## Dates by region

`firstShown`, `lastShown` and `daysShown` came back for every ad in the regions we measured (US and `anywhere`, 8 October 2026). The Transparency Center
also shows, for each region separately, a first-shown date and impression ranges, but only for EU countries; outside
the EU it shows a per-region last-shown date only (checked on 8 October 2026). This Actor does not return those
per-region figures.

<!-- sections from scripts/gtm/readme_sections.py -->
## Sample output

One real row from a run on 11 Oct 2026 (selected fields):

```json
{
  "advertiserName": "Nike, Inc.",
  "creativeId": "CR02029141914422345729",
  "format": "video",
  "firstShownDate": "2026-07-07",
  "lastShownDate": "2026-10-10",
  "daysShown": 80,
  "targetDomain": "nike.com",
  "region": "US",
  "headline": "Nike House of Innovation NYC",
  "description": "Start Your Season Right Gear up for your next run with the la...",
  "adUrl": "https://adstransparency.google.com/advertiser/AR16735076323512287233/creative/CR02029141914422345729?region=US"
}
```

<!-- sections from scripts/gtm/readme_sections.py -->

## What this Actor handles for you

1. **Ad content, not only metadata.** With **Include creative content** on (the default), each ad's preview is read and the headline, text, images, video links and display URL are returned in plain fields.
2. **No silent gaps.** Content is read from the rendered preview Google itself shows. When an ad's content is a single image, the image URL is returned and `contentStatus` says `imageOnly`, so nothing is silently null.
3. **Exact limits.** `maxAdsPerAdvertiser` is enforced exactly per advertiser, and `maxAdsPerDomain` exactly per domain, including across several domains in one run.
4. **Checked inputs and retried pages.** Inputs are checked before any request, with a clear message. Rate-limit pages are detected and retried on a new IP rather than saved as empty data, and a failed page is reported in `RUN_STATS` while the run continues with the rest.
5. **Several domains, ISO dates and the site's own formats.** Give several domains in one run; each domain has its own limit. Every date is ISO 8601. `format` is the value the Transparency Center's own format filter uses.

## Input

| Field | Default | What it does |
|---|---|---|
| `domains` | `[]` | Website domains, several per run |
| `advertiserIds` | `[]` | Direct AR... IDs |
| `searchTerms` | `[]` | Advertiser names. Every advertiser Google suggests is scored and saved in the `SEARCH_MATCHES` record |
| `minMatchScore` | 1 | Score needed to scrape a suggested advertiser; 1 means the name matches exactly, ignoring legal forms such as Inc. or GmbH |
| `maxAdvertisersPerTerm` | 3 | Best-scoring advertisers scraped per name, the one with the most ads first |
| `region` | `anywhere` | Two-letter country code or `anywhere` |
| `dateFrom`, `dateTo` | last 30 days | Shown window |
| `formats` | all | `text`, `image`, `video` |
| `platforms` | all | `search`, `youtube`, `shopping`, `maps`, `play`, using the Transparency Center's own platform filter |
| `includeCreativeContent` | true | Headline, text, image and video URLs, display URL |
| `downloadAssets` | false | Saves up to 3 images per ad to storage, as a separate paid event |
| `maxAdsPerAdvertiser` | 100 | Enforced exactly; 0 means no limit |
| `maxAdsPerDomain` | 200 | Enforced exactly per domain, across all advertisers of that domain; 0 means no limit |
| `onlyNewSince` | | Monitoring mode: only ads first shown after this time |

## Pricing

Pay per result: **$1.50 per 1,000 ads** and **$3 per 1,000 downloaded images**. You pay only for rows and files actually saved, never for errors, retries or duplicates. If you set a maximum cost per run, the Actor stops cleanly when it is reached.

## Monitoring competitors

Schedule a daily run for your competitors' domains with `onlyNewSince` set to the previous run's time, so each run returns only ads first shown after it.

## Reliability

A restarted or migrated run resumes where it stopped and does not deliver or charge the same ad twice.

## Use it as an API

Every run can be started and read through the Apify API. One request runs the Actor with your input as the JSON body and returns the ads as JSON, or as CSV or Excel with `format=csv` or `format=xlsx`:

`POST https://api.apify.com/v2/acts/garje~google-ads-transparency-scraper/run-sync-get-dataset-items?format=csv`

Send your Apify API token in the `Authorization` header. A worked example in Python: [Google Ads Transparency Center API: export a competitor's Google ads](https://dev.to/garje/google-ads-transparency-center-api-export-a-competitors-google-ads-4fag).

<!-- sections from scripts/gtm/readme_sections.py -->
## Use it in n8n, Make or Zapier

Apify has integrations for n8n, Make and Zapier. In each, add a step that runs an Actor, choose `garje/google-ads-transparency-scraper`, paste your input as JSON, and wait for the run to finish; the next step reads the dataset items. A schedule step in front of it turns this into a daily or weekly feed into Google Sheets, Slack or email.

## Use it from an AI agent (MCP)

Add `https://mcp.apify.com?tools=garje/google-ads-transparency-scraper` as an MCP server in Claude, Cursor, VS Code or any other MCP client. The agent then has this Actor as one tool and runs it with your Apify account, for example: "Which new Google ads did allbirds.com start showing in the US this week?"

<!-- sections from scripts/gtm/readme_sections.py -->

## Use cases

Each one is a ready task you can open, run and copy:

- [All Google ads for a company domain](https://apify.com/garje/google-ads-transparency-scraper/examples/google-ads-by-domain): every advertiser of one website, with format and first and last shown dates.
- [Google ads with image and video links](https://apify.com/garje/google-ads-transparency-scraper/examples/google-ads-image-video-links): download Google ad creatives, with image URLs and YouTube links.
- [Weekly monitor of new Google ads](https://apify.com/garje/google-ads-transparency-scraper/examples/weekly-new-google-ads): only ads first shown after a date. Schedule it weekly and move the date forward.
- [Google ads library export by advertiser name](https://apify.com/garje/google-ads-transparency-scraper/examples/google-ads-library-by-advertiser-name): look up a company's ads when you know its name but not its advertiser ID.
- [YouTube video ads for a brand](https://apify.com/garje/google-ads-transparency-scraper/examples/youtube-video-ads-for-a-brand): a brand's video ads, each with its YouTube link and first and last shown dates.
- [Google ads of three competing brands in one table](https://apify.com/garje/google-ads-transparency-scraper/examples/competitor-google-ads-three-brands): competitor Google ads side by side, with the domain on every row.
- [Google ads by Transparency Center advertiser ID](https://apify.com/garje/google-ads-transparency-scraper/examples/google-ads-transparency-api-by-advertiser-id): one exact advertiser's ads, for calls from your own code.

You can also find long-running ads by sorting on `daysShown`, or look up a brand by name: every candidate advertiser is scored in `SEARCH_MATCHES`.

<!-- sections from scripts/gtm/readme_sections.py -->
## FAQ

**Why do most text ads come back as images?**
Google publishes most text ads in the Ads Transparency Center as images: 2,866 of 2,958 text ads (96.9%) in one measured run. Where Google shows the words, rows carry the headline and description too.

**Does it return video ads?**
Yes. Video ads come with their video links where the Transparency Center gives them.

**Can I get only the ads that are new since my last run?**
Yes. Set `onlyNewSince` to the date of your last run and schedule it weekly.

**Does it work on Apify's free plan?**
Yes. On a free account, a run asked for 50 ads returned all 50.

<!-- sections from scripts/gtm/readme_sections.py -->

## Read more

I write up what the data shows, with the run ID behind every number:

- [Google Ads Transparency Center API: export a competitor's Google ads](https://dev.to/garje/google-ads-transparency-center-api-export-a-competitors-google-ads-4fag)

- [How long Google ads run: median days shown by format](https://garje-data-notes.laude--pify.workers.dev/articles/how-long-google-ads-run.html)
- [New Google ads in one week for 10 well-known brands](https://garje-data-notes.laude--pify.workers.dev/articles/new-google-ads-in-one-week.html)
- [Most text ads in the Transparency Center come back as images](https://garje-data-notes.laude--pify.workers.dev/articles/text-ads-come-back-as-images.html)
- [Facts and limits of this Actor](https://garje-data-notes.laude--pify.workers.dev/facts/google-ads-transparency-scraper.html) and its [changelog](https://garje-data-notes.laude--pify.workers.dev/changelog/google-ads-transparency-scraper.html)
