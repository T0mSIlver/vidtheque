package dev.vidtheque.app.data

import dev.vidtheque.app.auth.Instance
import java.io.IOException
import java.net.URLEncoder
import javax.inject.Inject
import javax.inject.Named
import javax.inject.Singleton
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response

// The shapes of dashboard.md §25, read as the server writes them.

/**
 * A profile entry the verdict matched: `up` an entry you want more of, `down` one you
 * want less of; strength 1 the video touches it, 2 it is central. At most 4, strongest first.
 */
@Serializable
data class Match(@SerialName("entry_id") val entryId: Long, val text: String, val direction: String, val strength: Int = 1)

@Serializable
data class FeedItem(
    @SerialName("video_id") val videoId: String,
    val title: String,
    val channel: String? = null,
    @SerialName("duration_s") val durationS: Double = 0.0,
    @SerialName("published_at") val publishedAt: Long? = null,
    val score: Int,
    /** What the feed shows: the week's ranking makes the 3s (companion.md §3.4). */
    val tier: Int? = null,
    val reason: String = "",
    val explored: Boolean = false,
    val matches: List<Match> = emptyList(),
    /** Seconds the moments cover; null for moments written before spans (companion.md §3.1). */
    @SerialName("moments_s") val momentsS: Double? = null,
)

@Serializable
data class Pagination(@SerialName("has_more") val hasMore: Boolean, @SerialName("next_offset") val nextOffset: Int? = null)

@Serializable
data class Skipped(val count: Int, val capped: Boolean)

@Serializable
data class FeedPage(val items: List<FeedItem>, val pagination: Pagination, val skipped: Skipped)

/**
 * What narrows the feed (dashboard.md §25.2): [q] a substring of the title or channel,
 * [channel] one channel whole, [entry] a profile entry id or `other`, [oldest] the order.
 */
data class FeedFilters(val q: String = "", val channel: String? = null, val entry: String? = null, val oldest: Boolean = false) {
    val narrowed get() = q.isNotBlank() || channel != null || entry != null
}

@Serializable
data class ChannelFacet(val name: String, val count: Int)

@Serializable
data class EntryFacet(@SerialName("entry_id") val entryId: Long, val text: String, val direction: String, val count: Int)

/** The channels and live profile entries the feed's filters offer, with their counts. */
@Serializable
data class FeedFacets(val channels: List<ChannelFacet> = emptyList(), val entries: List<EntryFacet> = emptyList(), val other: Int = 0)

@Serializable
data class VideoRow(
    @SerialName("video_id") val videoId: String,
    val title: String,
    val channel: String? = null,
    @SerialName("duration_s") val durationS: Double = 0.0,
    @SerialName("published_at") val publishedAt: Long? = null,
)

@Serializable
data class Moment(
    @SerialName("cue_id") val cueId: Long,
    @SerialName("offset_s") val offsetS: Double,
    val why: String,
    val url: String,
    @SerialName("end_s") val endS: Double? = null,
)

@Serializable
data class Verdict(
    val video: VideoRow,
    val score: Int,
    val tier: Int? = null,
    val reason: String = "",
    val explored: Boolean = false,
    val matches: List<Match> = emptyList(),
    /** The stored thumb or mute: `up`, `down`, `muted` or `none` (dashboard.md §25.4). */
    val feedback: String = "none",
    val summary: String = "",
    val moments: List<Moment> = emptyList(),
    @SerialName("moments_dropped") val momentsDropped: Int = 0,
    @SerialName("moments_s") val momentsS: Double? = null,
)

@Serializable
data class ProfileEntry(
    val id: Long,
    val text: String,
    val weight: Double,
    val source: String,
    val kind: String = "topic",
    // A project lapses here unless written again (#159).
    @SerialName("expires_at") val expiresAt: Long? = null,
    @SerialName("created_at") val createdAt: Long? = null,
    val evidence: String? = null,
)

/** One side of a profile event; absent fields did not change. */
@Serializable
data class EntryState(val text: String? = null, val weight: Double? = null, val live: Boolean? = null)

@Serializable
data class ProfileEvent(
    val id: Long,
    val at: Long,
    val actor: String,
    val op: String,
    @SerialName("entry_id") val entryId: Long,
    val before: EntryState? = null,
    val after: EntryState? = null,
    val reason: String? = null,
)

@Serializable
data class History(val events: List<ProfileEvent>, @SerialName("has_more") val hasMore: Boolean, @SerialName("next_before") val nextBefore: Long? = null)

@Serializable
data class Profile(
    val revision: Long,
    @SerialName("max_entries") val maxEntries: Int,
    val entries: List<ProfileEntry>,
    val history: History,
)

/** One hit of `GET /dashboard/api/search`, the MCP `search` tool's (dashboard.md §25.9). */
@Serializable
data class SearchHit(
    @SerialName("video_id") val videoId: String,
    val title: String = "",
    val channel: String? = null,
    val source: String = "",
    val start: Double = 0.0,
    /** The second that matched, inside the segment; `link` points there. */
    @SerialName("match_start") val matchStart: Double? = null,
    /** `null` on a frame that matched on imagery alone. */
    val text: String? = null,
    val link: String = "",
    @SerialName("published_at") val publishedAt: Long? = null,
)

@Serializable
data class SearchPagination(val limit: Int, val offset: Int, @SerialName("has_more") val hasMore: Boolean)

@Serializable
data class SearchPage(
    val results: List<SearchHit> = emptyList(),
    val pagination: SearchPagination,
    val notes: List<String> = emptyList(),
    /** Set on an empty page only: "nothing is indexed" rather than "nothing matched". */
    @SerialName("data_status") val dataStatus: String? = null,
)

/** What `E_NO_VERDICT` names beside its envelope (dashboard.md §25.3). */
@Serializable
data class NoVerdict(val video: VideoRow)

// The weekly brief (dashboard.md §26).

@Serializable
data class Pick(
    @SerialName("video_id") val videoId: String,
    val title: String,
    val channel: String? = null,
    @SerialName("duration_s") val durationS: Double = 0.0,
    val score: Int,
    val reason: String = "",
    val moments: List<Moment> = emptyList(),
)

/** One place a speaker said it: the cue, and its link. */
@Serializable
data class Receipt(
    @SerialName("video_id") val videoId: String,
    val title: String = "",
    val channel: String = "",
    @SerialName("offset_s") val offsetS: Double,
    val url: String? = null,
    val said: String? = null,
)

@Serializable
data class Disagreement(val about: String, val sides: List<Receipt>)

@Serializable
data class Said(@SerialName("entry_id") val entryId: Long, val text: String, val points: List<Receipt> = emptyList(), val disagreement: Disagreement? = null)

@Serializable
data class ChannelCard(
    val slug: String,
    val title: String,
    val state: String,
    val videos: Int,
    @SerialName("worth_share") val worthShare: Double? = null,
    @SerialName("engaged_share") val engagedShare: Double? = null,
    @SerialName("suggest_pause") val suggestPause: Boolean = false,
)

@Serializable
data class Change(
    @SerialName("event_id") val eventId: Long,
    val op: String,
    @SerialName("entry_id") val entryId: Long,
    val before: EntryState? = null,
    val after: EntryState? = null,
    val reason: String? = null,
    val reverted: Boolean = false,
)

@Serializable
data class Audited(
    @SerialName("video_id") val videoId: String,
    val title: String,
    val channel: String? = null,
    val reason: String = "",
    @SerialName("sunk_by") val sunkBy: Match? = null,
    /** `wrong` is "I'd have watched it", `right` the skip was fair, null not asked yet. */
    val answer: String? = null,
)

@Serializable
data class Checkin(val rating: Int, val missing: String? = null)

@Serializable
data class Brief(
    val week: String,
    val since: Long,
    @SerialName("previous_week") val previousWeek: String? = null,
    val picks: List<Pick> = emptyList(),
    val said: List<Said> = emptyList(),
    @SerialName("said_note") val saidNote: String? = null,
    val channels: List<ChannelCard> = emptyList(),
    @SerialName("profile_changes") val profileChanges: List<Change> = emptyList(),
    val audit: List<Audited> = emptyList(),
    val checkin: Checkin? = null,
    /** This week of the valued-time ledger (§25.12); null past the weeks it reads. */
    val ledger: ValuedTime? = null,
)

/** "Ease the entry that sank it to [to]?" — applied only when the reader says so. */
@Serializable
data class Proposal(@SerialName("entry_id") val entryId: Long, val text: String, val weight: Double, val to: Double)

@Serializable
data class SkipAnswered(val answer: String, val proposal: Proposal? = null)

@Serializable
data class Refusal(val error: String, val message: String)

/** The server said no, in its `{error, message, next}` envelope; [message] is fit to show, [body] is the whole answer. */
class ApiException(val error: String, message: String, val body: String = "") : Exception(message)

@Singleton
class Api @Inject constructor(@Named("api") private val http: OkHttpClient, private val instance: Instance) {
    private val json = Json { ignoreUnknownKeys = true }
    private val root get() = "${instance.base}/dashboard/api"

    suspend fun feed(band: String, offset: Int, filters: FeedFilters = FeedFilters()): FeedPage {
        val url = "$root/feed".toHttpUrl().newBuilder()
            .addQueryParameter("band", band)
            .addQueryParameter("offset", "$offset")
            .addQueryParameter("limit", "20")
        filters.q.trim().takeIf { it.isNotEmpty() }?.let { url.addQueryParameter("q", it) }
        filters.channel?.let { url.addQueryParameter("channel", it) }
        filters.entry?.let { url.addQueryParameter("entry", it) }
        if (filters.oldest) url.addQueryParameter("order", "oldest")
        return json.decodeFromString(get(url.build().toString()))
    }

    suspend fun facets(): FeedFacets = json.decodeFromString(get("$root/feed/facets?band=top"))

    suspend fun verdict(videoId: String): Verdict = json.decodeFromString(get("$root/verdicts/$videoId"))

    /** Every channel, no filter: the search the MCP tool runs, relevance first. The server cuts each snippet around its match. */
    suspend fun search(query: String, offset: Int): SearchPage =
        json.decodeFromString(get("$root/search?q=${URLEncoder.encode(query, "UTF-8")}&offset=$offset&max_text_chars=300"))

    /** A query submitted on the search screen, as the tool logs one (companion.md §2.3). */
    suspend fun searched(query: String) {
        post("$root/signals", buildJsonObject {
            put("kind", "mcp_search")
            put("text", query)
        })
    }

    /** The video an `E_NO_VERDICT` refusal names, or null. */
    fun unjudged(e: ApiException): VideoRow? =
        if (e.error != "E_NO_VERDICT") null else runCatching { json.decodeFromString<NoVerdict>(e.body).video }.getOrNull()

    suspend fun costs(): Costs = json.decodeFromString(get("$root/costs"))

    suspend fun profile(before: Long? = null): Profile =
        json.decodeFromString(get("$root/profile" + (before?.let { "?before=$it" } ?: "")))

    suspend fun drop(entryId: Long): Profile = json.decodeFromString(
        post("$root/profile", buildJsonObject {
            put("drop", kotlinx.serialization.json.JsonArray(listOf(kotlinx.serialization.json.JsonPrimitive(entryId))))
            put("reason", "dropped in the app")
        }),
    )

    suspend fun revert(eventId: Long): Profile =
        json.decodeFromString(post("$root/profile/revert", buildJsonObject { put("event_id", eventId) }))

    suspend fun brief(week: String? = null): Brief = json.decodeFromString(get("$root/brief" + (week?.let { "?week=$it" } ?: "")))

    suspend fun checkin(week: String, rating: Int, missing: String) {
        post("$root/brief/checkin", buildJsonObject {
            put("week", week)
            put("rating", rating)
            put("missing", missing)
        })
    }

    /** `wrong` is "I'd watch this": a thumb up, and a proposed reweight in the answer. */
    suspend fun skip(videoId: String, answer: String, source: String): SkipAnswered = json.decodeFromString(
        post("$root/skips", buildJsonObject {
            put("video_id", videoId)
            put("answer", answer)
            put("source", source)
        }),
    )

    suspend fun reweight(entryId: Long, weight: Double, reason: String): Profile = json.decodeFromString(
        post("$root/profile", buildJsonObject {
            put("reweight", kotlinx.serialization.json.JsonArray(listOf(buildJsonObject {
                put("id", entryId)
                put("weight", weight)
            })))
            put("reason", reason)
        }),
    )

    /** The console's own pause (dashboard.md §21), a form post like the page's. */
    suspend fun pauseFollow(slug: String) {
        val form = okhttp3.FormBody.Builder().add("action", "pause").build()
        send(Request.Builder().url("${instance.base}/dashboard/following/$slug/state").header("Accept", "application/json").post(form).build())
    }

    /** This phone's FCM token, so verdicts at the threshold reach it (§25.6). */
    suspend fun registerDevice(token: String) {
        post("$root/devices", buildJsonObject { put("token", token) })
    }

    suspend fun forgetDevice(token: String) {
        send(Request.Builder().url("$root/devices").delete(buildJsonObject { put("token", token) }.toString().toRequestBody(JSON)).build())
    }

    /** Sets the video's one thumb-or-mute state; `none` takes it back. */
    suspend fun feedback(videoId: String, state: String) {
        post("$root/feedback", buildJsonObject {
            put("video_id", videoId)
            put("state", state)
        })
    }

    /** A `watch` hand-off; its id closes it once the app is back (§25.10). */
    suspend fun watch(videoId: String, offsetS: Int): Long? = json.decodeFromString<Recorded>(
        post("$root/signals", buildJsonObject {
            put("kind", "watch")
            put("video_id", videoId)
            put("offset_s", offsetS)
        }),
    ).signalId

    suspend fun watched(signalId: Long, seconds: Double) {
        post("$root/watched", buildJsonObject {
            put("signal_id", signalId)
            put("watched_s", seconds)
        })
    }

    /** A YouTube link shared to the app: indexed, and counted as a miss if the feed did not offer it (§25.11). */
    suspend fun share(url: String): Shared = json.decodeFromString(post("$root/shares", buildJsonObject { put("url", url) }))

    suspend fun valuedTime(): ValuedTime = json.decodeFromString(get("$root/valued-time"))

    /** Fire and forget from the caller's point of view: a lost signal costs one data point. */
    suspend fun signal(kind: String, videoId: String, offsetS: Int? = null) {
        post("$root/signals", buildJsonObject {
            put("kind", kind)
            put("video_id", videoId)
            if (offsetS != null) put("offset_s", offsetS)
        })
    }

    internal suspend fun get(url: String): String = send(Request.Builder().url(url).get().build())

    internal suspend fun post(url: String, body: JsonObject): String =
        send(Request.Builder().url(url).post(body.toString().toRequestBody(JSON)).build())

    private suspend fun send(request: Request): String {
        val response = call(request)
        return withContext(Dispatchers.IO) {
            response.use {
                val text = it.body.string()
                if (it.isSuccessful) return@use text
                val refusal = runCatching { json.decodeFromString<Refusal>(text) }.getOrNull()
                throw ApiException(refusal?.error ?: "http_${it.code}", refusal?.message ?: "The server answered HTTP ${it.code}.", text)
            }
        }
    }

    // Enqueued rather than executed, so cancelling the coroutine (a timeout, a screen
    // that left) cancels the request instead of waiting out OkHttp's own timeouts.
    private suspend fun call(request: Request): Response = suspendCancellableCoroutine { cont ->
        val call = http.newCall(request)
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) = cont.resumeWithException(e)
            override fun onResponse(call: Call, response: Response) = cont.resume(response) { _, value, _ -> value.close() }
        })
    }

    private companion object {
        val JSON = "application/json".toMediaType()
    }
}
