# Manual Visuals Implementation Plan

> Execute inline with superpowers:executing-plans. Verify artifacts before completion.

**Goal:** Replace all 54 manual Mermaid blocks with illustrated assets and incorporate the website architecture.

**Architecture:** Store editable diagram data and a deterministic SVG/PNG renderer under `docs/`.
Publish committed PNG assets with descriptive captions; reuse official product images with provenance.

**Tech Stack:** AsciiDoc, Antora, SVG, PNG, Node.js, Graphviz WASM, Sharp, Asciidoctor PDF.

**Spec:** `docs/superpowers/specs/2026-11-07-manual-visuals-design.md`

## Global constraints

- Isolated branch; documentation files only.
- Preserve all original graph semantics; all 54 diagrams must be covered.
- Committed assets render without a diagram service during HTML/PDF builds.

## Review focus

- Long Korean labels remain legible and unclipped.
- Cycles, reverse arrows and edge labels remain distinguishable.
- Sequence alternatives/self-calls and ER cardinalities retain meaning.
- Subgraph boundaries and disconnected components remain present.
- Every page references an existing asset; website sources are credited.

## Tasks

- [x] Inventory the original diagrams, record semantic source data, and create shared illustrated SVG/PNG rendering.
- [x] Replace all 54 blocks, insert website platform/architecture images with context and provenance.
- [x] Remove Kroki configuration and document regeneration and offline image behavior.
- [x] Check complete conversion, build HTML/PDF, inspect rendered examples and a contact sheet, review diff.

Documentation/artwork verification uses source-to-asset audits and actual rendering, rather than application unit tests.

## Verification results

- 54 original diagrams (36 flow, 12 sequence, 5 state, 1 ER) converted across 47 pages.
- 56 new image references resolve; all original node, edge and event labels remain in SVG text.
- 54 SVG/XML and PNG files validated; official homepage images match downloaded originals byte-for-byte.
- Chromium text bounds: 54 diagrams, zero clipped text elements.
- Desktop/mobile introduction: no broken images or horizontal overflow; all 3 diagrams open original images in a new tab.
- Antora 3.1.14 HTML build passed with the primary-repository + named-worktree source configuration documented in README.
- `make pdf` passed without warnings; A4 manual rendered and sample architecture, workflow, and long-diagram pages inspected.
- Independent review found no critical or important findings; temporary dependency symlinks were replaced with ignored local dependency directories.
- `node --check tools/render-diagrams.mjs` and `git diff --check` passed.

The branch is retained for review; this task does not require integration into the original working branch.

## Integration into main

User requested merge and push to main. The integration baseline is `ada6396e3`.
Main has moved/split manual pages and updated diagrams since the original branch was created.
Conflict resolution retains all current-main prose, page moves/deletions, screenshots, PDF Noto fonts,
font sizes and copyright, and converts all 60 current diagrams. The original 54-diagram inventory
above records the branch before integration. Old unused diagram assets were retired.

- Latest-main prose comparison passed for every page (only image replacements and index additions differ).
- Independent semantic review passed for all 60 diagrams, including fanouts, undirected mappings,
  self-loops, model-stage transitions and current extension services.
- Explicit database icons preserve source cylinder nodes; bidirectional legends cover transitions.

Final merged-tree verification: Antora HTML and `make pdf` both exited 0 without warnings;
all 60 SVG/PNG assets and 62 image references passed validation, all diagram text remained
inside the SVG bounds, and desktop/mobile introduction and original-image links passed.
