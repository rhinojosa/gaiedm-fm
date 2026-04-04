"""
Deep House Radio — Knowledge Base System

Captures content from URLs (Instagram, LinkedIn, X, Reddit, articles, etc.),
extracts and converts to markdown, then compiles into a structured wiki
using an LLM for organization, summarization, and cross-referencing.

Architecture:
    capture.py      — URL ingestion & content extraction
    store.py        — Raw storage & SQLite catalog
    compiler.py     — LLM-powered wiki compilation (raw → .md wiki)
    wiki.py         — Wiki index, search, and Q&A interface
    health.py       — Wiki linting, consistency checks, gap detection
"""
