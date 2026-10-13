# Direct API units (баллы)

Source: https://yandex.ru/dev/direct/doc/ru/concepts/units
- Every Direct API response carries `Units: spent/remaining/daily_limit` (e.g. `10/20828/64000`) and `Units-Used-Login`.
- The daily limit depends on account activity and is released in 24 hourly slices (rolling window).
- An error in a method call costs 20 units; each failed create/update operation costs 20 units.
- Typical get costs per call + per object: Campaigns.get 10+1, AdGroups.get 15+1, Ads.get 15+1, Keywords.get 15 + 1 per 2000 phrases (3 if Productivity/Statistics fields are requested), Dictionaries.get 1.
- Writes: Ads.add/update 20+20, Keywords.add 20+2, Campaigns.update 10+3, Campaigns.suspend/resume 10+5.
- Agencies: with `Use-Operator-Units: true` units are charged to the agency, otherwise to the client.
- Max 5 concurrent API requests per advertiser. Error 152 (not enough units) and 506 (too many connections) come back as `provider_rate_limited`: wait, then retry.
- The Reports service is not in the units table; it is limited by its own report queue (offline reports can return 201/202 = retry later).
Economy: request only needed FieldNames, filter with SelectionCriteria, prefer one Reports call over many get calls.
