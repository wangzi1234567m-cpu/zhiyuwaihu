"""Reorder complete approved clauses; never shorten facts or complete notification."""
import re


def answer_first(text,intents,templates,company):
    key='guarantee_refusal' if 'ask_guarantee' in intents else 'source_grounding' if 'ask_source' in intents else None
    prefix=getattr(templates,key,'').replace('{company}',company) if key else ''
    if not prefix or prefix not in text:return text
    remainder=text.replace(prefix,'',1).strip()
    return prefix+(' '+remainder if remainder else '')


def semantic_segments(text):
    match=re.search(r'[。！？!?]',text)
    if not match or not text[match.end():].strip():return [text]
    # Preserve exact persisted text including whitespace; max two speech segments.
    return [text[:match.end()],text[match.end():]]
