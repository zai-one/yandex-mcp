# Direct account audit checklist

1. Campaign states: ON/SUSPENDED/ENDED, moderation, daily budget, end dates.
2. Spend vs conversions per campaign (Reports CAMPAIGN_PERFORMANCE_REPORT, Cost, Clicks, Conversions, CostPerConversion) for the last 30 days vs previous 30.
3. Goals: are the campaign's PriorityGoals the Metrika goals that matter? Goal statistics with the right AttributionModels (`direct_get_goal_statistics`).
4. Strategies: automatic strategy vs. goal and enough conversions (rule of thumb: >= 10 conversions/week for conversion-optimised strategies); manual bids on dead campaigns.
5. Search queries (SEARCH_QUERY_PERFORMANCE_REPORT): spend on irrelevant queries -> negative keyword candidates.
6. Ad groups/keywords with impressions but no clicks (CTR outliers), keywords with zero impressions.
7. Ads: rejected / missing sitelinks, callouts, images; responsive ads present.
8. Geography and devices: Cost per conversion by Device and LocationOfPresenceName.
9. Report findings as: issue, evidence (numbers, period), estimated impact, suggested change. Changes go through skill direct-safe-change.
