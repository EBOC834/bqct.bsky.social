ANSWER_PROMPTS = {
    1: "Answer concisely. Stick to the topic. Be natural. Output only the final answer.",
    2: "Answer in a friendly tone. Keep it short and helpful. Output only the final answer.",
    3: "Answer with enthusiasm. Keep it under 300 characters. Output only the final answer.",
    4: "If sources conflict, mention the range or uncertainty. Output only the final answer.",
    5: "If external data is unavailable, state that clearly and answer based on general knowledge. Output only the final answer."
}

SUMMARIZE_PROMPT = "You are maintaining a concise summary of a conversation thread. Update the summary with new essential information from the interaction. Remove redundant or outdated details. Keep it under 300 characters. Output only the summary text."
