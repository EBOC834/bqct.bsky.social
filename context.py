import os
from memory import load_context, save_context, load_daily_post_date, save_daily_post_date

def merge_contexts(root_post: dict, recent_posts: list, memory: str, search_results: str) -> str:
    parts = []
    if memory:
        parts.append(f"Thread Summary:\n{memory}\n")
    if root_post and root_post.get("text"):
        marker = " [ROOT]" if root_post.get("is_root") else ""
        line = f"@{root_post.get('handle', 'unknown')}{marker}: {root_post['text']}"
        if root_post.get("embed"):
            line += f" {root_post['embed']}"
        if root_post.get("link_hints"):
            line += " " + " ".join(root_post["link_hints"])
        parts.append(f"Thread Context:\n{line}")
    for p in recent_posts:
        if p.get("is_root"):
            continue
        marker = " [BOT]" if p.get("handle") == os.getenv("BOT_HANDLE") else ""
        line = f"@{p.get('handle', 'unknown')}{marker}: {p.get('text', '')}"
        if p.get("embed"):
            line += f" {p['embed']}"
        if p.get("link_hints"):
            line += " " + " ".join(p["link_hints"])
        parts.append(line)
    if search_results:
        parts.append(f"Search Results:\n{search_results}")
    all_alts = []
    for p in [root_post] + recent_posts:
        if p and p.get("alts"):
            all_alts.extend(p["alts"])
    if all_alts:
        parts.append(f"\n[Image/Video alts: {'; '.join(set(all_alts))}]")
    return "\n".join(parts)
