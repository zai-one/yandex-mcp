# Metrika Reporting API cheat sheet

Docs: https://yandex.ru/dev/metrika/ru/stat/
Metrics (visits, prefix `ym:s:`): `visits`, `users`, `pageviews`, `bounceRate`, `pageDepth`, `avgVisitDurationSeconds`, `newUsers`, `goal<ID>reaches`, `goal<ID>conversionRate`, `ecommerceRevenue`.
Dimensions: `trafficSource`, `lastTrafficSource`, `lastsignTrafficSource`, `searchEngine`, `UTMSource`, `UTMCampaign`, `startURL`, `deviceCategory`, `regionCity`, `date`.
Filters: `ym:s:trafficSource=='organic'`, `ym:s:deviceCategory=='mobile'`, combine with `AND`/`OR`.
Rules: up to 12 metrics, 5 dimensions per `metrika_report` call; periods <= 93 days; compare equal-length periods (week vs previous week, month vs same month last year).
Always report `sampled` / `sample_share`; sampled data is an estimate. For exact row-level data use the Logs API (not exposed yet).
Attribution: `lastsign` (last significant) is Metrika's default for goals; say which one you used.
