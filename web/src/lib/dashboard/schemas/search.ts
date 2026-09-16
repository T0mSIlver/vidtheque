// `GET /dashboard/api/search` is the public facade's search handler behind the
// read gate (dashboard.md §14.2), so it answers in the facade's shape rather
// than this surface's. The schema is the shared one; this file is where the
// dashboard names it, so no page here reaches into `lib/api`.
export { ContentType, Hit, SearchResponse } from "@/lib/schemas/search";
export { badges, type Badge } from "@/lib/schemas/evidence";
