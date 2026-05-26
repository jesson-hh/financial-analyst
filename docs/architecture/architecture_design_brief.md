# Design Brief — Guānlán Architecture Diagram

> **For**: AI design tool (Claude artifact / v0 / Bolt / Figma AI) or human designer.
> **Output**: one static architecture diagram for the project README.
> **Register**: arXiv paper Figure 1. NOT Chinese landscape, NOT anime, NOT cyberpunk.

---

## 1. Context

Guānlán (觀瀾) is an open-source A-share equity research CLI tool.
One command (`fa report SH600519`) spins up a swarm of **24 AI agents in 4 trust tiers** and produces a buy-side equity research report in ~10 minutes.

This diagram replaces a plain ASCII tier-flow block currently in the README. It must explain the 4-tier swarm **at a glance**, in the visual register of a modern AI/ML paper figure.

---

## 2. Canvas

- **Aspect / size**: 2400 × 1350 px (16:9). Export SVG (primary) + PNG @2x (3200×1800).
- **Background**: warm off-white `#FAFAF7`.
- **Optional grid**: 16px dot grid `#ECECE5` at 35% opacity.
- **Dark-mode variant**: same diagram on `#13131A` background, text `#E8E8E0`, cinnabar brightened to `#D14B47`.

---

## 3. Typography

| Use | Font | Size | Weight |
|---|---|---|---|
| Title | IBM Plex Serif | 44pt | Medium |
| Tier labels (UPPERCASE, letter-spacing 0.08em) | IBM Plex Sans | 16pt | SemiBold |
| Agent names (monospace, identifier-style) | IBM Plex Mono | 13pt | Regular |
| Footnote / legend | IBM Plex Sans | 11pt | Regular |

**Fallback** if Plex unavailable: Source Serif Pro / Inter / JetBrains Mono.

---

## 4. Color palette

| Role | Hex | Notes |
|---|---|---|
| Background | `#FAFAF7` | warm paper, not pure white |
| Ink / text / outlines | `#1A1A1A` | not pure black |
| Tier 1 · DATA | `#4A6FA5` | mineral blue |
| Tier 2 · ANALYSIS | `#6B8E7A` | mineral green |
| Tier 3 · DECISION | `#9E3E3C` | cinnabar — the **focal color** |
| Tier 4 · AUDIT | `#7A6A4F` | clay brown |
| Memory substrate | `#E8E4D8` | rice paper light |
| Data-flow arrows | `#666666` | 1.5pt solid |
| **Write arrow (1 only)** | `#9E3E3C` | 3pt solid — the visual climax |
| Memory / feedback arrows | `#999999` | 1.5pt dashed (6-4) |
| Trust boundary, 🔒 lock glyph | `#D97706` | amber |

**Hard rule**: cinnabar `#9E3E3C` appears only on (a) `report-writer`'s border, and (b) the single arrow exiting `report-writer`. Total cinnabar coverage ≤5% of the frame.

---

## 5. Composition · left-to-right DAG

```
                 [Title: Guānlán · Financial Analyst]
                 [Subtitle: A 4-tier, 24-agent swarm…]

                 ┌──────────────┐
                 │  SH600519    │  (input pill, centered above Tier 1)
                 └──────┬───────┘
                        ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ TIER 1   │→ │ TIER 2   │→ │ TIER 3   │→ │ TIER 4   │
│ DATA  ·7 │  │ ANAL  ·4 │  │ DEC  ·4  │  │ AUDIT ·1 │
└─────┬────┘  └─────┬────┘  └─────┬────┘  └─────┬────┘
      │             │             │             │
      └─────────────┴─────────────┴─────────────┘
                          ▼  retrieve (dashed)
            ┌────────── MEMORY substrate ──────────┐
            └─────────────────▲─────────────────────┘
                              └ propose · dream loop (dashed cinnabar from T4)
```

Right of Tier 4 (floating, gray-bordered satellites):

```
┌─ MARKET · n=8 ───────────┐    ┌─ META ──────────┐
│ (8 cross-stock agents)   │    │ ask (31 tools)  │
└──────────────────────────┘    └─────────────────┘
```

---

## 6. Tier container style

- Rounded rectangle, **8px corner radius**.
- **2pt border** in tier color.
- Fill: tier color at **8% tint** (almost white, hue barely visible).
- **Header bar**: 36px high, tier color at 100% fill, white text.
- Header content: left = `TIER N · LABEL`, right = `n=X · parallel|serial`.
- Inside, each agent is one row, 22px line-height:
  - 6×6 colored square + monospace agent name.

---

## 7. Agent contents (exact names, do not paraphrase)

### Tier 1 · DATA · parallel · n=7  (mineral blue)
- `quote-fetcher`
- `factor-computer`
- `model-predictor`
- 🔒 `news-reader`   ← amber lock glyph prefix, row fill `#FFF5E5`
- 🔒 `f10-reader`    ← same
- `overseas-scanner`
- `sector-rotation`

**Inside Tier 1**: a horizontal 1pt amber line above the two 🔒 rows, labeled
`— TRUST BOUNDARY · JSON-schema locked —` in 9pt amber.

### Tier 2 · ANALYSIS · parallel · n=4  (mineral green)
- `fundamental`
- `technical`
- `whale-sentiment`
- `quant`

### Tier 3 · DECISION · serial · n=4  (cinnabar)
- `bull-advocate`     ╮
- `bear-advocate`     ┤── three arrows fan-converge into ↓
- `risk-officer`      ╯
- ✏ **`report-writer`** ← thicker cinnabar border (3pt), pen glyph prefix, bold weight. This is THE focal element.

A single **3pt cinnabar arrow** exits `report-writer` to the right → a small page-shape icon labeled `report.md`, with annotation in 10pt italic `#9E3E3C`:
> *"only edge that writes to disk"*

### Tier 4 · AUDIT · post-mortem · n=1  (clay brown)
- `introspector`

---

## 8. Memory substrate

Horizontal rounded rectangle below all four tiers, fill `#E8E4D8`.
Label: `MEMORY · 24 markdown dirs · _shared/playbook_V1_V10.md · FTS5 index`

Arrows:
- Dashed gray ↓ from Tier 1, 2, 3 into memory, each labeled `retrieve` (9pt).
- Dashed clay-brown curve from Tier 4 back up into memory, labeled
  `propose → _proposed/  ·  no auto-merge  ·  dream loop` (9pt).

---

## 9. Satellite modules (right side, gray-bordered, same container style)

### MARKET · cross-stock · n=8
- `market-scanner`
- `morning-brief-writer`
- `catalyst-extractor`
- `global-news-aggregator`
- `macro-impact-analyzer`
- `mainline-classifier`
- `mainline-writer`
- `intraday-reviewer`

### META
- `ask  (free-form Q&A · 31 buddy tools)`

---

## 10. Legend (bottom-left, 200×150 white box, 1pt `#CCC` border, 4px radius)

```
▪  sub-agent
🔒 JSON-schema locked ingress
✏  write privilege
→  data hand-off
⇢  memory retrieval / feedback
━  trust boundary
```

---

## 11. Footnote caption (bottom, italic 11pt `#555`, max 4 lines)

> **Architectural invariants** — (1) only `report-writer` may write to disk;
> (2) Tier-1 untrusted ingress (news, F10) is JSON-schema-locked;
> (3) Tier-3 debate converges before persistence — single writer;
> (4) Tier-4 introspector proposes memory edits to `_proposed/` — no auto-merge, human approval required.

---

## 12. Brand anchor (the ONLY Chinese visual element)

Bottom-right corner, 32px from canvas edge:

- **16×16 px cinnabar square seal stamp**.
- Inside: single character `瀾` in **seal script (zhuanshu)**.
- White character on `#9E3E3C` fill.
- This is the *entire* Chinese-style visual budget. Nothing else.

Beside it, 10pt IBM Plex Sans `#555`:
`觀瀾 · v1.0.6 · 2026`

---

## 13. Style references — DO

- arXiv Figure 1 of recent AI papers: InstructGPT, Constitutional AI, DSPy, ToT, ReAct, Stable Diffusion.
- DeepMind / Anthropic technical post diagrams.
- Edward Tufte clarity — generous whitespace ≥30%.

## 14. Anti-style — DON'T

- ❌ Chinese landscape, ink wash, mountains, water, scrolls, brushes, calligraphy backgrounds (anything from 国风 / 山水画 vocabulary).
- ❌ Anime, chibi, big eyes, sakura, shrines.
- ❌ Cyberpunk, neon, glow, data-particle effects.
- ❌ Lucidchart / Visio default look (blue gradients, drop shadows).
- ❌ Icons of lightbulbs, gears, clouds, robots, brains. The *only* icons allowed are 🔒 (lock) and ✏ (pen).
- ❌ 3D, isometric, plastic, gradient mesh.
- ❌ Symmetric central composition.
- ❌ Cinnabar exceeding 5% of frame.

---

## 15. Deliverables

1. `architecture-light.svg` — vector, 2400×1350
2. `architecture-light@2x.png` — 3200×1800 raster
3. `architecture-dark.svg` — same diagram on `#13131A` bg, text `#E8E8E0`, cinnabar brightened to `#D14B47`
4. `architecture-dark@2x.png`
5. *(Optional)* Figma source file for future maintenance.

---

*Brief v1.0 · 2026-05-26 · For `G:/financial-analyst/docs/architecture/`*
