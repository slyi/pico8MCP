"""Development-only source annotations and profiler report decoding."""
import re
from pathlib import Path

MAX_SCOPES = 16
RESERVED = set('and break do else elseif end false for function goto if in local nil not or repeat return then true until while'.split())
PATH = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z')
MARKER = re.compile(r'--\s*@profile\s+(begin|end)\s+([A-Za-z_][A-Za-z0-9_. /:-]{0,39})\s*\Z')
LONG = re.compile(r'\[(=*)\[')


def line_comments(source):
    """Yield standalone line comments, ignoring quoted/long strings and comments."""
    i = 0
    while i < len(source):
        if source.startswith('--', i):
            block = LONG.match(source, i + 2)
            if block:
                end = source.find(']' + block[1] + ']', block.end())
                i = len(source) if end < 0 else end + len(block[1]) + 2
                continue
            end = source.find('\n', i)
            if end < 0:
                end = len(source)
            if not source[source.rfind('\n', 0, i) + 1:i].strip():
                yield i, end, source[i:end]
            i = end
        elif source[i] in "\"'":
            quote = source[i]
            i += 1
            while i < len(source):
                if source[i] == '\\':
                    i += 2
                elif source[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
        else:
            block = LONG.match(source, i)
            if block:
                end = source.find(']' + block[1] + ']', block.end())
                i = len(source) if end < 0 else end + len(block[1]) + 2
            else:
                i += 1


def instrument(source, enabled=False, functions=None, sections=False):
    functions = [] if functions is None else functions
    if not isinstance(functions, list) or len(functions) > MAX_SCOPES:
        raise ValueError('profile_functions must be a list of at most 16 function paths.')
    for name in functions:
        if (not isinstance(name, str) or len(name) > 80 or not PATH.fullmatch(name)
                or any(part in RESERVED for part in name.split('.')) or name.startswith('__p8mcp')):
            raise ValueError('Invalid profile function path: ' + repr(name))
    if len(set(functions)) != len(functions):
        raise ValueError('profile_functions must not contain duplicates.')
    enabled = bool(enabled or functions or sections)
    scopes = [dict(name=name, kind='function') for name in functions]
    suffix = []
    for index, name in enumerate(functions, 1):
        parts = name.split('.')
        guards = [f'type({".".join(parts[:n])})=="table"' for n in range(1, len(parts))]
        guards.append(f'type({name})=="function"')
        suffix.append(f'if {" and ".join(guards)} then {name}=__p8mcp_prof.wrap({index},{name}) else __p8mcp_prof.rows[{index}][7]=1 end')
    if sections:
        ids, stack, replacements = {}, [], []
        for start, end, comment in line_comments(source):
            marker = MARKER.fullmatch(comment)
            if not marker:
                if re.match(r'--\s*@profile\b', comment):
                    raise ValueError('Expected -- @profile begin NAME or -- @profile end NAME.')
                continue
            action, name = marker[1], marker[2].strip()
            if action == 'begin':
                if name not in ids:
                    scopes.append(dict(name=name, kind='section'))
                    ids[name] = len(scopes)
                stack.append(name)
            elif not stack or stack.pop() != name:
                raise ValueError('Unmatched profile section end: ' + name)
            replacements.append((start, end, f'__p8mcp_prof.{"enter" if action == "begin" else "exit"}({ids[name]})'))
        if stack:
            raise ValueError('Unclosed profile section: ' + stack[-1])
        for start, end, replacement in reversed(replacements):
            source = source[:start] + replacement + source[end:]
    if len(scopes) > MAX_SCOPES:
        raise ValueError('At most 16 function/section scopes can be profiled together.')
    prefix = ('local __p8mcp_prof=nil\n' if not enabled else
              Path(__file__).with_name('profiler.lua').read_text(encoding='utf-8').replace('__SCOPE_COUNT__', str(len(scopes))) + '\n')
    return prefix, source, '\n'.join(suffix), dict(profiling=enabled, profileScopes=scopes)


def decode_report(logs, sequence, scopes):
    prefix = f'@@p8mcp_prof:{sequence}:'
    records = []
    for log in logs:
        text = log.get('text', '')
        if prefix in text:
            records.append(text.split(prefix, 1)[1].strip().split(':'))
    headers = [r for r in records if r[0] == 'H']
    rows = [r for r in records if r[0] == 'R']
    if len(headers) != 1 or len(rows) != len(scopes) or not any(r == ['DONE'] for r in records):
        raise ValueError('Incomplete native profiling report.')
    h = [int(x) for x in headers[0][1:]]
    if len(h) != 10:
        raise ValueError('Invalid native profiling header.')
    frames, updates, draws, native, update, draw, minimum, maximum, over, invalid = h
    total = (update + draw) / 65536
    hotspots = []
    seen = set()
    for row in rows:
        values = [int(x) for x in row[1:]]
        if len(values) != 8:
            raise ValueError('Invalid native profiling row.')
        index, inclusive, exclusive, peak, low, high, bad, missing = values
        if not 1 <= index <= len(scopes) or index in seen:
            raise ValueError('Invalid or duplicate profiling scope ID.')
        seen.add(index)
        calls = low + high * 32768
        hotspots.append(dict(**scopes[index-1], calls=calls, missing=bool(missing),
                             invalidSpans=bad, inclusiveCpu=inclusive/65536,
                             selfCpu=exclusive/65536, maxCallCpu=peak/65536,
                             meanCallCpu=inclusive/65536/calls if calls else None,
                             selfSharePercent=exclusive/65536/total*100 if total else 0))
    hotspots.sort(key=lambda row: row['selfCpu'], reverse=True)
    unmeasured = max(0, total - sum(row['selfCpu'] for row in hotspots))
    return dict(renderFrames=frames, updateCalls=updates, drawCalls=draws,
                nativeFrameCpu=dict(mean=native/65536/frames if frames else None,
                                    min=minimum/65536 if frames else None,
                                    max=maximum/65536 if frames else None,
                                    overBudgetFrames=over),
                callbacks=dict(updateCpu=update/65536, drawCpu=draw/65536, totalCpu=total,
                               meanUpdateCpu=update/65536/updates if updates else None,
                               meanDrawCpu=draw/65536/draws if draws else None),
                hotspots=hotspots, unattributedCpu=unmeasured,
                unattributedSharePercent=unmeasured/total*100 if total else 0,
                invalidSpans=invalid,
                units='CPU values are fractions of the PICO-8 frame budget, not milliseconds. '
                      'selfSharePercent uses all captured user callback CPU as its denominator. '
                      'Inclusive times overlap for nested calls; do not add them.')


def rates(samples):
    first, last = samples[0], samples[-1]
    elapsed = (last['ms'] - first['ms']) / 1000
    updates = sum((b['updates']-a['updates']) % 32768 for a, b in zip(samples, samples[1:]))
    draws = sum((b['draws']-a['draws']) % 32768 for a, b in zip(samples, samples[1:]))
    gaps = [(b['ms']-a['ms']) for a, b in zip(samples, samples[1:])]
    return dict(seconds=elapsed, updates=updates, draws=draws,
                updatesPerSecond=updates/elapsed if elapsed else 0,
                drawsPerSecond=draws/elapsed if elapsed else 0,
                maxPollGapMs=max(gaps, default=0),
                pollsWithoutDraw=sum(a['draws'] == b['draws'] for a, b in zip(samples, samples[1:])),
                vmFps=last['vmFps'],
                note='Rates count user callbacks over browser performance.now() time. '
                     'VM FPS is separate. Poll gaps are sampling delays, not exact frame times; '
                     'draw callback rate does not measure physical display refresh.')
