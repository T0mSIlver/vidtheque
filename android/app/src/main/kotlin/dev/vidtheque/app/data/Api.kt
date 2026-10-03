package dev.vidtheque.app.data

import dev.vidtheque.app.auth.Instance
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import javax.inject.Inject
import javax.inject.Named
import javax.inject.Singleton

// The shapes of dashboard.md §25, read as the server writes them.

@Serializable
data class FeedItem(
    @SerialName("video_id") val videoId: String,
    val title: String,
    val channel: String? = null,
    @SerialName("duration_s") val durationS: Double = 0.0,
    val score: Int,
    val reason: String = "",
    val explored: Boolean = false,
)

@Serializable
data class Pagination(@SerialName("has_more") val hasMore: Boolean, @SerialName("next_offset") val nextOffset: Int? = null)

@Serializable
data class Skipped(val count: Int, val capped: Boolean)

@Serializable
data class FeedPage(val items: List<FeedItem>, val pagination: Pagination, val skipped: Skipped)

@Serializable
data class Refusal(val error: String, val message: String)

/** The server said no, in its `{error, message, next}` envelope; [message] is fit to show. */
class ApiException(val error: String, message: String) : Exception(message)

@Singleton
class Api @Inject constructor(@Named("api") private val http: OkHttpClient, private val instance: Instance) {
    private val json = Json { ignoreUnknownKeys = true }
    private val root get() = "${instance.base}/dashboard/api"

    suspend fun feed(band: String, offset: Int): FeedPage = json.decodeFromString(get("$root/feed?band=$band&offset=$offset&limit=20"))

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

    private suspend fun send(request: Request): String = withContext(Dispatchers.IO) {
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (response.isSuccessful) return@use text
            val refusal = runCatching { json.decodeFromString<Refusal>(text) }.getOrNull()
            throw ApiException(refusal?.error ?: "http_${response.code}", refusal?.message ?: "The server answered HTTP ${response.code}.")
        }
    }

    private companion object {
        val JSON = "application/json".toMediaType()
    }
}
