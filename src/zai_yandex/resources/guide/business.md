# Yandex Business, Maps promotion, business cards

What has an official API, and the tool to use.

| Need | Tool | Notes |
|---|---|---|
| Organization profiles linked to the advertiser | `direct_list_businesses` | Direct Businesses.get: Id, Name, IsPublished, Rubric. Use Id as `BusinessId` in ads. |
| Promote an organization in Maps / organization search | `direct_prepare_write` (service `campaigns`, `UnifiedCampaign`) | v501 unified campaign with `Maps` / `SearchOrganizationList`; ads carry `BusinessId`. Approval-only. |
| Business cards (vCards) | `direct_get_inventory` (service `vcards`), writes via `direct_prepare_write` | add/delete only, approval-only. Prefer `BusinessId` when a profile exists. |
| Invite a person into a Yandex ID organization (agency) | `direct_prepare_write` (service `agencyclients`, method `addPassportOrganizationMember`) | One object: PassportOrganizationLogin, Role ADMIN/USER, SendInviteTo Phone xor Email. High risk, 10/day. |
| Region ids by name | `direct_get_inventory` (service `dictionaries`) | getGeoRegions: SelectionCriteria Name / RegionIds / ExactNames, FieldNames GeoRegionId, GeoRegionName, ParentGeoRegionNames. |

No official API (tell the user, do not improvise or use browser cookies):
- Editing the organization card (hours, prices, photos) and replying to reviews: only in the Yandex Business cabinet. Chains: an XML/CSV file URL set in the cabinet.
- Reading reviews: only the public reviews widget on the site.
- Geoadv / Business promotion subscription: replaced by Direct campaigns with Maps placements.
