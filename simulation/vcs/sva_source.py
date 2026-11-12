"""Create a simulation copy of the port SVA and keep the authoring source intact."""
import re


PROPERTY = re.compile(r'\b(?:(?P<label>[A-Za-z_][\w$]*)\s*:\s*)?'
                      r'(?P<kind>assert|assume)\s+property\s*\(')
DISABLE = re.compile(r'\bdisable\s+iff\s*\(')
ELSE = re.compile(r'else\b\s*')
STRICT = 'tb_cmdbuf_rd.monitor.strict_mode'


def mask_sv(code):
    """Hide comments and strings while keeping offsets and line numbers."""
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                  lambda m: ''.join('\n' if c == '\n' else ' ' for c in m.group()), code, flags=re.S)


def after_paren(code, opening):
    depth = 0
    for index in range(opening, len(code)):
        if code[index] == '(':
            depth += 1
        elif code[index] == ')':
            depth -= 1
            if depth == 0:
                return index+1
    return None


def next_token(code, start):
    return len(code)-len(code[start:].lstrip())


def action_end(code, start):
    """Recognize a begin/end block or a simple call without parsing its body."""
    if re.match(r'begin\b', code[start:]):
        depth = 0
        for token in re.finditer(r'\b(begin|end)\b', code[start:]):
            depth += 1 if token.group() == 'begin' else -1
            if depth == 0:
                end = start + token.end()
                name = re.match(r'\s*:\s*[A-Za-z_]\w*', code[end:])
                return end + (name.end() if name else 0)
        return None
    if code[start:start+1] == ';':
        return start+1
    call = re.match(r'[A-Za-z_$][\w$.]*\s*(?=[(;])', code[start:])
    if not call:
        return None
    end = start+call.end()
    if code[end] == '(':
        end = after_paren(code, end)
        if end is None:
            return None
    terminator = re.match(r'\s*;', code[end:])
    return end+terminator.end() if terminator else None


def instrument(code, strict_labels):
    clean = mask_sv(code)
    properties, edits = [], []
    detail = ('$sformatf("path=%m sampled_pending=%h src=%b arvalid=%b ready=%h req=%h '
              'argrant=%h rwgrant=%h rready=%h rvalid=%h rid=%h", '
              '$sampled(pending_cnt), $sampled(src), $sampled(arvalid), $sampled(arready), '
              '$sampled(req), $sampled(ar_rd_gnt), $sampled(rw_rd_gnt), $sampled(rready), '
              '$sampled(rvalid), $sampled(rid))')
    cursor = 0
    for index, prop in enumerate(PROPERTY.finditer(clean), 1):
        if prop.start() < cursor:
            continue
        end = after_paren(clean, prop.end()-1)
        if end is None:
            # VCS reports syntax errors against the actual edited source copy.
            continue
        line = clean.count('\n', 0, prop.start())+1
        label = prop['label'] or 'CMDBUF_SVA_L{}_{}'.format(line, index)
        strict = label in strict_labels
        tail = next_token(clean, end)
        following = ELSE.match(clean, tail)
        action_start = tail
        bare = clean[tail:tail+1] == ';' and not re.match(r'\s*else\b', clean[tail+1:])
        if not following and not bare:
            pass_end = action_end(clean, tail)
            if pass_end is not None:
                action_start = next_token(clean, pass_end)
                following = ELSE.match(clean, action_start)
        failure_start = following.end() if following else None
        failure_end = action_end(clean, failure_start) if following else None
        action = 'studio' if bare else 'recorded_action' if failure_end is not None else 'authored'
        if action == 'authored':
            # Keep other legal native action syntax under the author's control.
            properties.append(dict(label=label, line=line, original_kind=prop['kind'],
                                   scope='common', action=action))
            continue

        expression = code[prop.end('kind'):end]
        disable = DISABLE.search(mask_sv(expression)) if strict else None
        guard = 'if ({}) '.format(STRICT) if strict else ''
        if disable:
            disable_end = after_paren(mask_sv(expression), disable.end()-1)
            if disable_end is not None:
                reset = expression[disable.end():disable_end-1]
                reset = reset if reset.strip()=='!rst_n' else '('+reset+')'
                expression = (expression[:disable.end()]+reset+' || !'+STRICT+
                              expression[disable_end-1:])
                guard = ''
        head = code[prop.start():prop.start('kind')] if prop['label'] else label+': '
        callback = 'tb_cmdbuf_rd.monitor.native_failure("{}", $realtime/1ps, {}'.format(label, detail)
        if bare:
            action_code = 'else '+guard+callback+');'
            cursor = tail+1
        else:
            action_code = 'else '+guard+'begin '+callback+', 0); '+code[failure_start:failure_end]+' end'
            cursor = failure_end
        edits.append((prop.start(), cursor, head+'assert'+expression+code[end:action_start]+' '+action_code))
        properties.append(dict(label=label, line=line, original_kind=prop['kind'],
                               scope='strict' if strict else 'common', action=action))
    for start, end, replacement in reversed(edits):
        code = code[:start]+replacement+code[end:]
    return code, properties
