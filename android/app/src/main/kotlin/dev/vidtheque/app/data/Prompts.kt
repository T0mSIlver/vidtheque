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

fun claudeUri(prompt: String): Uri = Uri.parse("https://claude.ai/new?q=${Uri.encode(prompt)}")
