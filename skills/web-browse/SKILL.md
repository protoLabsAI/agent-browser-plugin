---
name: web-browse
description: >-
  Open a website, fill a form, click buttons, take a screenshot, scrape or
  extract page content, test a web app, log in, or automate any browser
  interaction.  Trigger when the user asks you to browse, visit, navigate,
  interact with, or automate a web page.
tools:
  - browser_open
  - browser_snapshot
  - browser_click
  - browser_fill
  - browser_type
  - browser_get_text
  - browser_press
  - browser_screenshot
  - browser_close
---

# web-browse

Drive a real browser through the `agent-browser` CLI.  Every task follows the
same four-step loop:

1. **Open** — `browser_open <url>` to navigate to the target page.
2. **Snapshot** — `browser_snapshot` to get the accessibility tree with
   compact `@eN` element refs (e.g. `@e7`).
3. **Act** — use an `@eN` ref (or a CSS selector) with `browser_click`,
   `browser_fill`, `browser_type`, `browser_press`, etc.
4. **Verify** — run `browser_snapshot` again (or `browser_get_text`) to
   confirm the action succeeded; repeat steps 3–4 until done.

When finished, call `browser_close` to free the browser session.

For the always-current usage — workflows, common patterns, troubleshooting, and
(with `--full`) the complete command reference and templates — load it from the
CLI, which serves skill content matched to the installed version so the
instructions never go stale:

```
agent-browser skills get core          # start here — workflows, patterns, troubleshooting
agent-browser skills get core --full   # + full command reference and templates
```

There are also specialized skills (e.g. Electron apps, Slack, cloud browsers) —
list them with `agent-browser skills list`.
