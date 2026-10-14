# Yandex Maps organization search (Geosearch API)

Opt-in toolset `maps_geosearch`, read-only. The tools exist only when the operator configures a
Geosearch API key (Developer Cabinet, "API Поиска по организациям"): `YANDEX_MAPS_GEOSEARCH_API_KEY_FILE`
(owner-private file, preferred) or `YANDEX_MAPS_GEOSEARCH_API_KEY`, in the client env or the operator
config. Never ask for the key in chat. The key itself is the opt-in: with it the tools join any
`YANDEX_TOOLSETS` selection (hide with `YANDEX_DISABLED_TOOLS`); without it they never appear.

| Need | Tool | Notes |
|---|---|---|
| Companies by name, category, phone, address | `maps_search_organizations(text, ...)` | type=biz; name, address, categories, hours, phones, url, coordinates, uri |
| A place, street or city; area for a later search | `maps_search_places(text, ...)` | type=geo; kind, precision, full address, coordinates |
| Same organization again | `maps_search_organizations(uri="ymapsbm1://...")` | `uri` from an earlier result |

Area: `center=[lon, lat]` + `span=[lon_span, lat_span]` (degrees) or `bbox=[[lon,lat],[lon,lat]]`
(lower-left, upper-right; wins over center+span). `strict_area=true` (rspn=1) stops results outside
it. Coordinates are always longitude first. `results` 1-50 (default 10); `skip` <= 1000 and divisible by
`results`. `lang`: ru_RU (default), uk_UA, be_BY, en_RU, en_US, tr_TR. `detail="full"` returns the raw
features. The API returns the most relevant organizations, not every one in the area.

Terms (https://yandex.ru/legal/maps_api/) and how the tools follow them:
- No storing or editing results beyond temporary caching: the server caches and persists nothing.
- Do not change order or hide attributions: results keep API order and carry `attribution`; keep it
  when you show results, and do not re-rank or merge them into a stored database.
- Every call counts against the key's daily license quota; the API allows up to 50 RPS, the server
  paces calls to at most 5 per second. HTTP 403 = key invalid or not activated (new keys take ~15 min).
Request/response reference: https://yandex.ru/maps-api/docs/geosearch-api/request.html,
https://yandex.ru/maps-api/docs/geosearch-api/response.html.
