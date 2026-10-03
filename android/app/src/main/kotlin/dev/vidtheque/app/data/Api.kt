package dev.vidtheque.app.data

import dev.vidtheque.app.auth.Instance
import java.io.IOException
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
    val reason: String = "",
    val explored: Boolean = false,
    val matches: List<Match> = emptyList(),
)

@Serializable
data class Pagination(@SerialName("has_more") val hasMore: Boolean, @SerialName("next_offset") val nextOffset: Int? = null)

@Serializable
data class Skipped(val count: Int, val capped: Boolean)

@Serializable
data class FeedPage(val items: List<FeedItem>, val pagination: Pagination, val skipped: Skipped)

@Serializable
data class VideoRow(
    @SerialName("video_id") val videoId: String,
    val title: String,
    val channel: String? = null,
    @SerialName("duration_s") val durationS: Double = 0.0,
    @SerialName("published_at") val publishedAt: Long? = null,
)

@Serializable
data class Moment(@SerialName("cue_id") val cueId: Long, @SerialName("offset_s") val offsetS: Double, val why: String, val url: String)

@Serializable
data class Verdict(
    val video: VideoRow,
    val score: Int,
    val reason: String = "",
    val explored: Boolean = false,
    val matches: List<Match> = emptyList(),
    /** The stored thumb or mute: `up`, `down`, `muted` or `none` (dashboard.md §25.4). */
    val feedback: String = "none",
    val summary: String = "",
    val moments: List<Moment> = emptyList(),
    @SerialName("moments_dropped") val momentsDropped: Int = 0,
)

@Serializable
data class ProfileEntry(
    val id: Long,
    val text: String,
    val weight: Double,
    val source: String,
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

@Serializable
data class Refusal(val error: String, val message: String)

/** The server said no, in its `{error, message, next}` envelope; [message] is fit to show. */
class ApiException(val error: String, message: String) : Exception(message)

@Singleton
class Api @Inject constructor(@Named("api") private val http: OkHttpClient, private val instance: Instance) {
    private val json = Json { ignoreUnknownKeys = true }
    private val root get() = "${instance.base}/dashboard/api"

    suspend fun feed(band: String, offset: Int): FeedPage = json.decodeFromString(get("$root/feed?band=$band&offset=$offset&limit=20"))

    suspend fun verdict(videoId: String): Verdict = json.decodeFromString(get("$root/verdicts/$videoId"))

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
                throw ApiException(refusal?.error ?: "http_${it.code}", refusal?.message ?: "The server answered HTTP ${it.code}.")
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
