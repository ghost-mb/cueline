# Examples

Real Cueline outputs, unedited. The only change: citations like `[S1 0:52]` were turned into links that open the
video at that second.

## Hubble & Webb (`hubble-webb/`)

A notebook with two NASA videos (US government works, public domain):

| | Source |
|---|---|
| S1 | [How Hubble Images Are Made](https://www.youtube.com/watch?v=QGf0yzdM5OA) (NASA Goddard, 5 min) |
| S2 | [An Introduction to the James Webb Space Telescope Mission](https://www.youtube.com/watch?v=6VqG3Jazrfs) (NASA Goddard, 4 min) |

| Output | How it was made |
|---|---|
| [answer.md](hubble-webb/answer.md) | Chat: *"Are the colors in Hubble and Webb images real? Explain how they are chosen."* Note the last paragraph: the sources don't say how Webb images are colored, so Cueline says so instead of guessing. |
| [linkedin-post.md](hubble-webb/linkedin-post.md) | Studio → Content creation → Social post, Educational tone, *"For LinkedIn, aimed at designers and content creators."* With its fact check. |
| [SKILL.md](hubble-webb/SKILL.md) | Studio → Skill, no instructions. Ready to install into Claude Code or Codex. |

Made with the ChatGPT engine (Codex CLI). The built-in citation checker verified every citation (52 in total).

## Content marketing (`content-marketing/`)

One video: [In 3 Minutes: Why Every Content Marketing Strategy Should Be Embracing Format Agnostic Content](https://www.youtube.com/watch?v=bWPtxiA_tQE)
by **Daniel Rosehill**, licensed [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). The outputs below are
AI adaptations of his talk, not his own words.

| Output | How it was made |
|---|---|
| [newsletter.md](content-marketing/newsletter.md) | Studio → Content creation → Newsletter, Conversational tone, *"For a newsletter read by small-business owners who do their own marketing."* |
| [linkedin-post.md](content-marketing/linkedin-post.md) | Studio → Content creation → Social post, Storytelling tone, *"For LinkedIn, aimed at marketing agency owners."* |

YouTube's automatic captions spell his name "Rosell". Cueline passes the channel name to the AI, so the drafts credit
him correctly.
