# Mugen Char Linter — Agent guide

Stdlib-only tool that lints MUGEN character **text** files (`.cns` / `.cmd` / `.air`), never media. Not an Ikemen linter (that is later).

- `mugen_char_linter.py` — all logic + CLI
- `mugen_char_linter_gui.py` — Tkinter GUI, imports the core. No `tkinterdnd2`, no pip deps in the shipped app. PyInstaller is a local build-time exception only.

Read the `.py` files. This doc is invariants and landmines, not a changelog. After any change to behavior, update **this file** — keep it short.

## Rules

1. **Don't guess the engine.** Confirm sctrl params from https://github.com/potsmugen/ikemen-merged-docs/blob/main/static/sctrl_mugen11.md. Maintainer-provided before/after examples, and real MUGEN files (character comments, KFM, etc.) also count. Runtime facts already in this guide stay. If a new question isn't covered by those, ask the maintainer. **Never consult Ikemen documentation/wiki as a source for MUGEN facts** - Ikemen is a different, diverging, still-changing engine (see Parked); anything sourced from its docs, even to cross-reference or corroborate, is guessing the wrong engine. That includes ikemen-engine/Ikemen-GO's own wiki pages (Character features, State controllers (new), etc.) - separate from the sctrl_mugen11.md doc above, which is MUGEN 1.1-only despite living in a repo with "ikemen" in the name.
2. **Real files beat synthetics.** Prefer KFM / real characters the maintainer actually tests with.
3. **Non-destructive default:** comment-out, not delete. In-place changing runs back up: `.bak` is the original (never overwritten); later changing runs write `.bak2`, `.bak3`, …. No backup and no rewrite when the file is already clean. Analyze / `--dry-run` writes nothing except an optional `.diff`.
4. **Ask the maintainer before building** when scope is ambiguous.
5. **MUGEN parameters only.** If Elecbyte MUGEN ever accepted a name (including deprecated dual names), allow it. If it is not a MUGEN parameter, prune it (when the controller type is known). Do not maintain a denylist of foreign keys.

## Architecture

**Params.** `SCTRL_PARAMS` is own keys only, then flattened at import by `_resolve_sctrl_params()` from:

- `SCTRL_INHERITS`: projectile←hitdef, reversaldef←hitdef, modifyexplod←explod, allpalfx/bgpalfx←palfx, appendtoclipboard←displaytoclipboard, changeanim2←changeanim, selfstate←changestate, superpause←pause
- `SCTRL_PREFIX_INHERITS`: projectile also gets AfterImage keys as dotted `afterimage.*` (never underscores)

Edit the own sets + inherit tables, not the resolved copies. `'statedef'` is Statedef-block keys, not a controller type.

`var()`/`fvar()`/`sysvar()`/`sysfvar()` shorthand is only valid on VarSet/VarAdd/ParentVarSet/ParentVarAdd, checked per-controller via `is_var_shorthand_allowed()` - not `ALWAYS_ALLOWED`, which is only for keys valid on every controller. Doc says sysvar/sysfvar actually crash the Parent* versions; deliberately not pruning that separately - too niche to maintain, and the engine punishes it directly.

`SKIP_PRUNE_TYPES` = `{null, zoom}`. `null` is disable-in-place (keep every key). `zoom` is 1.1 beta with no stable official list — accept anything. Empty own-sets are `set()`, not `{}`. Those empty sets are not used for validation.

**Value validation (CNS).** `ENUM_VALUES_BY_TYPE[ctrl_type][key]` — domains are per controller type, not global per key name. Currently: Trans/Explod/ModifyExplod `trans` = `{default,none,add,add1,addalpha,sub}`; AfterImage `trans` and Projectile `afterimage.trans` = `{none,add,add1,sub}` (no `default`/`addalpha`); Explod/ModifyExplod `postype` = `{p1,p2,front,back,left,right,none}`; Helper/Projectile `postype` omit `none`; Explod/ModifyExplod `space` = `{stage,screen}`. Engine silently no-ops a bad value (same dead-line outcome as an unknown key). Deliberately narrow: only literal closed-enum keys, never numeric/expression-valued params. Case-insensitive, exact spelling only. Only checked on a key that already survived pruning. Toggle `do_check_values`, tag `[CNS Invalid Value]`, stat `invalid_values_removed`. Expanding requires Rule 1 sourcing — don't guess a key's enum domain, don't assume it's identical across types.

**CNS** (`process_file`): prune unknown keys, dedupe within a block (keep **first** — engine skips later dups), normalize `[State]` headers to the enclosing `[Statedef N]`. Any bracket header ends a state block (a `[Command]` after `[State -1]` is not part of it). Header rewrite must preserve original comment whitespace. Header normalize is **counted only, never logged per line**.

On VarSet/VarAdd/ParentVarSet/ParentVarAdd, `v`/`fv`/`var()`/`fvar()`/`sysvar()`/`sysfvar()` are one shared "target" slot, not independent keys — maintainer-confirmed: only the first one across any of those spellings has effect at runtime, later ones (even under a different spelling) are dead. Deduped as a group (`is_var_target_key` / `VAR_TARGET_GROUP`), same `[CNS Duplicate Parameter]` tag as an ordinary same-key dup - no separate tag. `value` is the payload on these four, not a target — untouched by this, still plain per-key dedupe. Goes through the existing `do_dedupe` + removal_mode toggle, no separate flag.

**Garbage (CNS):** a line is garbage iff it has **no top-level `=`**. Any `[...]` header is structure (`is_any_header_shape`), never garbage — do not whitelist section names. Do not reintroduce `is_plausible_key` / key-shape checks.

**CMD** (`process_cmd_file`): only `[Command]` / `[Remap]` / `[Defaults]`. Never dedupe `[Command]` blocks by `name` (alternate motions). Official keys only: Command `name`/`command`/`time`/`buffer.time`; Defaults `command.time`/`command.buffer.time` (fallback values for the matching per-Command keys); Remap `x,y,z,a,b,c,s`. Confirmed via a real character's `.cmd` header comments (Rule 2), not Ikemen docs (Rule 1) - a stock `.cmd` documents `buffer.time` directly.

**Routing:** `classify_file()` returns only `'cns'` or `'air'`. Every non-AIR file runs **both** CNS and CMD engines. `.def` discovery uses `st`/`st0`/…/`cmd` and `anim` — not `stcommon`, not `cns`. Role from `.def` beats file extension.

**AIR** (`process_air_file`): `[Begin Action N]` only. Duplicates keep first. Empty actions can bake the next-action fallthrough, then that baked body is garbage-scanned. Shape only — do not count commas or validate sprite/timing/clsn values. Recognized: blank, `;`, any line starting with a digit or `-` (frame or coords; field count is not a garbage test), `Loopstart`, `Interpolate offset/scale/angle/blend`, anything starting `clsn` (`Clsn2:` / `Clsn2Default:` / `Clsn2[0] = …`). Anything else is garbage. “Has content” = any non-comment line (cheap; a junk-only body still counts as non-empty).

**Logs:** spelled-out tags (`[CNS Unknown Parameter]`, …). Unknown-param lines include `(type=…)`. Summary verb is "removed". Align summary with `W = 35`, not hand-spaced. Dry-run wording is "no input files were modified" (a `.diff` may still be written). Every line under a heading is indented two spaces. No bullets. Dry-run only prints `(would overwrite/write to …)` when the file would actually change.

**File I/O:** `read_text_file` → `TextFile` (text with LF endings, BOM, newline). Decoded as UTF-8 with `surrogateescape`, so non-UTF-8 bytes (Shift-JIS/cp1252) round-trip exactly and are never whitespace — don't switch to Latin-1 (`strip()` would eat bytes 0x85/0xA0). Log text goes through `printable()`. `commit_output` writes back with the same encoding/BOM/newline and keeps a missing final newline missing. Split file text with `split_lines`, never `str.splitlines()`. Compare paths with `same_file`, never `==`. Folder mode maps outputs via `plan_output_paths` (clashing names go in a subfolder named after the source folder). `.def` files are read-only and may use the ANSI code page (`read_def_text`).

**Backups:** write only when content actually changed (`commit_output`). That includes Run and `--output-dir` copies — "No changes needed." means the file on disk is not touched. `.bak` is a byte-for-byte copy (`shutil.copy2`) of the original from the first changing run and is never overwritten. Later changing runs write `.bak2`, `.bak3`, … (`allocate_backup_path`). No rewrite, no backup, no `.diff` when unchanged.

**Removal modes:** `removal_mode` is `'delete' | 'comment' | 'tag'`, shared uniformly by every fixer (CNS/CMD prune+dedupe+invalid-value, both AIR removal paths, all garbage-line handling). `'tag'` is `'comment'` plus the exact bracketed tag from that line's log entry embedded right after the `;` — e.g. `; [CNS Garbage Line] blablabla` — via `comment_out(raw, tag)` / `maybe_comment_out(line, tag)`. Tag text must match the log line's bracket text verbatim (own literal per call site, not derived) - keep them in sync by hand if a tag string changes. `tag=None` (i.e. plain `'comment'` mode) preserves the old plain `"; " + text` output exactly.

**GUI:** one file list; garbage and tagged-line checkboxes live in the "Global checks" group; "Comment out" is the first radio. Checkboxes within a fixer group (Global/CNS/CMD/AIR) are ordered by importance, most important first — not alphabetically, not by implementation order. Log filter is display-only (`self._full_log` stays complete). A compile check is not a GUI test; if your environment can't open Tkinter windows, ask the maintainer to run it.

Engines are a no-op on content they don't own (empirically). Re-check that if parsers change.

## Landmines (do not reintroduce)

- Don't whitelist garbage headers by name — `[Data]`/`[Size]`/… live in the same file as states when `cns` and `st` point at one path.
- Don't treat `type=null` as "zero params." Skip prune. Triggers stay on every type (`ALWAYS_ALLOWED` / `triggerN`).
- Don't put `justify=` on `ttk.Checkbutton`.
- Reads: `utf-8-sig`. Writes: `utf-8` (no BOM). If the output has no final newline, add one; if it already ends with `\n`, leave it.
- Always reconstruct `[State]` headers (trailing commas, comment spacing).
- Duplicate AIR action: comment the **header** too, not just the body.
- Don't log every header normalize. Don't log "DIFFERENT VALUE" on dups. `get_value()` is gone.
- `removal_mode` has three values now, not two - any new call site doing `if removal_mode == 'comment':` silently drops `'tag'` handling. Check for `in ('comment', 'tag')` instead. Processing APIs validate the mode explicitly; do not replace that with `assert`.
- Windows "Open file location": call `explorer /select,"<path>"` as a raw command string, never a Popen list (list quoting breaks paths with spaces → Explorer opens Documents).

## MUGEN param policy

Sctrl source (canonical): https://www.elecbyte.com/mugendocs-11b1/sctrls.html  
Sctrl mirror used for cross-checking: https://github.com/potsmugen/ikemen-merged-docs/blob/main/static/sctrl_mugen11.md  
Trigger source (canonical): https://www.elecbyte.com/mugendocs-11b1/trigger.html  
Already-accepted dual names (2002 / maintainer-confirmed) stay in the tables even if 1.1 dropped them. Don't strip those to "match 1.1 only."

Dual names in the tables: PlaySnd `volume`+`volumescale`; Explod/ModifyExplod `vel`+`velocity` (docs list `vel`; maintainer confirmed `velocity` works); Projectile `velocity`+`vel`; Explod `supermove`+`supermovetime`; AfterImageTime / ExplodBindTime `time`+`value`; HitDef `attack.width` (2002, unused); Explod `alpha` (2002, `trans=addalpha`).

Also official-era: TagIn (`stateno`/`partnerstateno`/`ctrl`), RemapPal, VictoryQuote.

**HitBy / NotHitBy have no `attr` key.** Docs: `value` or `value2` (attr *string*), plus `time`. Examples are `value = S, NA`. `attr` is HitDef-only. Maintainer-confirmed: `attr` on HitBy does nothing. Prune it.

A controller `type` not in `SCTRL_PARAMS` is unrecognized: skip prune (same as `SKIP_PRUNE_TYPES`). A known type only keeps keys in its resolved set (plus `ALWAYS_ALLOWED`).

## Regressions

- Projectile = HitDef ∪ own ∪ dotted `afterimage.*` (no `afterimage_time`)
- ReversalDef = HitDef ∪ `reversal.attr`. 1.1 text only names pausetime/sparkno/hitsound/p1stateno/p2stateno plus `reversal.attr`; the rest of HitDef is accepted anyway. How many of those extra keys actually work is unverified.
- ModifyExplod = Explod including `vel`/`velocity`
- PlaySnd keeps `volume` and `volumescale`
- `null` / `zoom`: no param prune
- TagIn keys above
- CMD: only the official keys listed above; anything else on those sections prunes
- HitBy/NotHitBy: only `value`/`value2`/`time`
- Don't paste HitDef's list into Projectile again
- Empty sctrl own-sets are `set()`, not `{}` (a dict). `_resolve_sctrl_params` would survive either, but keep the type honest.
- HitDef `palfx.*` = full PalFX param set (`time`/`mul`/`add`/`sinadd`/`invertall`/`color`), not just time/mul/add — doc says "rest of the parameters are the same as PalFX". Kept as literal keys, not `SCTRL_PREFIX_INHERITS` (that mechanism assumes one resolve pass; Projectile/ReversalDef already consume HitDef's plain set in that same pass, so HitDef can't also be a prefix-inherit child without a second pass)
- Explod does NOT get `under` - unconfirmed in doc, that key belongs to GameMakeAnim/EnvColor
- Tables stay MUGEN-only

## Parked / don't do unless asked

- Ikemen linter — later or never. This tool is MUGEN (stable/dead). Ikemen is a live moving target.
- CMD motion-string parsing (`F,F`, `~`, `$`, `+`, …) — parameter names only, for now.
- CNS/CMD/AIR GUI tabs — keep one window.
- Shipped pip dependencies — stdlib only. PyInstaller is local/Git build for a double-click GUI.
