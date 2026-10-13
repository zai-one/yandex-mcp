# Direct bidding strategies (Unified Performance Campaign / Text)

Strategy types from https://yandex.ru/dev/direct/doc/ru/campaigns/update-text-campaign :
- `HIGHEST_POSITION` - manual bids; you control bids, needs constant care. `SERVING_OFF` - no impressions on that network.
- `WB_MAXIMUM_CLICKS` (max clicks with weekly budget) - traffic, not conversions.
- `AVERAGE_CPC` - target average cost per click.
- `WB_MAXIMUM_CONVERSION_RATE` (max conversions with weekly budget) - needs goal data.
- `WEEKLY_CLICK_PACKAGE` - fixed number of clicks per week.
- `AVERAGE_CPA` / `AVERAGE_CPA_MULTIPLE_GOALS` / `PAY_FOR_CONVERSION` - target or pay-per-conversion CPA; requires Metrika goals with enough conversions.
- `AVERAGE_CRR` / `PAY_FOR_CONVERSION_CRR` - target cost/revenue share; needs revenue (ecommerce or goal value).
Read the actual strategy and settings with `direct_get_campaign` / `direct_list_strategies`; type-specific names differ (TextCampaign vs UnifiedCampaign) - see https://yandex.ru/dev/direct/doc/ru/strategies/get.
Checks: conversion strategy with < ~10 conversions/week learns slowly; CPA target far below the real CPA throttles impressions; weekly budget below 7x daily spend caps delivery.
