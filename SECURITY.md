# Security

Cueline runs on your computer and reads content from the internet, so these reports are welcome:

- a source (video, page, feed, file) that makes the AI or the app do something it shouldn't
- a website that can reach the local server, your keys or your files
- a generated page that escapes its sandbox
- a fetch that reaches your computer or local network

**Please report privately:** [open a security advisory](https://github.com/ghost-mb/cueline/security/advisories/new)
rather than a public issue. Include the steps to reproduce and the output of `cueline --version`.
Only the latest version on `main` gets fixes. This is a one-person project, so replies are best effort.

## What Cueline protects against

The protections are listed in the [README](README.md#privacy-and-safety).

## What it doesn't

- **Other programs or users on your computer.** The server has no login: anything running locally can call it on
  `127.0.0.1`. API keys are stored in plain text in `~/.cueline/keys.json`, readable only by your user account.
- **Mistakes by the AI.** Sources are treated as untrusted data and the ChatGPT/Codex engine has no tools, but a
  hostile source can still try to influence what the AI writes. Read outputs before you publish them.
- **Your AI provider.** The text of the sources you use goes to the AI you picked, under that provider's terms.
