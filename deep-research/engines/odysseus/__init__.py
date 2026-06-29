"""
Odysseus Deep Research engine — vendored into the DDDD platform for side-by-side
comparison against our own DRT.

The engine (deep_research.py), goal-based extractor, and prompt-injection guard are
vendored verbatim from the Odysseus project (github.com/pewdiepie-archdaemon/odysseus),
whose Deep Research is itself adapted from Alibaba Tongyi DeepResearch (Apache-2.0).

Two external seams are reimplemented here so it runs standalone in this platform:
  * llm_core.py  — routes the engine's LLM calls to our Anthropic key (claude-sonnet-4-6),
                   so a comparison isolates METHODOLOGY rather than model choice.
  * search.py    — the engine's no-key DuckDuckGo search + httpx/bs4 page fetch
                   (the repo's native SearXNG/Brave/Tavily providers need infra/keys).
"""
