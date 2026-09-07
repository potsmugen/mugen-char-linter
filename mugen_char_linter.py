#!/usr/bin/env python3
"""
Mugen Char Linter - lints/cleans up MUGEN character code files
(.cns/.cmd/.air) - never touches media files (.sff/.snd/.pcx/etc).

Handles two file families in one unified tool:

CNS-family (.cns, .cmd - anything with [State]/[Statedef] blocks):
  1. Prune       - removes parameters that aren't valid for a controller's 'type'.
  2. Dedupe      - removes duplicate parameters within the same block (keeps FIRST,
                   matching the engine's actual duplicate-key behavior).
  3. Headers     - normalises [State ...] header case and renumbers to match
                   the enclosing [Statedef N].
  4. Garbage     - handles lines that are neither blank, a comment, a header,
                   nor a well-formed parameter (leftover disabled code, stray
                   text, etc. - regardless of what style someone used).
  .cmd files get the same treatment for any [State]/[Statedef] blocks they
  contain, PLUS a dedicated CMD fixer for their own sections:
    - Prune  - removes parameters not valid for [Command]/[Remap]/[Defaults].
    - Dedupe - removes duplicate parameters WITHIN one of those blocks (keeps
               FIRST). Never treats same-named [Command] blocks as duplicates
               of each other - the format explicitly allows (and real
               character files rely on) multiple commands sharing a name to
               define alternate motions for the same move.

AIR-family (.air - [Begin Action N] sections):
  1. Dedupe      - removes duplicate [Begin Action N] blocks (keeps FIRST).
  2. Bake        - physically copies in whatever content the engine's empty-
                   animation fallthrough would use at runtime, instead of
                   relying on that mechanism.
  3. Flag empty  - reports (never modifies) any action still empty afterward.
  Only whole [Begin Action N] blocks are handled - individual frame/element
  syntax inside them is never parsed or touched.

A single global choice (delete vs. comment out) governs every removal across
both families. A character's .def file can be given directly to auto-discover
its state files (st/st0/.../cmd) and its .air file in one go.

Usage:
    python mugen_char_linter.py input.cns
    python mugen_char_linter.py input.cns input.cmd input.air
    python mugen_char_linter.py character.def
    python mugen_char_linter.py input.cns --prune --dedupe --headers --garbage-lines --mode comment
    python mugen_char_linter.py input.cns --output-dir ./fixed
    (in-place writes keep the original as .bak on first change; later
    changing runs write .bak2, .bak3, ... . No backup and no rewrite when
    the file is already clean. --output-dir writes copies instead.)
"""

import argparse
import codecs
import difflib
import locale
import os
import re
import shutil
import sys
from collections import Counter
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

TRIGGER_RE = re.compile(r'^trigger[1-9][0-9]*$', re.IGNORECASE)
# var()/fvar()/sysvar()/sysfvar() shorthand: valid on VarSet/VarAdd/ParentVarSet/
# ParentVarAdd. Doc says sysvar/sysfvar crash the Parent* versions - not worth
# a separate allow-list for that niche misuse, engine punishes it directly.
VAR_SHORTHAND_RE = re.compile(r'^(var|fvar|sysvar|sysfvar)\s*\(', re.IGNORECASE)
VAR_SHORTHAND_TYPES = frozenset({'varset', 'varadd', 'parentvarset', 'parentvaradd'})

# ----------------------------------------------------------------------
# MUGEN parameters: no versioning. If Elecbyte MUGEN ever accepted a
# name, it stays. Canonical sctrl text:
# https://github.com/potsmugen/ikemen-merged-docs/blob/main/static/sctrl_mugen11.md
# Dual old/new syntax kept (PlaySnd volume + volumescale, Explod vel +
# velocity). Not a MUGEN parameter → not in these tables.
# ----------------------------------------------------------------------

# Own parameters only. Controllers that "take all parameters of X" inherit
# via SCTRL_INHERITS / SCTRL_PREFIX_INHERITS below — don't copy those lists.
SCTRL_PARAMS: Dict[str, Set[str]] = {
    'afterimage': {
        'time', 'length', 'palcolor', 'palinvertall', 'palbright', 'palcontrast',
        'palpostbright', 'paladd', 'palmul', 'timegap', 'framegap', 'trans',
    },
    'afterimagetime': {
        'time', 'value',
    },
    'allpalfx': set(),  # inherits PalFX
    'angleadd': {
        'value',
    },
    'angledraw': {
        'value', 'scale',
    },
    'anglemul': {
        'value',
    },
    'angleset': {
        'value',
    },
    'appendtoclipboard': set(),  # inherits DisplayToClipboard
    'assertspecial': {
        'flag', 'flag2', 'flag3',
    },
    'attackdist': {
        'value',
    },
    'attackmulset': {
        'value',
    },
    'bindtoparent': {
        'time', 'facing', 'pos',
    },
    'bindtoroot': {
        'time', 'facing', 'pos',
    },
    'bindtotarget': {
        'id', 'time', 'pos',
    },
    'bgpalfx': set(),  # inherits PalFX
    'changeanim': {
        'value', 'elem',
    },
    'changeanim2': set(),  # inherits ChangeAnim
    'changestate': {
        'value', 'ctrl', 'anim',
    },
    'clearclipboard': set(),
    'ctrlset': {
        'value',
    },
    'defencemulset': {
        'value',
    },
    'destroyself': {
        'recursive', 'removeexplods',
    },
    'displaytoclipboard': {
        'text', 'params',
    },
    'envcolor': {
        'value', 'time', 'under',
    },
    'envshake': {
        'time', 'ampl', 'freq', 'phase',
    },
    'explod': {
        'anim', 'id', 'space', 'pos', 'facing', 'vfacing', 'bindid', 'bindtime',
        'vel', 'velocity',  # docs name is vel; velocity accepted as the old/alt name
        'accel', 'removetime', 'supermovetime', 'pausemovetime', 'scale',
        'angle', 'yangle', 'xangle', 'sprpriority', 'ontop', 'shadow', 'ownpal',
        'remappal', 'removeongethit', 'ignorehitpause', 'trans', 'alpha',
        # deprecated in 1.1 but still documented and accepted
        'postype', 'random', 'supermove',
    },
    'explodbindtime': {
        'id', 'time', 'value',
    },
    'fallenvshake': set(),
    'forcefeedback': {
        'waveform', 'time', 'freq', 'ampl', 'self',
    },
    'gamemakeanim': {
        'pos', 'random', 'under', 'value',
    },
    'gravity': set(),
    'helper': {
        'helpertype', 'name', 'postype', 'ownpal',
        'size.xscale', 'size.yscale',
        'size.ground.back', 'size.ground.front',
        'size.air.back', 'size.air.front',
        'size.height', 'size.proj.doscale',
        'size.head.pos', 'size.mid.pos', 'size.shadowoffset',
        'stateno', 'keyctrl', 'id', 'pos', 'facing',
        'pausemovetime', 'supermovetime', 'remappal',
    },
    'hitadd': {
        'value',
    },
    'hitby': {
        'value', 'value2', 'time',
    },
    'hitdef': {
        'attr', 'hitflag', 'guardflag', 'affectteam',
        'animtype', 'air.animtype', 'fall.animtype', 'priority', 'damage',
        'pausetime', 'guard.pausetime', 'sparkno', 'guard.sparkno', 'sparkxy',
        'hitsound', 'guardsound',
        'ground.type', 'air.type',
        'ground.slidetime', 'guard.slidetime',
        'ground.hittime', 'guard.hittime', 'air.hittime',
        'guard.ctrltime', 'guard.dist', 'yaccel',
        'ground.velocity', 'guard.velocity', 'air.velocity', 'airguard.velocity',
        'ground.cornerpush.veloff', 'air.cornerpush.veloff',
        'down.cornerpush.veloff', 'guard.cornerpush.veloff',
        'airguard.cornerpush.veloff', 'airguard.ctrltime', 'air.juggle',
        'mindist', 'maxdist', 'snap',
        'p1sprpriority', 'p2sprpriority',
        'p1facing', 'p1getp2facing', 'p2facing',
        'p1stateno', 'p2stateno', 'p2getp1state', 'forcestand',
        'fall', 'fall.xvelocity', 'fall.yvelocity', 'fall.recover',
        'fall.recovertime', 'fall.damage', 'air.fall', 'forcenofall',
        'down.velocity', 'down.hittime', 'down.bounce',
        'id', 'chainid', 'nochainid', 'hitonce',
        'kill', 'guard.kill', 'fall.kill', 'numhits',
        'getpower', 'givepower',
        'attack.width',  # 2002 docs ("not currently used"); keep as old syntax
        # PalFX set, prefixed. Doc: "the rest of the parameters are the same
        # as in the PalFX controller" — time/mul/add plus sinadd/invertall/color.
        'palfx.time', 'palfx.mul', 'palfx.add',
        'palfx.sinadd', 'palfx.invertall', 'palfx.color',
        'envshake.time', 'envshake.freq', 'envshake.ampl', 'envshake.phase',
        'fall.envshake.time', 'fall.envshake.freq',
        'fall.envshake.ampl', 'fall.envshake.phase',
    },
    'hitfalldamage': set(),
    'hitfallset': {
        'value', 'xvel', 'yvel',
    },
    'hitfallvel': set(),
    'hitoverride': {
        'attr', 'slot', 'stateno', 'time', 'forceair',
    },
    'hitvelset': {
        'x', 'y',
    },
    'lifeadd': {
        'value', 'absolute', 'kill',
    },
    'lifeset': {
        'value',
    },
    'makedust': {
        'spacing', 'pos', 'pos2',
    },
    'modifyexplod': set(),  # inherits Explod
    'movehitreset': set(),
    'nothitby': {
        'value', 'value2', 'time',
    },
    'null': set(),
    'offset': {
        'x', 'y',
    },
    'palfx': {
        'time', 'add', 'mul', 'sinadd', 'invertall', 'color',
    },
    'parentvaradd': {
        'v', 'fv', 'value',
    },
    'parentvarset': {
        'v', 'fv', 'value',
    },
    'pause': {
        'time', 'movetime', 'pausebg', 'endcmdbuftime',
    },
    'playerpush': {
        'value',
    },
    'playsnd': {
        'value', 'channel', 'lowpriority', 'pan', 'abspan',
        'volume', 'volumescale',  # volume = pre-1.0 RC8 name; volumescale = 1.0+
        'freqmul', 'loop',
    },
    'posadd': {
        'x', 'y',
    },
    'posfreeze': {
        'value',
    },
    'posset': {
        'x', 'y',
    },
    'poweradd': {
        'value',
    },
    'powerset': {
        'value',
    },
    'projectile': {
        'projid', 'projanim', 'projhitanim', 'projremanim', 'projcancelanim',
        'projscale', 'projremove', 'projremovetime', 'vel', 'velocity', 'remvelocity',
        'accel', 'velmul', 'projhits', 'projmisstime', 'projpriority',
        'projsprpriority', 'projedgebound', 'projstagebound', 'projheightbound',
        'offset', 'postype', 'projshadow', 'supermovetime', 'pausemovetime',
        'ownpal', 'remappal',
    },
    'remappal': {
        'source', 'dest',
    },
    'removeexplod': {
        'id',
    },
    'reversaldef': {
        'reversal.attr',
    },
    'screenbound': {
        'value', 'movecamera',
    },
    'selfstate': set(),  # inherits ChangeState
    'sndpan': {
        'channel', 'pan', 'abspan',
    },
    'sprpriority': {
        'value',
    },
    'statetypeset': {
        'statetype', 'movetype', 'physics',
    },
    'statedef': {
        'type', 'movetype', 'physics', 'anim', 'velset', 'ctrl', 'poweradd',
        'juggle', 'facep2', 'hitdefpersist', 'movehitpersist', 'hitcountpersist',
        'sprpriority',
    },
    # 2002.04.14 docs (WinMUGEN-era tag). Not in 1.1 sctrls.html.
    'tagin': {
        'stateno', 'partnerstateno', 'ctrl',
    },
    'stopsnd': {
        'channel',
    },
    'superpause': {
        'anim', 'sound', 'pos', 'darken', 'p2defmul', 'poweradd', 'unhittable',
    },
    'targetbind': {
        'id', 'time', 'pos',
    },
    'targetdrop': {
        'excludeid', 'keepone',
    },
    'targetfacing': {
        'id', 'value',
    },
    'targetlifeadd': {
        'id', 'absolute', 'kill', 'value',
    },
    'targetpoweradd': {
        'id', 'value',
    },
    'targetstate': {
        'id', 'value',
    },
    'targetveladd': {
        'id', 'x', 'y',
    },
    'targetvelset': {
        'id', 'x', 'y',
    },
    'trans': {
        'trans', 'alpha',
    },
    'turn': set(),
    'varadd': {
        'v', 'fv', 'value',
    },
    'varrandom': {
        'v', 'range',
    },
    'varrangeset': {
        'first', 'last', 'value', 'fvalue',
    },
    'varset': {
        'v', 'fv', 'value',
    },
    'veladd': {
        'x', 'y',
    },
    'velmul': {
        'x', 'y',
    },
    'velset': {
        'x', 'y',
    },
    'victoryquote': {
        'value',
    },
    'width': {
        'value', 'edge', 'player',
    },
    # 1.1 beta / undocumented. Accepted with any parameters — see
    # SKIP_PRUNE_TYPES. Entry exists so the type is recognized as MUGEN.
    'zoom': set(),
}

# Child type -> parent types whose full param set is accepted as-is.
# Confirmed from Elecbyte 1.1 wording ("takes all parameters of X" /
# "same as X" / "accepts all optional parameters that X does").
SCTRL_INHERITS: Dict[str, Tuple[str, ...]] = {
    'allpalfx': ('palfx',),
    'appendtoclipboard': ('displaytoclipboard',),
    'bgpalfx': ('palfx',),
    'changeanim2': ('changeanim',),
    'modifyexplod': ('explod',),
    'projectile': ('hitdef',),
    # 1.1 text only names pausetime/sparkno/hitsound/p1stateno/p2stateno plus
    # reversal.attr. We still accept the full HitDef set; which of the extra
    # keys actually do anything at runtime is unverified.
    'reversaldef': ('hitdef',),
    'selfstate': ('changestate',),
    'superpause': ('pause',),
}

# Child type -> (parent, prefix) pairs. Projectile documents AfterImage
# params prepended with "afterimage." (dotted names, not underscores).
SCTRL_PREFIX_INHERITS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    'projectile': (('afterimage', 'afterimage.'),),
}


def _resolve_sctrl_params(
    own: Dict[str, Set[str]],
    inherits: Dict[str, Tuple[str, ...]],
    prefix_inherits: Dict[str, Tuple[Tuple[str, str], ...]],
) -> Dict[str, Set[str]]:
    """Expand inherit tables into the flat per-type sets process_file() uses."""
    resolved = {name: set(params) for name, params in own.items()}
    # One pass is enough: no parent in these tables inherits from a child.
    for child, parents in inherits.items():
        resolved.setdefault(child, set())
        for parent in parents:
            resolved[child] |= resolved[parent]
    for child, specs in prefix_inherits.items():
        resolved.setdefault(child, set())
        for parent, prefix in specs:
            resolved[child] |= {prefix + param for param in resolved[parent]}
    return resolved


SCTRL_PARAMS = _resolve_sctrl_params(SCTRL_PARAMS, SCTRL_INHERITS, SCTRL_PREFIX_INHERITS)

# Types whose parameters are never pruned. null keeps whatever was on the
# controller so it can be re-enabled later. zoom is 1.1 beta with no stable
# official param list — accept anything until that's worth documenting.
SKIP_PRUNE_TYPES = frozenset({'null', 'zoom'})

# Params allowed on every controller regardless of type.
ALWAYS_ALLOWED = {
    'trigger1', 'trigger2', 'trigger3', 'trigger4', 'trigger5',
    'trigger6', 'trigger7', 'trigger8', 'trigger9', 'trigger10',
    'triggerall',
    'persistent', 'ignorehitpause',
    'type',  # never remove, it's not a per-controller param
}

def is_trigger_key(key: str) -> bool:
    key = key.lower()
    if key == 'triggerall':
        return True
    return TRIGGER_RE.match(key) is not None

def is_always_allowed(key: str) -> bool:
    key = key.lower()  # CNS keys are case-insensitive
    if key in ALWAYS_ALLOWED:
        return True
    if TRIGGER_RE.match(key):
        return True
    return False

def is_var_shorthand_allowed(key: str, ctrl_type: Optional[str]) -> bool:
    """var()/fvar()/sysvar()/sysfvar() are only valid on the var-family
    controllers, not every controller type."""
    return ctrl_type in VAR_SHORTHAND_TYPES and VAR_SHORTHAND_RE.match(key) is not None

def is_var_target_key(key: str) -> bool:
    """v/fv/var()/fvar()/sysvar()/sysfvar() all select ONE variable to act on -
    only the first such key in a var-family block has any effect at runtime."""
    key = key.lower()
    return key in ('v', 'fv') or VAR_SHORTHAND_RE.match(key) is not None

# Closed-enum params: a key valid for the type but whose value isn't one of
# these is silently ignored at runtime (dead line), same as an unknown key.
# Deliberately a short, engine-confirmed list, not a general value/range/type
# checker - numeric and expression-valued params stay out of this. Compared
# case-insensitively, exact spelling only.
#
# Domains are per (controller type, key). Do not assume a key's values are
# the same on every controller that accepts it — AfterImage.trans is a
# smaller set than Trans/Explod.trans; Explod.postype includes `none`
# (1.1 default) while Helper/Projectile postype do not.

_TRANS_FULL = frozenset({'default', 'none', 'add', 'add1', 'addalpha', 'sub'})
_TRANS_AFTERIMAGE = frozenset({'none', 'add', 'add1', 'sub'})
_POSTYPE_FULL = frozenset({'p1', 'p2', 'front', 'back', 'left', 'right', 'none'})
_POSTYPE_NO_NONE = frozenset({'p1', 'p2', 'front', 'back', 'left', 'right'})
_SPACE = frozenset({'stage', 'screen'})

# ctrl_type -> {key: allowed values}. Dotted keys are listed as written
# (afterimage.trans), not via the prefix-inherit machinery.
ENUM_VALUES_BY_TYPE: Dict[str, Dict[str, Set[str]]] = {
    'trans': {'trans': set(_TRANS_FULL)},
    'explod': {
        'trans': set(_TRANS_FULL),
        'postype': set(_POSTYPE_FULL),
        'space': set(_SPACE),
    },
    'modifyexplod': {
        'trans': set(_TRANS_FULL),
        'postype': set(_POSTYPE_FULL),
        'space': set(_SPACE),
    },
    'afterimage': {'trans': set(_TRANS_AFTERIMAGE)},
    'helper': {'postype': set(_POSTYPE_NO_NONE)},
    'projectile': {
        'postype': set(_POSTYPE_NO_NONE),
        'afterimage.trans': set(_TRANS_AFTERIMAGE),
    },
}


def enum_allowed_values(key: str, ctrl_type: Optional[str]) -> Optional[Set[str]]:
    """Allowed literals for a closed-enum key on this controller type, or
    None if this key is not value-checked."""
    if not ctrl_type:
        return None
    by_key = ENUM_VALUES_BY_TYPE.get(ctrl_type)
    if not by_key:
        return None
    return by_key.get(key.lower())

# ----------------------------------------------------------------------
# Shared parsing helpers
# ----------------------------------------------------------------------

def split_lines(content: str) -> List[str]:
    """Split on LF only; str.splitlines() also breaks on U+0085, U+2028 etc."""
    lines = content.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines

def strip_comment(line: str) -> str:
    """Remove inline comment (; ...) but keep quoted strings intact."""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i-1] != '\\'):
            in_quote = not in_quote
        elif not in_quote and ch == ';':
            return line[:i].strip()
    return line.strip()

def split_key_value(line: str) -> Tuple[Optional[str], Optional[str]]:
    """Split at first '=' not inside parentheses or quotes."""
    paren = 0
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i-1] != '\\'):
            in_quote = not in_quote
        elif not in_quote:
            if ch == '(':
                paren += 1
            elif ch == ')':
                paren -= 1
            elif ch == '=' and paren == 0:
                key = line[:i].strip()
                value = line[i+1:].strip()
                return key, value
    return None, None

def is_any_header_shape(line: str) -> bool:
    """
    True if the line (after stripping any inline comment) is shaped like a
    '[Section Name]' header at all - regardless of whether we recognize or
    process that particular section. This deliberately does NOT try to
    whitelist specific section names: a character's constants file has
    [Data]/[Size]/[Velocity]/[Movement]/[Quotes]/etc, a .cmd file has
    [Command...]/[Remap]/[Defaults], and any of these can legitimately share
    a physical file with [State]/[Statedef] blocks (e.g. many characters'
    .def points 'cns' and 'st' at the exact same file). Enumerating "known"
    section names is fragile and risks destroying real structure we simply
    don't have logic for yet - anything bracket-shaped is left alone.
    """
    stripped = strip_comment(line).strip()
    return len(stripped) >= 2 and stripped.startswith('[') and stripped.endswith(']')

def is_garbage_line(raw: str) -> bool:
    """
    A line that isn't blank, a real ';' comment, a section header of any
    kind (bracket-shaped, whether or not we recognize the section name), or
    a 'key = value' pair - ANY top-level '=' (not inside parens/quotes)
    counts as one, no matter how garbled the key text looks. MUGEN's own
    parser is extremely lenient here: it doesn't validate the key's shape,
    it just doesn't recognize an odd one as a known parameter - that's a
    separate "unknown parameter" concern (see flush_block's pruning step),
    not a "garbage line" one. Only lines with no '=' at all (leftover
    disabled code in whatever style someone used, stray text, etc.) count
    as garbage.
    """
    if raw.strip() == '':
        return False
    if is_comment_line(raw):
        return False
    if is_any_header_shape(raw):
        return False
    stripped = strip_comment(raw)
    if stripped == '':
        return False
    key, _ = split_key_value(stripped)
    return key is None

def count_garbage_lines(content: str) -> int:
    return sum(1 for line in split_lines(content) if is_garbage_line(line))

def is_air_garbage_line(raw: str) -> bool:
    """
    Garbage for AIR content, scoped ONLY to lines outside any [Begin Action N]
    block - a block's body absorbs everything up to the next header, so the
    only "outside" region is the preamble before the first action. Frame/
    element syntax inside a block is deliberately never parsed or judged.
    """
    if raw.strip() == '':
        return False
    if is_comment_line(raw):
        return False
    if is_any_header_shape(raw):
        return False
    return True

def is_air_element_garbage_line(raw: str) -> bool:
    """
    Garbage INSIDE a [Begin Action N] block body. A body line is recognized
    structure if it is blank, a ';' comment, a frame or coordinate row
    (starts with a digit or '-'; field count is not judged), a Clsn line,
    Loopstart, or Interpolate offset/scale/angle/blend.

    Clsn: official Elecbyte form is `Clsn1:` / `Clsn2:` / `Clsn1Default:` /
    `Clsn2Default:` plus `Clsn2[0] = x1,y1, x2,y2` — the `clsn` prefix
    covers both the declaration and the boxed-index assignment.
    """
    if raw.strip() == '':
        return False
    # AIR element lines are plain numeric lists: split on first ';' only.
    stripped = raw.split(';', 1)[0].strip()
    if stripped == '':
        return False
    if is_any_header_shape(raw):
        return False
    lower = stripped.lower()
    if lower.startswith('clsn'):
        return False
    if lower.startswith('loopstart'):
        return False
    if (lower.startswith('interpolate offset') or lower.startswith('interpolate scale')
            or lower.startswith('interpolate angle') or lower.startswith('interpolate blend')):
        return False
    first_char = stripped[0]
    if first_char.isdigit() or first_char == '-':
        return False
    return True

def is_block_header(line: str) -> bool:
    stripped = strip_comment(line).lower()
    if not stripped.startswith('[') or not stripped.endswith(']'):
        return False
    inner = stripped[1:-1].strip()
    return inner.startswith('statedef') or inner.startswith('state')

def is_comment_line(line: str) -> bool:
    return line.lstrip().startswith(';')

def parse_statedef_number(header: str) -> Optional[str]:
    """Extract the state number from a [Statedef ...] header."""
    clean = strip_comment(header)
    match = re.search(r'statedef\s+([+-]?\d+)', clean, re.IGNORECASE)
    return match.group(1) if match else None

def split_off_comment_verbatim(line: str) -> Tuple[str, str]:
    """
    Like strip_comment, but preserves exact whitespace on both sides -
    returns (before, comment) where comment includes the leading ';' and
    before includes any whitespace that was between the content and it.
    Used so header rewriting never touches cosmetic comment spacing.
    """
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i-1] != '\\'):
            in_quote = not in_quote
        elif not in_quote and ch == ';':
            return line[:i], line[i:]
    return line, ''

def normalize_state_header_case(header: str) -> Tuple[str, bool]:
    """Normalise [state ...] -> [State ...], preserving trailing comment verbatim."""
    before, comment = split_off_comment_verbatim(header)
    leading_ws = before[:len(before) - len(before.lstrip())]
    trailing_ws = before[len(before.rstrip()):]
    core = before.strip()

    if not core.startswith('[') or not core.endswith(']'):
        return header, False
    inner = core[1:-1].strip()
    if not inner.lower().startswith('state'):
        return header, False

    new_inner = re.sub(r'^(state)(\s|,|$)', r'State\2', inner, flags=re.IGNORECASE)
    if new_inner == inner:
        return header, False
    new_header = leading_ws + f"[{new_inner}]" + trailing_ws + comment
    return new_header, True

def rewrite_state_header(header: str, statedef_no: str) -> Tuple[str, bool]:
    """
    Replace the first numeric part with statedef_no, or insert statedef_no
    if the first part is a label. Returns (new_header, changed).
    """
    header, case_changed = normalize_state_header_case(header)
    before, comment = split_off_comment_verbatim(header)
    leading_ws = before[:len(before) - len(before.lstrip())]
    trailing_ws = before[len(before.rstrip()):]
    core = before.strip()

    if not core.startswith('[') or not core.endswith(']'):
        return header, case_changed
    inner = core[1:-1].strip()
    if not inner.lower().startswith('state'):
        return header, case_changed

    rest = inner[5:].strip()
    if not rest:
        new_inner = f"State {statedef_no}"
    else:
        parts = [p.strip() for p in rest.split(',') if p.strip()]
        if not parts:
            new_inner = f"State {statedef_no}"
        else:
            first = parts[0]
            if re.match(r'^[+-]?\d+$', first):
                # Always rebuild cleanly (drops stray trailing commas, extra
                # whitespace, etc.) even when the number already matches -
                # comparison against the original happens below.
                parts[0] = statedef_no
                new_inner = f"State {', '.join(parts)}"
            else:
                # Label: insert statedef_no as first part
                parts = [statedef_no] + parts
                new_inner = f"State {', '.join(parts)}"

    new_header = leading_ws + f"[{new_inner}]" + trailing_ws + comment
    if new_header != header:
        return new_header, True
    return header, case_changed

# ----------------------------------------------------------------------
# .def file discovery - find a character's state (.cns-like) files
# ----------------------------------------------------------------------

# Matches the [Files] section keys treated as state files: 'st', 'st0',
# 'st1', ... 'cmd' is included too — .cmd files also contain [State ...]
# blocks and get the same pruning. 'stcommon' and 'cns' are intentionally
# excluded: stcommon is typically a shared file used by many characters, not
# something to auto-touch per-character, and 'cns' is the constants file
# ([Data]/[Size]/etc), not state controllers.
ST_KEY_RE = re.compile(r'^st[0-9]*$')

def parse_ini_sections(content: str) -> Dict[str, Dict[str, str]]:
    """Minimal INI parser: {section_name_lower: {key_lower: value}}."""
    sections: Dict[str, Dict[str, str]] = {}
    current: Optional[str] = None
    for raw in split_lines(content):
        stripped = strip_comment(raw).strip()
        if not stripped:
            continue
        if stripped.startswith('[') and stripped.endswith(']'):
            current = stripped[1:-1].strip().lower()
            sections.setdefault(current, {})
            continue
        if current is None:
            continue
        key, value = split_key_value(stripped)
        if key is not None:
            sections[current][key.strip().lower()] = (value or "").strip().strip('"')

    return sections

def read_def_text(def_path: str) -> str:
    """Read-only decode; file names in a .def may use the system ANSI code page."""
    with open(def_path, 'rb') as f:
        data = f.read()
    for encoding in ('utf-8-sig', locale.getpreferredencoding(False)):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('latin-1')


def is_ikemen_def(def_path: str) -> bool:
    """Return whether a character .def declares an Ikemen engine version."""
    sections = parse_ini_sections(read_def_text(def_path))
    return 'ikemenversion' in sections.get('info', {})


def discover_state_files_from_def(def_path: str) -> List[str]:
    """
    Parse a character's .def file and return the state (.cns-like) file
    paths it references - 'st'/'st0'/'st1'/... and 'cmd' - resolved
    relative to the .def's folder, in the engine's compile order (numbered
    st files, then cmd). 'stcommon' is deliberately excluded. Duplicates
    (e.g. two keys pointing at the same file) are only included once.
    """
    sections = parse_ini_sections(read_def_text(def_path))
    files_section = sections.get('files', {})
    base_dir = os.path.dirname(os.path.abspath(def_path))

    numbered = [(k, v) for k, v in files_section.items() if ST_KEY_RE.match(k) and v]

    def sort_key(item):
        digits = item[0][2:]  # text after 'st'
        return (0, -1) if digits == '' else (1, int(digits))

    numbered.sort(key=sort_key)
    rel_paths = [v for _, v in numbered]

    cmd = files_section.get('cmd', '')
    if cmd:
        rel_paths.append(cmd)

    resolved = []
    seen = set()
    for p in rel_paths:
        if not p:
            continue
        full = p if os.path.isabs(p) else os.path.normpath(os.path.join(base_dir, p))
        if full not in seen:
            seen.add(full)
            resolved.append(full)
    return resolved

def discover_air_file_from_def(def_path: str) -> Optional[str]:
    """
    Parse a character's .def file and return its 'anim' (.air) file path,
    resolved relative to the .def's folder, or None if not present.
    """
    sections = parse_ini_sections(read_def_text(def_path))
    files_section = sections.get('files', {})
    anim = files_section.get('anim', '')
    if not anim:
        return None
    base_dir = os.path.dirname(os.path.abspath(def_path))
    return anim if os.path.isabs(anim) else os.path.normpath(os.path.join(base_dir, anim))

# ----------------------------------------------------------------------
# Main processing - single pass, one ordered list of lines per block
# ----------------------------------------------------------------------

LINTER_TAG_NAMES = (
    'CNS Unknown Parameter', 'CNS Invalid Value', 'CNS Duplicate Parameter',
    'CNS Garbage Line', 'CMD Unknown Parameter', 'CMD Duplicate Parameter',
    'AIR Duplicate Action', 'AIR Garbage Line',
)
LINTER_TAG_RE = re.compile(
    r'^[ \t]*;[ \t]*\[(?:'
    + '|'.join(re.escape(tag) for tag in LINTER_TAG_NAMES)
    + r')\](?:[ \t]+|(?=\r?$))',
    re.IGNORECASE | re.MULTILINE,
)


def remove_tagged_lines(content: str) -> Tuple[str, int]:
    """Delete lines carrying one of this tool's recognized tags."""
    lines = content.split('\n')
    kept_lines = [line for line in lines if not LINTER_TAG_RE.match(line)]
    return '\n'.join(kept_lines), len(lines) - len(kept_lines)


def comment_out(raw: str, tag: Optional[str] = None) -> str:
    """Turn any line into an inert ';' comment, preserving its text. If tag
    is given (removal_mode == 'tag'), embed it right after the ';' - same
    bracketed text as the matching log entry, for consistency."""
    body = raw.strip()
    return f"; [{tag}] {body}" if tag else f"; {body}"

def process_file(content: str, do_prune: bool, do_dedupe: bool, do_headers: bool,
                  do_garbage_lines: bool, removal_mode: str = 'comment',
                  do_check_values: bool = False
                  ) -> Tuple[str, List[str], Dict[str, int]]:
    """
    removal_mode: 'delete' (drop flagged lines entirely), 'comment' (turn
    them into plain ';' comments), or 'tag' (comment out AND prefix with the
    same bracketed tag used in the log, e.g. '; [CNS Garbage Line] ...').
    Applies uniformly to pruned parameters, duplicate parameters, invalid
    enum values, and garbage lines.
    do_garbage_lines: master on/off for the garbage-line fixer (any line
    that isn't blank, a real comment, a header, or a well-formed key=value
    pair). When on, every detected line is handled per removal_mode - never
    left as-is.
    do_check_values: flag/remove keys whose value isn't in the per-type
    enum table for that key (only checked when the key itself is valid
    for the type).
    """
    validate_removal_mode(removal_mode)
    lines = split_lines(content)
    new_lines: List[str] = []
    removed_log: List[str] = []
    stats = {
        'pruned': 0,
        'duplicates_removed': 0,
        'headers_normalized': 0,
        'garbage_lines_handled': 0,
        'invalid_values_removed': 0,
    }
    verb = "commented out" if removal_mode in ('comment', 'tag') else "deleted"

    in_block = False
    block_header = ""
    header_line = ""
    block_lines: List[Tuple[Optional[str], str, int]] = []  # (key, raw, lineno)
    ctrl_type: Optional[str] = None
    is_statedef_block = False
    current_statedef_no: Optional[str] = None

    def flush_block():
        # --- 1. Pruning: which line indices are invalid params? Statedef blocks
        #        validate against the fixed 'statedef' param set; State controller
        #        blocks validate against SCTRL_PARAMS[ctrl_type] (from 'type='). ---
        invalid_indices: Set[int] = set()
        if do_prune:
            if is_statedef_block:
                valid_params = SCTRL_PARAMS.get('statedef')
            elif ctrl_type in SKIP_PRUNE_TYPES:
                # type=null: disable a controller while keeping its original
                # params (MUGEN ignores them). type=zoom: 1.1 beta, param
                # list never properly documented — accept anything for now.
                # valid_params=None skips pruning. Don't use the {} entries
                # in SCTRL_PARAMS for validation.
                valid_params = None
            elif ctrl_type and ctrl_type in SCTRL_PARAMS:
                valid_params = SCTRL_PARAMS[ctrl_type]
            else:
                valid_params = None
            if valid_params is not None:
                for idx, (key, raw, lineno) in enumerate(block_lines):
                    if (key is not None and key.lower() not in valid_params
                            and not is_always_allowed(key)
                            and not is_var_shorthand_allowed(key.lower(), ctrl_type)):
                        invalid_indices.add(idx)

        # --- 2. Invalid values: key is valid for the type, but its value
        #        isn't allowed for that (type, key) - a dead line, same as an
        #        unknown key, just wrong on the value side instead. ---
        invalid_value_indices: Set[int] = set()
        if do_check_values:
            type_for_enum = 'statedef' if is_statedef_block else ctrl_type
            for idx, (key, raw, lineno) in enumerate(block_lines):
                if idx in invalid_indices or key is None:
                    continue
                allowed = enum_allowed_values(key, type_for_enum)
                if allowed is None:
                    continue
                _, value = split_key_value(strip_comment(raw))
                if value is not None and value.strip().lower() not in allowed:
                    invalid_value_indices.add(idx)

        # --- 3. Dedupe: for each key (excluding triggers), keep only the FIRST
        #        surviving occurrence — later duplicates are dead. On var-family
        #        controllers, v/fv/var()/fvar()/sysvar()/sysfvar() all pick ONE
        #        target — group them so a second target under a different
        #        spelling still counts as a duplicate.
        duplicate_indices: Set[int] = set()
        first_index_for_key: Dict[str, int] = {}
        VAR_TARGET_GROUP = '\0vartarget'
        if do_dedupe:
            def dedupe_key(key: str) -> str:
                if ctrl_type in VAR_SHORTHAND_TYPES and is_var_target_key(key):
                    return VAR_TARGET_GROUP
                return key.lower()

            def skip(idx, key):
                return (idx in invalid_indices or idx in invalid_value_indices
                        or key is None or is_trigger_key(key))

            for idx, (key, raw, lineno) in enumerate(block_lines):
                if skip(idx, key):
                    continue
                dk = dedupe_key(key)
                if dk not in first_index_for_key:
                    first_index_for_key[dk] = idx
            for idx, (key, raw, lineno) in enumerate(block_lines):
                if skip(idx, key):
                    continue
                dk = dedupe_key(key)
                if first_index_for_key[dk] != idx:
                    duplicate_indices.add(idx)

        # --- 4. Emit ---
        new_lines.append(header_line)
        sctrl_display = 'Statedef' if is_statedef_block else (ctrl_type or 'unknown type')
        for idx, (key, raw, lineno) in enumerate(block_lines):
            if idx in invalid_indices:
                stats['pruned'] += 1
                removed_log.append(
                    f"Line {lineno}: [CNS Unknown Parameter] {verb} unknown '{key}' "
                    f"(type={sctrl_display}) from {block_header}: {raw.strip()}"
                )
                if removal_mode in ('comment', 'tag'):
                    tag = 'CNS Unknown Parameter' if removal_mode == 'tag' else None
                    new_lines.append(comment_out(raw, tag))
                continue
            if idx in invalid_value_indices:
                stats['invalid_values_removed'] += 1
                removed_log.append(
                    f"Line {lineno}: [CNS Invalid Value] {verb} invalid value for '{key}' "
                    f"(type={sctrl_display}) from {block_header}: {raw.strip()}"
                )
                if removal_mode in ('comment', 'tag'):
                    tag = 'CNS Invalid Value' if removal_mode == 'tag' else None
                    new_lines.append(comment_out(raw, tag))
                continue
            if idx in duplicate_indices:
                stats['duplicates_removed'] += 1
                removed_log.append(
                    f"Line {lineno}: [CNS Duplicate Parameter] {verb} duplicate '{key}' from "
                    f"{block_header}: {raw.strip()}"
                )
                if removal_mode in ('comment', 'tag'):
                    tag = 'CNS Duplicate Parameter' if removal_mode == 'tag' else None
                    new_lines.append(comment_out(raw, tag))
                continue
            new_lines.append(raw)

    i = 0
    while i < len(lines):
        raw = lines[i]
        line_num = i + 1

        is_garbage = is_garbage_line(raw)

        if is_garbage and not do_garbage_lines:
            # Toggle is off - leave it fully untouched, and never let prune/dedupe
            # downstream extract a garbled "key" from it.
            if in_block:
                block_lines.append((None, raw, line_num))
            else:
                new_lines.append(raw)
            i += 1
            continue

        if is_garbage:  # do_garbage_lines is True here
            stats['garbage_lines_handled'] += 1
            removed_log.append(
                f"Line {line_num}: [CNS Garbage Line] {verb} garbage line: {raw.strip()}"
            )
            if removal_mode in ('comment', 'tag'):
                raw = comment_out(raw, 'CNS Garbage Line' if removal_mode == 'tag' else None)
            else:
                i += 1
                continue

        if in_block and is_any_header_shape(raw):
            # Any section header ends the block ([Command] after [State -1] etc.).
            flush_block()
            in_block = False
            block_lines = []
            ctrl_type = None
            is_statedef_block = False
            continue

        if is_block_header(raw):
            in_block = True
            clean_header = strip_comment(raw)[1:-1].strip()
            block_header = clean_header
            header_line = raw
            is_statedef_block = clean_header.lower().startswith('statedef')

            if do_headers:
                if is_statedef_block:
                    num = parse_statedef_number(clean_header)
                    current_statedef_no = num if num is not None else None
                    # Statedef headers themselves are left untouched, only their
                    # number is captured for renumbering [State ...] sub-headers.
                else:
                    if current_statedef_no is not None:
                        new_header, changed = rewrite_state_header(raw, current_statedef_no)
                        if changed:
                            stats['headers_normalized'] += 1
                            # Deliberately not logged per-line - this can fire
                            # thousands of times on a real file; the summary
                            # count is what's useful, not a wall of before/after.
                            header_line = new_header

            block_lines = []
            ctrl_type = None
            i += 1
            continue

        if in_block:
            if is_comment_line(raw):
                block_lines.append((None, raw, line_num))
            else:
                stripped = strip_comment(raw)
                if '=' in stripped:
                    key, value = split_key_value(stripped)
                    block_lines.append((key, raw, line_num))
                    if key is not None and key.lower() == 'type' and ctrl_type is None:
                        ctrl_type = value.strip().lower()
                else:
                    block_lines.append((None, raw, line_num))
            i += 1
            continue

        # Outside any block
        new_lines.append(raw)
        i += 1

    if in_block:
        flush_block()

    output = '\n'.join(new_lines)
    if output and not output.endswith('\n'):
        output += '\n'
    return output, removed_log, stats

# ----------------------------------------------------------------------
# CMD processing - a .cmd file's own [Command]/[Remap]/[Defaults] sections.
# The [State -1, ...]/[Statedef -1] part of a .cmd file is handled entirely
# by process_file() above (same CNS state-controller engine); this function
# only ever touches Command/Remap/Defaults blocks and leaves everything
# else - including State/Statedef content - completely untouched, so the
# two fixers can be chained safely over the same file.
# ----------------------------------------------------------------------

# Official MUGEN CMD keys only. [Defaults] sets fallback values (command.time,
# command.buffer.time) for the matching per-[Command] keys (time, buffer.time).
CMD_SECTION_PARAMS: Dict[str, Set[str]] = {
    'command': {
        'name', 'command', 'time', 'buffer.time',
    },
    'remap': {
        'x', 'y', 'z', 'a', 'b', 'c', 's',
    },
    'defaults': {
        'command.time', 'command.buffer.time',
    },
}

def cmd_section_kind(line: str) -> Optional[str]:
    """
    Returns 'command', 'remap', or 'defaults' if this header is one of the
    .cmd file's own section types, else None. Any section name starting
    with "command" counts, not just the exact literal "[Command]".
    """
    stripped = strip_comment(line).lower()
    if not stripped.startswith('[') or not stripped.endswith(']'):
        return None
    inner = stripped[1:-1].strip()
    if inner.startswith('command'):
        return 'command'
    if inner == 'remap':
        return 'remap'
    if inner == 'defaults':
        return 'defaults'
    return None

def process_cmd_file(content: str, do_prune: bool, do_dedupe: bool,
                      removal_mode: str = 'comment'
                      ) -> Tuple[str, List[str], Dict[str, int]]:
    """
    do_prune: remove parameters not valid for a Command/Remap/Defaults block.
    do_dedupe: remove duplicate parameters WITHIN a single block (keeps
        FIRST, matching the engine's usual first-wins INI behavior).
        Deliberately does NOT deduplicate [Command] blocks that share the
        same 'name' across different blocks - the format explicitly allows
        (and real character files rely on) multiple commands sharing a name
        to define alternate motions for the same move; the engine adds
        every one of them rather than keeping just one.
    """
    validate_removal_mode(removal_mode)
    lines = split_lines(content)
    new_lines: List[str] = []
    log: List[str] = []
    stats = {'cmd_pruned': 0, 'cmd_duplicates_removed': 0}
    verb = "commented out" if removal_mode in ('comment', 'tag') else "deleted"

    in_block = False
    kind: Optional[str] = None
    header = ""
    block_lines: List[Tuple[Optional[str], str, int]] = []

    def flush_block():
        invalid_indices: Set[int] = set()
        if do_prune and kind in CMD_SECTION_PARAMS:
            valid_params = CMD_SECTION_PARAMS[kind]
            for idx, (key, raw, lineno) in enumerate(block_lines):
                if key is not None and key.lower() not in valid_params:
                    invalid_indices.add(idx)

        duplicate_indices: Set[int] = set()
        first_index_for_key: Dict[str, int] = {}
        if do_dedupe:
            for idx, (key, raw, lineno) in enumerate(block_lines):
                if idx in invalid_indices or key is None:
                    continue
                if key.lower() not in first_index_for_key:
                    first_index_for_key[key.lower()] = idx
            for idx, (key, raw, lineno) in enumerate(block_lines):
                if idx in invalid_indices or key is None:
                    continue
                if first_index_for_key[key.lower()] != idx:
                    duplicate_indices.add(idx)

        section_label = f"[{kind.capitalize()}]" if kind else "[?]"
        new_lines.append(header)
        for idx, (key, raw, lineno) in enumerate(block_lines):
            if idx in invalid_indices:
                stats['cmd_pruned'] += 1
                log.append(
                    f"Line {lineno}: [CMD Unknown Parameter] {verb} unknown '{key}' from "
                    f"{section_label}: {raw.strip()}"
                )
                if removal_mode in ('comment', 'tag'):
                    tag = 'CMD Unknown Parameter' if removal_mode == 'tag' else None
                    new_lines.append(maybe_comment_out(raw, tag))
                continue
            if idx in duplicate_indices:
                stats['cmd_duplicates_removed'] += 1
                log.append(
                    f"Line {lineno}: [CMD Duplicate Parameter] {verb} duplicate '{key}' from "
                    f"{section_label}: {raw.strip()}"
                )
                if removal_mode in ('comment', 'tag'):
                    tag = 'CMD Duplicate Parameter' if removal_mode == 'tag' else None
                    new_lines.append(maybe_comment_out(raw, tag))
                continue
            new_lines.append(raw)

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line_num = i + 1
        header_kind = cmd_section_kind(raw)

        if in_block and is_any_header_shape(raw):
            flush_block()
            in_block = False
            block_lines = []
            kind = None

        if header_kind is not None:
            in_block = True
            kind = header_kind
            header = raw
            block_lines = []
            i += 1
            continue

        if in_block:
            if is_comment_line(raw):
                block_lines.append((None, raw, line_num))
            else:
                stripped = strip_comment(raw)
                if '=' in stripped:
                    key, _ = split_key_value(stripped)
                    block_lines.append((key, raw, line_num))
                else:
                    block_lines.append((None, raw, line_num))
            i += 1
            continue

        # Outside any Command/Remap/Defaults block - includes all
        # State/Statedef content, which process_file() handles separately.
        new_lines.append(raw)
        i += 1

    if in_block:
        flush_block()

    output = '\n'.join(new_lines)
    if output and not output.endswith('\n'):
        output += '\n'
    return output, log, stats

# ----------------------------------------------------------------------
# AIR processing - [Begin Action N] sections
# ----------------------------------------------------------------------

# Matches "[Begin Action N]" (optionally with an inline ';' comment, which
# strip_comment removes before matching). Case-insensitive, tolerant of
# extra whitespace, matching the engine's own loose "begin "/"action " check.
ACTION_HEADER_RE = re.compile(r'^\[\s*begin\s+action\s+([+-]?\d+)', re.IGNORECASE)

def is_action_header(line: str) -> bool:
    return ACTION_HEADER_RE.match(strip_comment(line)) is not None

def parse_action_number(line: str) -> Optional[int]:
    m = ACTION_HEADER_RE.match(strip_comment(line))
    return int(m.group(1)) if m else None

def maybe_comment_out(line: str, tag: Optional[str] = None) -> str:
    """Comment out a line, unless it's already blank or already a comment.
    tag: see comment_out()."""
    if not line.strip():
        return line
    if line.lstrip().startswith(';'):
        return line
    body = line.strip()
    return f"; [{tag}] {body}" if tag else f"; {body}"

def parse_action_blocks(lines: List[str]) -> List[Dict]:
    """
    Split lines into a flat, ordered list of Begin Action blocks:
    {header_line_num, no, header, body (list[str]), has_content}.
    Lines before the first header are ignored here (caller handles pass-through).
    """
    blocks: List[Dict] = []
    i, n = 0, len(lines)
    while i < n:
        if is_action_header(lines[i]):
            header_line_num = i + 1
            header = lines[i]
            no = parse_action_number(lines[i])
            i += 1
            body: List[str] = []
            while i < n and not is_action_header(lines[i]):
                body.append(lines[i])
                i += 1
            has_content = any(ln.strip() and not ln.lstrip().startswith(';') for ln in body)
            blocks.append({
                'header_line_num': header_line_num, 'no': no, 'header': header,
                'body': body, 'has_content': has_content,
            })
        else:
            i += 1
    return blocks

def count_air_garbage_lines(content: str) -> int:
    """
    0 if the file has no [Begin Action N] blocks at all (preserves "no AIR
    content -> no change" rather than flagging an entire non-AIR file as
    garbage). Otherwise counts preamble garbage (is_air_garbage_line) plus
    in-block garbage (is_air_element_garbage_line) across every block body.
    """
    lines = split_lines(content)
    blocks = parse_action_blocks(lines)
    if not blocks:
        return 0
    first_header_line = blocks[0]['header_line_num']
    count = sum(1 for line in lines[:first_header_line - 1] if is_air_garbage_line(line))
    for block in blocks:
        count += sum(1 for line in block['body'] if is_air_element_garbage_line(line))
    return count

def resolve_fallthrough(blocks: List[Dict]
                        ) -> Tuple[Dict[int, Optional[List[str]]], Dict[int, int]]:
    """
    Resolves empty-action fallthrough over the already-split block list.
    An empty (non-duplicate) action adopts the next action's resolved body.
    Returns:
      resolved: {action_no: body_lines_or_None} - final content per FIRST
                 occurrence of each number (None means it stayed empty).
      source_block_index: {action_no: index into `blocks` of whichever block's
                 raw body was ultimately used} - only set when resolved via
                 fallthrough to a *different* block, for logging.
    """
    resolved: Dict[int, Optional[List[str]]] = {}
    source_block_index: Dict[int, int] = {}

    def resolve_at(pos: int) -> Tuple[Optional[List[str]], int]:
        if pos >= len(blocks):
            return None, pos
        block = blocks[pos]
        no = block['no']
        if no in resolved:
            # Duplicate: its own body is discarded, whatever was already
            # known for this number is used instead (matches "return existing").
            return resolved[no], pos + 1
        if block['has_content']:
            resolved[no] = block['body']
            return block['body'], pos + 1
        # Empty and not a duplicate: try exactly one step forward - that
        # step's own resolution (which may itself cascade) is adopted as-is.
        if pos + 1 < len(blocks):
            inner_body, next_pos = resolve_at(pos + 1)
            resolved[no] = inner_body
            if inner_body is not None:
                for idx, b in enumerate(blocks):
                    if b['body'] is inner_body:
                        source_block_index[no] = idx
                        break
            return inner_body, next_pos
        resolved[no] = None
        return None, pos + 1

    pos = 0
    while pos < len(blocks):
        _, pos = resolve_at(pos)

    return resolved, source_block_index

def process_air_file(content: str, do_dedupe_actions: bool, do_bake_fallthrough: bool,
                      do_flag_empty: bool, do_garbage_lines: bool, removal_mode: str = 'comment'
                      ) -> Tuple[str, List[str], Dict[str, int]]:
    """
    do_dedupe_actions: remove/comment later [Begin Action N] blocks that
        repeat a number already declared earlier in the file.
    do_bake_fallthrough: for empty (non-duplicate) [Begin Action N] blocks,
        physically copy in whatever content the engine's runtime fallthrough
        would have used, so the file no longer relies on that mechanism.
    do_flag_empty: log any block that's still empty after baking (or if
        baking is off) - informational only, never modifies content.
    do_garbage_lines: handle stray lines both before the first [Begin
        Action N] header (is_air_garbage_line) and inside each block's body
        (is_air_element_garbage_line). Baked-in fallthrough bodies are
        scanned too. No-op if the file has no action blocks at all.
    removal_mode: 'delete', 'comment', or 'tag' (comment + bracketed tag).
    """
    validate_removal_mode(removal_mode)
    lines = split_lines(content)
    verb = "commented out" if removal_mode in ('comment', 'tag') else "deleted"
    log: List[str] = []
    stats = {'duplicate_actions_removed': 0, 'empty_actions_baked': 0,
              'empty_actions_flagged': 0, 'air_garbage_lines_handled': 0}

    blocks = parse_action_blocks(lines)
    resolved, source_block_index = resolve_fallthrough(blocks) if do_bake_fallthrough else ({}, {})
    first_header_line = blocks[0]['header_line_num'] if blocks else None

    seen_numbers = set()
    new_lines: List[str] = []
    i = 0
    n = len(lines)
    block_iter = iter(blocks)
    current_block = next(block_iter, None)

    while i < n:
        raw = lines[i]
        line_num = i + 1

        if current_block is not None and line_num == current_block['header_line_num']:
            block = current_block
            current_block = next(block_iter, None)
            no = block['no']
            block_len = 1 + len(block['body'])
            i += block_len

            is_duplicate = (do_dedupe_actions and no is not None and no in seen_numbers)
            if no is not None:
                seen_numbers.add(no)

            if is_duplicate:
                stats['duplicate_actions_removed'] += 1
                log.append(
                    f"Line {block['header_line_num']}: [AIR Duplicate Action] {verb} "
                    f"duplicate Begin "
                    f"Action {no} ({block_len} line(s))"
                )
                if removal_mode in ('comment', 'tag'):
                    tag = 'AIR Duplicate Action' if removal_mode == 'tag' else None
                    new_lines.append(maybe_comment_out(block['header'], tag))
                    new_lines.extend(maybe_comment_out(ln, tag) for ln in block['body'])
                continue

            is_empty = not block['has_content']
            baked = False
            body_out = block['body']
            body_start_line = block['header_line_num'] + 1
            src_no = None
            if is_empty and do_bake_fallthrough:
                source_lines = resolved.get(no)
                if source_lines is not None:
                    src_idx = source_block_index.get(no)
                    src_no = blocks[src_idx]['no'] if src_idx is not None else None
                    src_line = blocks[src_idx]['header_line_num'] if src_idx is not None else None
                    stats['empty_actions_baked'] += 1
                    log.append(
                        f"Line {block['header_line_num']}: [AIR Baked Fallthrough] Begin Action "
                        f"{no} was empty - baked in the contents of Begin Action {src_no} "
                        f"(line {src_line})"
                    )
                    body_out = source_lines
                    body_start_line = (src_line + 1) if src_line is not None else body_start_line
                    baked = True

            if is_empty and not baked and do_flag_empty:
                stats['empty_actions_flagged'] += 1
                if i < n:
                    note = ("it will inherit whichever action is declared NEXT in this file")
                else:
                    note = ("it's the last action in the file, so there's nothing "
                            "to fall through to - it will remain empty")
                log.append(
                    f"Line {block['header_line_num']}: [AIR Empty Action] Begin Action "
                    f"{no} is empty - {note}"
                )

            new_lines.append(block['header'])
            for b_idx, b_line in enumerate(body_out):
                b_line_num = body_start_line + b_idx
                if do_garbage_lines and is_air_element_garbage_line(b_line):
                    stats['air_garbage_lines_handled'] += 1
                    baked_note = f" (baked from Action {src_no})" if baked else ""
                    log.append(f"Line {b_line_num}: [AIR Garbage Line] {verb} unrecognized "
                               f"line in Begin Action {no}{baked_note}: {b_line.strip()}")
                    if removal_mode in ('comment', 'tag'):
                        tag = 'AIR Garbage Line' if removal_mode == 'tag' else None
                        new_lines.append(maybe_comment_out(b_line, tag))
                else:
                    new_lines.append(b_line)
            continue

        if (do_garbage_lines and first_header_line is not None
                and line_num < first_header_line and is_air_garbage_line(raw)):
            stats['air_garbage_lines_handled'] += 1
            log.append(f"Line {line_num}: [AIR Garbage Line] {verb} stray line before first "
                       f"Begin Action: {raw.strip()}")
            if removal_mode in ('comment', 'tag'):
                tag = 'AIR Garbage Line' if removal_mode == 'tag' else None
                new_lines.append(maybe_comment_out(raw, tag))
            i += 1
            continue

        new_lines.append(raw)
        i += 1

    output = '\n'.join(new_lines)
    if output and not output.endswith('\n'):
        output += '\n'
    return output, log, stats

# ----------------------------------------------------------------------
# Entry point - unified CLI, dispatches by known .def role, falling back to
# file extension only for files the user added directly (not via a .def)
# ----------------------------------------------------------------------

AIR_EXTENSIONS = {'.air'}
REMOVAL_MODES = frozenset({'delete', 'comment', 'tag'})


def validate_removal_mode(removal_mode: str) -> None:
    """Reject invalid removal modes even when Python assertions are disabled."""
    if removal_mode not in REMOVAL_MODES:
        allowed = ', '.join(sorted(REMOVAL_MODES))
        raise ValueError(f"removal_mode must be one of: {allowed}")


def is_air_file(path: str) -> bool:
    """Extension-based guess - only a fallback for files not discovered via
    a .def, whose [Files] section is the authoritative source of truth."""
    return os.path.splitext(path)[1].lower() in AIR_EXTENSIONS

def classify_file(path: str, known_type: Optional[str] = None) -> str:
    """
    Returns 'cns' or 'air'. Prefers a known_type (set when a file's role came
    from a .def's [Files] section - authoritative regardless of extension)
    over guessing from the file extension.
    """
    if known_type in ('cns', 'air'):
        return known_type
    return 'air' if is_air_file(path) else 'cns'

def ask_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            resp = input(prompt + suffix).strip().lower()
        except EOFError:  # piped / no console: take the default
            print()
            return default
        if resp == "":
            return default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        print("Please answer y or n.")

def ask_choice(prompt: str, options: Dict[str, str], default: str) -> str:
    """options: {key: label}. User types a key; blank input picks default."""
    lines = [prompt]
    for key, label in options.items():
        marker = " (default)" if key == default else ""
        lines.append(f"  [{key}] {label}{marker}")
    lines.append("> ")
    full_prompt = "\n".join(lines)
    while True:
        try:
            resp = input(full_prompt).strip().lower()
        except EOFError:
            print()
            return default
        if resp == "":
            return default
        if resp in options:
            return resp
        print("Please enter one of: " + ", ".join(options))

def expand_inputs(paths: List[str]) -> Tuple[List[str], Dict[str, str], int]:
    """
    Expand any .def file in the list into its discovered state + air files.
    Returns (ordered_paths, known_types), where known_types maps a path to
    'cns' or 'air' ONLY for files whose role came from a .def's [Files]
    section (authoritative regardless of extension); paths not in the dict
    fall back to extension-based detection in classify_file(). The third
    value counts .def files that couldn't be read.
    """
    expanded: List[str] = []
    known_types: Dict[str, str] = {}
    failed_defs = 0
    for p in paths:
        if p.lower().endswith('.def'):
            try:
                state_files = [f for f in discover_state_files_from_def(p) if os.path.isfile(f)]
                air_file = discover_air_file_from_def(p)
            except OSError as e:
                print(f"Error: couldn't read '{p}': {e.strerror or e}")
                failed_defs += 1
                continue
            print(f"Discovered from '{p}':")
            for f in state_files:
                print(f"  {f}")
                expanded.append(f)
                known_types[f] = 'cns'
            if air_file and os.path.isfile(air_file):
                print(f"  {air_file}")
                expanded.append(air_file)
                known_types[air_file] = 'air'
            elif air_file:
                print(f"  (anim file '{air_file}' listed but not found on disk - skipped)")
        else:
            expanded.append(p)
    return dedupe_paths(expanded), known_types, failed_defs

# Non-UTF-8 bytes (Shift-JIS, cp1252) become lone surrogates: never whitespace,
# never line breaks, and they encode back to the exact original bytes.
FILE_ERRORS = 'surrogateescape'


class TextFile(NamedTuple):
    """Decoded text (LF line endings) plus what's needed to write it back as-is."""
    text: str
    bom: bool
    newline: str   # '\r\n', '\n' or '\r'


def read_text_file(path: str) -> TextFile:
    """Never drops bytes; see FILE_ERRORS."""
    with open(path, 'rb') as f:
        data = f.read()
    bom = data.startswith(codecs.BOM_UTF8)
    if bom:
        data = data[len(codecs.BOM_UTF8):]
    text = data.decode('utf-8', FILE_ERRORS)
    if '\r\n' in text:
        newline = '\r\n'
    elif '\r' in text and '\n' not in text:
        newline = '\r'
    else:
        newline = '\n'
    text = text.replace('\r\n', '\n')
    if newline == '\r':
        text = text.replace('\r', '\n')
    return TextFile(text, bom, newline)


def encode_text(source: TextFile, text: str) -> bytes:
    """Encode text with the source file's BOM and line endings."""
    if source.newline != '\n':
        text = text.replace('\n', source.newline)
    data = text.encode('utf-8', FILE_ERRORS)
    return codecs.BOM_UTF8 + data if source.bom else data


def printable(text: str) -> str:
    """Show undecodable bytes as U+FFFD so printing / Tk never chokes on them."""
    return text.encode('utf-8', FILE_ERRORS).decode('utf-8', 'replace')


def same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def dedupe_paths(paths: List[str]) -> List[str]:
    """Drop repeats of the same file under different spellings; keeps first."""
    seen: Set[str] = set()
    result = []
    for p in paths:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def plan_output_paths(input_files: List[str], output_dir: Optional[str]) -> Dict[str, str]:
    """Map inputs to outputs. In folder mode, clashing file names go into a
    subfolder named after their source folder (numbered if that clashes too)."""
    if not output_dir:
        return {p: p for p in input_files}
    name_counts = Counter(os.path.normcase(os.path.basename(p)) for p in input_files)
    used: Set[str] = set()
    result = {}
    for p in input_files:
        base = os.path.basename(p)
        if name_counts[os.path.normcase(base)] > 1:
            parent = os.path.basename(os.path.dirname(os.path.abspath(p))) or 'root'
            rel = os.path.join(parent, base)
        else:
            rel = base
        candidate, n = rel, 2
        while os.path.normcase(candidate) in used:
            stem, ext = os.path.splitext(rel)
            candidate, n = f"{stem}_{n}{ext}", n + 1
        used.add(os.path.normcase(candidate))
        result[p] = os.path.join(output_dir, candidate)
    return result


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def write_diff_file(source: TextFile, new_content: str, input_file: str, output_file: str
                     ) -> Optional[str]:
    """
    Writes a unified diff (source -> new_content) to '<output_file>.diff',
    next to the real output. Returns the diff path, or None if unchanged.
    Encoded like the source file, so non-UTF-8 bytes stay as they were.
    """
    if source.text == new_content:
        return None
    diff_lines = difflib.unified_diff(
        split_lines(source.text), split_lines(new_content),
        fromfile=input_file, tofile=output_file, lineterm='',
    )
    diff_path = output_file + '.diff'
    _ensure_parent_dir(diff_path)
    with open(diff_path, 'wb') as f:
        f.write(encode_text(source._replace(bom=False), '\n'.join(diff_lines) + '\n'))
    return diff_path


def allocate_backup_path(input_file: str) -> str:
    """`.bak` is the original file from the first in-place run that changed
    anything. Later changing runs get `.bak2`, `.bak3`, ... — existing
    backups are never overwritten."""
    first = input_file + '.bak'
    if not os.path.exists(first):
        return first
    n = 2
    while os.path.exists(f'{input_file}.bak{n}'):
        n += 1
    return f'{input_file}.bak{n}'


def commit_output(input_file: str, output_file: str, source: TextFile, new_content: str,
                  make_backup: bool, do_diff: bool, dry_run: bool
                  ) -> Tuple[Optional[str], Optional[str], bool]:
    """Write new_content / backup / diff. Returns (backup_path, diff_path, changed).

    `changed` is True iff new_content differs from the source text. No backup,
    no rewrite, no diff when unchanged — including output-dir copies and Run.
    Dry-run: never writes the output or a backup; may still write a .diff
    when changed. Output keeps the source's encoding, BOM and line endings;
    the backup is a byte-for-byte copy of the original.
    """
    # Processors always end with a newline; don't count that as a change.
    if source.text and not source.text.endswith('\n') and new_content.endswith('\n'):
        new_content = new_content[:-1]
    changed = source.text != new_content
    if not changed:
        return None, None, False
    if dry_run:
        diff_path = (write_diff_file(source, new_content, input_file, output_file)
                     if do_diff else None)
        return None, diff_path, True
    backup_path = None
    if make_backup and same_file(input_file, output_file):
        backup_path = allocate_backup_path(input_file)
        shutil.copy2(input_file, backup_path)
    _ensure_parent_dir(output_file)
    with open(output_file, 'wb') as f:
        f.write(encode_text(source, new_content))
    diff_path = (write_diff_file(source, new_content, input_file, output_file)
                 if do_diff else None)
    return backup_path, diff_path, True

def run_cns_file(input_file: str, output_file: str, do_prune: bool, do_dedupe: bool,
                  do_headers: bool, do_garbage_lines: bool, do_prune_cmd: bool,
                  do_dedupe_cmd: bool, removal_mode: str, make_backup: bool,
                  do_diff: bool = False, dry_run: bool = False,
                  do_check_values: bool = False, do_remove_tagged_lines: bool = False
                  ) -> Tuple[List[str], Dict[str, int], Optional[str], Optional[str], bool]:
    """
    Runs the CNS/state fixer and the CMD/commands fixer in sequence - they
    touch disjoint parts of the file (State/Statedef blocks vs. Command/
    Remap/Defaults blocks) so chaining them is safe. The CMD fixer is a
    no-op on a file with no such sections (a plain .cns), so this is always
    applied rather than gated on the file extension.
    Returns (log, stats, backup_path, diff_path, wrote).
    """
    source = read_text_file(input_file)
    content = source.text
    if do_remove_tagged_lines:
        content, removed_tagged_lines = remove_tagged_lines(content)
    else:
        removed_tagged_lines = 0
    step1, log1, stats1 = process_file(
        content, do_prune, do_dedupe, do_headers, do_garbage_lines, removal_mode,
        do_check_values
    )
    new_content, log2, stats2 = process_cmd_file(
        step1, do_prune_cmd, do_dedupe_cmd, removal_mode
    )
    log = log1 + log2
    stats = {**stats1, **stats2, 'tagged_lines_removed': removed_tagged_lines}
    backup_path, diff_path, wrote = commit_output(
        input_file, output_file, source, new_content, make_backup, do_diff, dry_run
    )
    return log, stats, backup_path, diff_path, wrote

def run_air_file(input_file: str, output_file: str, do_dedupe_actions: bool,
                  do_bake_fallthrough: bool, do_flag_empty: bool, do_garbage_lines: bool,
                  removal_mode: str, make_backup: bool, do_diff: bool = False,
                  dry_run: bool = False, do_remove_tagged_lines: bool = False
                  ) -> Tuple[List[str], Dict[str, int], Optional[str], Optional[str], bool]:
    """Returns (log, stats, backup_path, diff_path, wrote)."""
    source = read_text_file(input_file)
    content = source.text
    if do_remove_tagged_lines:
        content, removed_tagged_lines = remove_tagged_lines(content)
    else:
        removed_tagged_lines = 0
    new_content, log, stats = process_air_file(
        content, do_dedupe_actions, do_bake_fallthrough, do_flag_empty,
        do_garbage_lines, removal_mode
    )
    stats['tagged_lines_removed'] = removed_tagged_lines
    backup_path, diff_path, wrote = commit_output(
        input_file, output_file, source, new_content, make_backup, do_diff, dry_run
    )
    return log, stats, backup_path, diff_path, wrote

def render_file_report(input_file: str, log: List[str], stats: Dict[str, int],
                        wrote: bool, backup_path: Optional[str],
                        diff_path: Optional[str], dry_run: bool, output_file: str,
                        heading: str = "") -> List[str]:
    out: List[str] = []
    if heading:
        out.append(heading)
    if log:
        out.extend(f"  {printable(line)}" for line in log)
    if not wrote:
        out.append("  No changes needed.")
    else:
        n_headers = stats.get('headers_normalized', 0)
        if n_headers:
            out.append(f"  State headers normalised: {n_headers}")
        n_tagged_lines = stats.get('tagged_lines_removed', 0)
        if n_tagged_lines:
            out.append(f"  Tagged lines removed: {n_tagged_lines}")
        in_place = same_file(input_file, output_file)
        if dry_run:
            verb = "overwrite" if in_place else "write to"
            out.append(f"  (would {verb}: {output_file})")
        else:
            if backup_path:
                out.append(f"  Backup saved to '{backup_path}'.")
            out.append(f"  {'Overwrote' if in_place else 'Written to'} '{output_file}'.")
        # No semantic change (no log entries, no header normalisations) means
        # the only difference is encoding / line endings / trailing newline.
        if not log and not n_headers and not n_tagged_lines:
            out.append("  (no fixes applied; file differs only in encoding / line endings)")
    if diff_path:
        out.append(f"  Diff written to '{diff_path}'.")
    return out

def render_batch_summary(*, totals: Dict[str, int], has_cns: bool, has_air: bool,
                          diffs_written: int, processed: int, failed: int,
                          dry_run: bool, multi: bool, do_diff: bool,
                          removal_mode: str, heading: str = "=== Summary ==="
                          ) -> List[str]:
    """
    Pure batch-summary lines (no I/O). `multi` gates the Files processed /
    Files failed lines (CLI omits them for a single-file run; pass True to
    always show). `do_diff` gates the Diffs written line.
    """
    W = 35  # summary label field width - keep every line's label under this
    out: List[str] = [heading]
    if dry_run:
        note = " (.diff files were still written)" if diffs_written else ""
        out.append(f"  (DRY RUN - no input files were modified{note})")
    if multi:
        out.append(f"  {'Files processed:':<{W}}{processed}")
        if failed:
            out.append(f"  {'Files failed:':<{W}}{failed}")
    if do_diff:
        out.append(f"  {'Diffs written:':<{W}}{diffs_written}")
    if has_cns:
        out.append(f"  {'CNS unknown parameters removed:':<{W}}{totals['pruned']}")
        out.append(f"  {'CNS duplicate parameters removed:':<{W}}{totals['duplicates_removed']}")
        out.append(f"  {'CNS invalid values removed:':<{W}}{totals['invalid_values_removed']}")
        out.append(f"  {'State headers normalised:':<{W}}{totals['headers_normalized']}")
        out.append(f"  {'CMD unknown parameters removed:':<{W}}{totals['cmd_pruned']}")
        out.append(f"  {'CMD duplicate parameters removed:':<{W}}"
                   f"{totals['cmd_duplicates_removed']}")
    if has_air:
        out.append(f"  {'Duplicate actions removed:':<{W}}{totals['duplicate_actions_removed']}")
        out.append(f"  {'Empty actions baked:':<{W}}{totals['empty_actions_baked']}")
        out.append(f"  {'Empty actions flagged:':<{W}}{totals['empty_actions_flagged']}")
    if has_cns or has_air:
        garbage_total = totals['garbage_lines_handled'] + totals['air_garbage_lines_handled']
        out.append(f"  {'Garbage lines handled:':<{W}}{garbage_total}")
        out.append(f"  {'Tagged lines removed:':<{W}}{totals.get('tagged_lines_removed', 0)}")
    out.append(f"  {'Mode used:':<{W}}{removal_mode}")
    if processed > 0 and failed == 0:
        out.append("  All tasks ran successfully.")
    elif failed:
        out.append("  Some tasks failed; see errors above.")
    return out

def main():
    parser = argparse.ArgumentParser(
        description="Mugen Char Linter",
        add_help=True,
    )
    parser.add_argument('input_files', nargs='+',
                         help="One or more .cns/.cmd/.air files, and/or a character's "
                              ".def file to auto-discover its files.")
    parser.add_argument('--output-dir', dest='output_dir', default=None,
                         help="Write copies here instead of overwriting inputs in place.")
    # CNS-family fixers
    parser.add_argument('--prune', dest='prune', action='store_true', default=None)
    parser.add_argument('--no-prune', dest='prune', action='store_false')
    parser.add_argument('--dedupe', dest='dedupe', action='store_true', default=None)
    parser.add_argument('--no-dedupe', dest='dedupe', action='store_false')
    parser.add_argument('--headers', dest='headers', action='store_true', default=None)
    parser.add_argument('--no-headers', dest='headers', action='store_false')
    parser.add_argument('--garbage-lines', dest='garbage_lines', action='store_true', default=None)
    parser.add_argument('--no-garbage-lines', dest='garbage_lines', action='store_false')
    parser.add_argument('--check-values', dest='check_values', action='store_true', default=None,
                         help="Flag/remove keys whose value isn't a recognized enum "
                              "(currently trans/postype/space).")
    parser.add_argument('--no-check-values', dest='check_values', action='store_false')
    parser.add_argument('--prune-cmd', dest='prune_cmd', action='store_true', default=None)
    parser.add_argument('--no-prune-cmd', dest='prune_cmd', action='store_false')
    parser.add_argument('--dedupe-cmd', dest='dedupe_cmd', action='store_true', default=None)
    parser.add_argument('--no-dedupe-cmd', dest='dedupe_cmd', action='store_false')
    # AIR-family fixers
    parser.add_argument('--dedupe-actions', dest='dedupe_actions', action='store_true',
                        default=None)
    parser.add_argument('--no-dedupe-actions', dest='dedupe_actions', action='store_false')
    parser.add_argument('--bake-fallthrough', dest='bake', action='store_true', default=None)
    parser.add_argument('--no-bake-fallthrough', dest='bake', action='store_false')
    parser.add_argument('--flag-empty', dest='flag_empty', action='store_true', default=None)
    parser.add_argument('--no-flag-empty', dest='flag_empty', action='store_false')
    # Shared
    parser.add_argument('--mode', dest='removal_mode', choices=['delete', 'comment', 'tag'],
                         default=None,
                         help="How to handle anything flagged for removal across both families. "
                              "'tag' comments out and prefixes the same bracketed tag used in "
                              "the log.")
    parser.add_argument('--remove-tagged-lines', dest='remove_tagged_lines', action='store_true',
                         help="Delete lines carrying this tool's tags before processing.")
    parser.add_argument('-y', '--yes', dest='yes', action='store_true',
                         help="Don't ask questions: use the default answer for any option "
                              "not given, and skip the Ikemen GO warning.")
    parser.add_argument('--no-backup', dest='backup', action='store_false', default=True,
                         help="Don't create a .bak file when overwriting inputs in place.")
    parser.add_argument('--dry-run', '--analyze', dest='dry_run', action='store_true',
                         default=False, help="Show what would change without writing any files.")
    parser.add_argument('--diff', dest='diff', action='store_true', default=False,
                         help="Also write a '<output>.diff' unified diff per file "
                              "(works with or without --dry-run; skipped for files "
                              "with no changes).")
    args = parser.parse_args()

    input_files, known_types, failed_defs = expand_inputs(args.input_files)
    if not input_files:
        print("No files to process.")
        sys.exit(1)

    def ask(prompt: str, default: bool = True) -> bool:
        return default if args.yes else ask_yes_no(prompt, default)

    cns_files = [f for f in input_files if classify_file(f, known_types.get(f)) == 'cns']
    air_files = [f for f in input_files if classify_file(f, known_types.get(f)) == 'air']

    do_prune = args.prune
    do_dedupe = args.dedupe
    do_headers = args.headers
    do_garbage_lines = args.garbage_lines
    do_check_values = args.check_values
    do_prune_cmd = args.prune_cmd
    do_dedupe_cmd = args.dedupe_cmd
    do_dedupe_actions = args.dedupe_actions
    do_bake = args.bake
    do_flag_empty = args.flag_empty
    removal_mode = args.removal_mode

    if cns_files:
        if do_prune is None:
            do_prune = ask("[CNS] Remove unknown/invalid state-controller parameters?")
        if do_check_values is None:
            do_check_values = ask(
                "[CNS] Also flag/remove recognized keys with an invalid enum value "
                "(trans/postype/space)?"
            )
        if do_dedupe is None:
            do_dedupe = ask(
                "[CNS] Remove duplicate parameters within a block (keeps the FIRST occurrence)?"
            )
        if do_headers is None:
            do_headers = ask(
                "[CNS] Normalise [State] header casing and renumber to match the "
                "enclosing Statedef?"
            )
        if do_prune_cmd is None:
            do_prune_cmd = ask(
                "[CMD] Remove unknown/invalid parameters from Command/Remap/Defaults sections?"
            )
        if do_dedupe_cmd is None:
            do_dedupe_cmd = ask(
                "[CMD] Remove duplicate parameters within a Command/Remap/Defaults block "
                "(keeps FIRST - note: same-named [Command] blocks are NEVER treated as "
                "duplicates, the format allows multiple motions sharing one name)?"
            )
    else:
        do_prune = do_dedupe = do_headers = do_check_values = False
        do_prune_cmd = do_dedupe_cmd = False

    # Garbage-line handling is global - it runs across CNS+CMD text and,
    # separately, over AIR preambles - so it's scanned/asked once for
    # whichever files are actually present, not gated behind cns_files.
    if do_garbage_lines is None:
        garbage_count = 0
        for f in cns_files:
            try:
                garbage_count += count_garbage_lines(read_text_file(f).text)
            except OSError:
                pass  # reported when the file is processed
        for f in air_files:
            try:
                garbage_count += count_air_garbage_lines(read_text_file(f).text)
            except OSError:
                pass  # reported when the file is processed
        if garbage_count > 0:
            do_garbage_lines = ask(
                f"Found {garbage_count} garbage line(s) across {len(cns_files) + len(air_files)} "
                f"file(s) - text that isn't blank, a real comment, a header, or (for CNS/CMD) a "
                f"valid parameter. Handle them?"
            )
        else:
            do_garbage_lines = False

    if air_files:
        if do_dedupe_actions is None:
            do_dedupe_actions = ask(
                "[AIR] Remove duplicate [Begin Action N] blocks (keeps the FIRST occurrence)?"
            )
        if do_bake is None:
            do_bake = ask(
                "[AIR] Bake empty [Begin Action N] blocks with whatever content the "
                "engine's fallthrough would use, instead of relying on it at runtime?"
            )
        if do_flag_empty is None:
            do_flag_empty = ask(
                "[AIR] Report any [Begin Action N] blocks still empty afterward "
                "(never modifies them)?"
            )
    else:
        do_dedupe_actions = do_bake = do_flag_empty = False

    if removal_mode is None:
        any_removal_possible = (do_prune or do_dedupe or do_garbage_lines
                                  or do_prune_cmd or do_dedupe_cmd
                                  or do_dedupe_actions or do_bake)
        if any_removal_possible and not args.yes:
            removal_mode = ask_choice(
                "When something is flagged for removal (either file family), should "
                "it be deleted or commented out?",
                {'d': 'Delete entirely', 'c': 'Comment out (kept, but inert)',
                 't': 'Comment out and tag with the reason'},
                default='c',
            )
            removal_mode = {'d': 'delete', 'c': 'comment', 't': 'tag'}[removal_mode]
        else:
            removal_mode = 'comment'

    # Only matters when unknown parameters would be removed.
    ikemen_defs = [p for p in args.input_files
                   if p.lower().endswith('.def') and os.path.isfile(p) and is_ikemen_def(p)]
    if ikemen_defs and do_prune and not args.dry_run and not args.yes:
        who = ("This character's" if len(ikemen_defs) == 1
               else "At least one character's")
        warning = (
            f"WARNING:\n{who} engine version is Ikemen GO. This linter targets M.U.G.E.N, "
            "so Ikemen-exclusive parameters may be treated as unknown and removed.\n"
            "Continue?"
        )
        if not ask_yes_no(warning, default=False):
            print("Canceled.")
            sys.exit(0)

    if args.output_dir and not os.path.isdir(args.output_dir):
        print(f"Error: output directory '{args.output_dir}' does not exist.")
        sys.exit(1)

    totals = {'pruned': 0, 'duplicates_removed': 0, 'headers_normalized': 0,
              'invalid_values_removed': 0,
              'garbage_lines_handled': 0, 'air_garbage_lines_handled': 0,
              'cmd_pruned': 0, 'cmd_duplicates_removed': 0,
              'duplicate_actions_removed': 0, 'empty_actions_baked': 0,
              'empty_actions_flagged': 0, 'tagged_lines_removed': 0}
    processed, failed, diffs_written = 0, failed_defs, 0
    multi = len(input_files) > 1
    output_paths = plan_output_paths(input_files, args.output_dir)

    if args.dry_run:
        print("\n=== DRY RUN - no files will be modified ===")

    for input_file in input_files:
        output_file = output_paths[input_file]
        try:
            if classify_file(input_file, known_types.get(input_file)) == 'air':
                log, stats, backup_path, diff_path, wrote = run_air_file(
                    input_file, output_file, do_dedupe_actions, do_bake, do_flag_empty,
                    do_garbage_lines, removal_mode, args.backup, do_diff=args.diff,
                    dry_run=args.dry_run, do_remove_tagged_lines=args.remove_tagged_lines
                )
            else:
                log, stats, backup_path, diff_path, wrote = run_cns_file(
                    input_file, output_file, do_prune, do_dedupe, do_headers,
                    do_garbage_lines, do_prune_cmd, do_dedupe_cmd,
                    removal_mode, args.backup, do_diff=args.diff, dry_run=args.dry_run,
                    do_check_values=do_check_values,
                    do_remove_tagged_lines=args.remove_tagged_lines
                )
        except FileNotFoundError:
            print(f"Error: File '{input_file}' not found.")
            failed += 1
            continue
        except (OSError, ValueError) as e:
            print(f"Error: '{input_file}': {e}")
            failed += 1
            continue

        if not multi:
            print()
        if diff_path:
            diffs_written += 1
        heading = f"--- {input_file} ---" if multi else ""
        for line in render_file_report(input_file, log, stats, wrote, backup_path,
                                       diff_path, args.dry_run, output_file, heading):
            print(line)
        print()

        processed += 1
        for key in totals:
            totals[key] += stats.get(key, 0)

    for line in render_batch_summary(
        totals=totals, has_cns=bool(cns_files), has_air=bool(air_files),
        diffs_written=diffs_written, processed=processed, failed=failed,
        dry_run=args.dry_run, multi=multi, do_diff=args.diff,
        removal_mode=removal_mode, heading="=== Summary ==="
    ):
        print(line)

if __name__ == '__main__':
    main()
