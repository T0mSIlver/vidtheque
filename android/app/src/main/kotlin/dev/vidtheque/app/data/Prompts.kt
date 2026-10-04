package dev.vidtheque.app.data

import android.net.Uri

// The same words the web feed hands Claude (web/src/lib/feed/words.ts), so either
// surface asks the same question (companion.md §2.2, §6).

/** Names the corpus and the video, leaves the question to you, and has Claude save what it learns. */
fun videoPrompt(video: VideoRow): String {
    val by = video.channel?.let { " by $it" }.orEmpty()
    return "I have a question about a video in my vidtheque corpus: \"${video.title}\"$by, " +
        "video id ${video.videoId}. Read it with the vidtheque tools (video-summary first, " +
        "then get-segment-context where you need the detail) and cite the youtu.be links " +
        "with their timestamps. $PROFILE_NOTE\n\nMy question: "
}

/** What Claude saves from an Ask Claude conversation (#159). */
const val PROFILE_NOTE =
    "If our conversation shows a topic I care about or am tired of, save it with the " +
        "vidtheque profile tool: a topic of 2-4 words, a weight from -1 to 1 and a one-line " +
        "reason. Topics only: no companies, people, pay or job details."

/** The on-demand interview (§2.2), word for word the web's. */
const val PROFILE_PROMPT =
    "Interview me to build my interest profile in vidtheque, which decides which new videos " +
        "from the channels I follow are worth my time. Ask me these five questions one at a time, " +
        "and keep each answer short: 1. What am I building right now? 2. What do I want to learn " +
        "next? 3. What do I already know well enough to skip the basics of? 4. What am I tired of " +
        "hearing about? 5. What kind of video is worth my time? Then show me the list you would " +
        "save: topics of 2-4 words, each weighted from -1 (less of this) to 1, and what I am " +
        "building as kind \"project\". Topics only: no companies, people, pay or job details. " +
        "Once I agree, save it with the vidtheque profile tool: call it bare to see what is there, " +
        "add what is missing, and do not drop entries I wrote."

/** `+0.9`, `−0.8`, `0.0`: a weight always prints its sign. */
fun signed(weight: Double): String {
    // Locale.ROOT: a weight reads "+0.9" on every phone, as the web prints it.
    val fixed = "%.1f".format(java.util.Locale.ROOT, kotlin.math.abs(weight))
    if (fixed == "0.0") return fixed
    return (if (weight < 0) "−" else "+") + fixed
}

fun claudeUri(prompt: String): Uri = Uri.parse("https://claude.ai/new?q=${Uri.encode(prompt)}")
