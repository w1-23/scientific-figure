# Interactive review of a reconstruction

The review bridge lets a user watch the current SVG, zoom into it, and pin
comments while the host keeps drawing. It is optional: every draw, check,
export and project transaction still works without a browser or a server.

## Source and session

`review-serve SOURCE` accepts:

- a draft preview directory: `figure.svg` + `preview.json`;
- an engine project: `figure.svg` + `receipt.json`;
- a single `.svg` file.

The served figure is always the newest successfully published `figure.svg`; a
preview directory is replaced atomically, so the bridge retries a read that
races the rename. Revision comes from `preview.json` or `receipt.json` when
present.

The session directory defaults to a sibling named `<source>-review/` and holds:

```
<preview>-review/
├── session.json      # source, figure_id, created_at (never rewritten)
└── feedback.json     # append-only rounds; absent until the first submission
```

Feedback deliberately lives outside the preview directory: a new preview
replaces that directory wholesale. A session is bound to one resolved source
and refuses to be reused for another.

## Feedback protocol

A submission is strict JSON bound to the SVG content the user actually saw:

```json
{
  "schema_version": "0.1",
  "figure_id": "quick",
  "svg_sha256": "<sha256 of the sanitized served SVG>",
  "scene_revision": 2,
  "status": "changes_requested",
  "comments": [
    {"object_id": "mitochondrion.inner_membrane",
     "position": [205.5, 112.0],
     "comment": "The cristae should follow the reference more closely."}
  ]
}
```

- `status` is `approved` or `changes_requested`.
- Each comment names an object ID present in the served SVG, or `null` for a
  location-only pin. `position` is optional/null but normally the SVG user-space
  point; `comment` is required, 1..2000 characters, at most 100 comments.
- A comment may carry `svg_sha256`; the server fills it with the submission's
  digest when omitted. This records pins that were placed on an earlier version
  and carried across a reload.

The server rejects with HTTP 409 when `svg_sha256` is not the currently
published figure. Old feedback is never silently attached to a new drawing.
Unknown object IDs, unknown fields and oversized bodies are rejected with 400
or 413.

## Commands

```bash
figure-cli review-serve work/quick --open
figure-cli review-serve work/quick --wait --timeout 900
figure-cli review-status work/quick-review
```

`review-serve` binds `127.0.0.1` (override with `--host` only when you
understand the exposure), issues a random token, prints one JSON line with the
URL, then blocks. `--wait` returns after the next feedback round; without it,
Ctrl-C stops the server. `review-status` prints the latest round, the current
SVG digest and `stale: true/false`. `--port 0` picks a free port.

## Page behaviour

The page fetches `/svg`, `/state` and posts to `/feedback`. It presents one
compact preview panel: a file/version bar, a canvas with the figure fitted and
centred, numbered pins, and a list of comment cards. The figure is fitted on first load,
when a new version arrives, when the page is restored from the back/forward
cache, and on window resizes until the user pans or zooms; the reset button
restores the fit. A manual zoom survives a same-version reload. It keeps
the current zoom, pan and unsent pins when the figure is reloaded, and shows a
banner when a newer version is available instead of replacing the drawing under
the user's cursor. Pins placed on an earlier version are marked; the submission
still binds to the version currently displayed.

The page is a local browser fallback. It does not install a VS Code webview or
own any drawing state. An editor host that wants a side panel can consume the
same three routes and write the same feedback file; it must not become a second
source of Scene data.

## Security and limits

- The server reads only the selected figure; there is no directory browsing or
  arbitrary file route.
- Served SVG is parsed before display and rejects `script`, `foreignObject`,
  `iframe`, `image`, `audio`, `video`, `a`, `use`, `on*` handlers, DOCTYPE /
  external stylesheets and non-local `href` or `url(...)` references.
- Responses carry `Content-Security-Policy: default-src 'none'` (inline style
  and script only), `nosniff`, `no-referrer` and `no-store`.
- Feedback bodies are capped at 256 KiB; the served SVG at 8 MiB.
- Feedback is data. Applying it is a host decision through a patch or
  generation merge; the bridge never executes a comment as code.

## External code

The page and server are an independent implementation in this repository, with
no third-party JavaScript or Node runtime. Draw Better SVG's MIT-licensed
review server and page were reviewed as a design reference for the pin /
feedback round-trip; no source was copied, so no additional notice is required.
If a later change vendors any of that code, record its fixed upstream revision,
files, MIT notice and modifications in `THIRD_PARTY_NOTICES.md` before merging.
