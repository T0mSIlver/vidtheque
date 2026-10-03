package dev.vidtheque.app.data

import android.net.Uri

// The same words the web feed hands Claude (web/src/lib/feed/words.ts), so either
// surface asks the same question (companion.md §2.2, §6).

/** Names the corpus and the video, and leaves the question to you. */
fun videoPrompt(video: VideoRow): String {
    val by = video.channel?.let { " by $it" }.orEmpty()
    return "I have a question about a video in my vidtheque corpus: \"${video.title}\"$by, " +
        "video id ${video.videoId}. Read it with the vidtheque tools (video-summary first, " +
        "then get-segment-context where you need the detail) and cite the youtu.be links " +
        "with their timestamps.\n\nMy question: "
}

/** "Ask Claude to build my profile" (§2.2), word for word the web's. */
const val PROFILE_PROMPT =
    "Help me build my interest profile in vidtheque. It is a short list of interests in " +
        "plain words, each with a weight from -1 to 1 (negative means less of this), and it " +
        "decides which new videos from the channels I follow are worth my time. Start from " +
        "what you already know about me and my work. If that is not enough, interview me " +
        "with a few short questions first. Show me the list, then save it with the vidtheque " +
        "profile tool: call it bare to see what is there, add what is missing, and do not " +
        "drop entries I wrote."

/** `+0.9`, `−0.8`, `0.0`: a weight always prints its sign. */
fun signed(weight: Double): String {
    val fixed = "%.1f".format(kotlin.math.abs(weight))
    if (fixed == "0.0") return fixed
    return (if (weight < 0) "−" else "+") + fixed
}

fun claudeUri(prompt: String): Uri = Uri.parse("https://claude.ai/new?q=${Uri.encode(prompt)}")
