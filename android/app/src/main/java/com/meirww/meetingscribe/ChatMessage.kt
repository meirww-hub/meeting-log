package com.meirww.meetingscribe

data class Citation(
    val recordingTitle: String,
    val timestamp: String,
    val quote: String,
)

data class ChatMessage(
    val isUser: Boolean,
    val text: String,
    val citations: List<Citation> = emptyList(),
)
