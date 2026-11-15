"""Lower bounded reachability of single-clock SVA sequences to sampled logic."""
from functools import lru_cache
import re

import sva_source


TRUE = "1'b1"
FALSE = "1'b0"
BOUND = r'(?:[0-9]+|[A-Za-z_][\w$]*(?:::[A-Za-z_][\w$]*)*)'
DELAY = r'##\s*(?:\[\s*{0}\s*:\s*(?:{0}|\$)\s*\]|{0})'.format(BOUND)
REPETITION = r'\[\s*(?:\+|\*|=|->)\s*(?:{0}(?:\s*:\s*(?:{0}|\$))?)?\s*\]'.format(BOUND)
TOKENS = re.compile(r'{}|{}|\|[-=]>|(?<![\w$])(?:throughout|within|intersect|and|or|'
                    r's_until_with|until_with|s_until|until)(?![\w$])|[()\[\]{{}}]'.format(DELAY, REPETITION))
TIMED_PROPERTY = re.compile(r'(s_nexttime|nexttime|s_eventually|eventually|s_always|always)\b\s*'
                            r'(?:\[\s*({0}(?:\s*:\s*(?:{0}|\$))?)\s*\]\s*)?'.format(BOUND))


def number(text):
    return int(text) if text.isdecimal() else text


def bounds(text):
    parts = re.split(r'(?<!:):(?!:)', text, maxsplit=1)
    lo = parts[0]
    lo = number(lo.strip())
    hi = None if parts[-1].strip() == '$' else number(parts[-1].strip())
    if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
        raise ValueError('时序范围的下界大于上界')
    return lo, hi


def timed_property(source):
    match = TIMED_PROPERTY.match(source)
    if not match:
        return None
    kind = match[1]
    interval = bounds(match[2]) if match[2] else (1, 1) if kind.endswith('nexttime') else (0, None)
    if kind.endswith('nexttime') and interval[0] != interval[1]:
        raise ValueError('nexttime 需要固定拍数')
    return kind, interval, source[match.end():]


def timed_match(timing, boolean):
    kind, interval, operand = timing
    if kind.endswith('always'):
        low, high = interval
        if not isinstance(low, int) or not isinstance(high, int):
            raise ValueError('Cover 的 always 需要可观察结束的固定有限区间')
        duration = high-low+1
        held = ('repeat', ('atom', boolean(operand)), '*', (duration, duration))
        return ('delay', ('atom', TRUE), (low, low), held)
    return ('delay', ('atom', TRUE), interval, parse(operand, boolean))


def parse(source, boolean):
    """Parse sequence operators over unchanged SystemVerilog expressions."""
    source = source.strip()
    while source.startswith('(') and sva_source.after_paren(source, 0) == len(source):
        source = source[1:-1].strip()
    timing = timed_property(source)
    if timing and not timing[0].endswith('nexttime'):
        return timed_match(timing, boolean)
    top, depth = [], 0
    for match in TOKENS.finditer(source):
        operator = match.group()
        if operator in ('(', '[', '{'):
            depth += 1
        elif operator in (')', ']', '}'):
            depth -= 1
        elif depth == 0:
            top.append(match)
    for group in (('|->', '|=>'), ('until', 'until_with', 's_until', 's_until_with'), ('or',), ('and',), ('intersect',),
                  ('within',), ('throughout',)):
        if len(group) == 1 and group[0] == 'intersect':
            if timing:
                return timed_match(timing, boolean)
            unary = re.match(r'not\s+', source)
            if unary:
                return ('atom', '!' + boolean(source[unary.end():]))
        operators = [match for match in top if match.group() in group]
        if operators:
            operator = operators[0] if len(group) > 1 or group == ('throughout',) else operators[-1]
            kind = operator.group()
            left = parse(source[:operator.start()], boolean)
            right = parse(source[operator.end():], boolean)
            if kind in ('|->', '|=>'):
                lead = int(kind == '|=>')
                return ('delay', left, (lead, lead), right)
            if kind == 'throughout' and left[0] != 'atom':
                raise ValueError('throughout 左侧需要单拍布尔条件')
            if 'until' in kind and (left[0] != 'atom' or right[0] != 'atom'):
                raise ValueError('当前支持两个单拍条件之间的 until')
            return (kind, left, right)
    delays = [match for match in top if match.group().startswith('##')]
    if delays:
        left = source[:delays[0].start()].strip()
        node = parse(left, boolean) if left else ('atom', TRUE)
        for index, match in enumerate(delays):
            delay = match.group()[2:].strip()
            following = delays[index+1].start() if index+1 < len(delays) else len(source)
            node = ('delay', node, bounds(delay.strip('[]')), parse(source[match.end():following], boolean))
        return node
    if top and top[-1].end() == len(source) and top[-1].group().startswith('['):
        repeat = top[-1]
        mode = re.match(r'\[\s*(->|[+=*])\s*', repeat.group())
        limits = repeat.group()[mode.end():-1].strip()
        node = parse(source[:repeat.start()], boolean)
        if mode[1] in ('->', '=') and node[0] != 'atom':
            raise ValueError('非连续重复需要单拍布尔条件')
        return ('repeat', node, mode[1], bounds(limits or ('1:$' if mode[1] == '+' else '0:$')))
    wrapper = re.match(r'(first_match|strong|weak)\s*\(', source)
    if wrapper and sva_source.after_paren(source, wrapper.end()-1) == len(source):
        inner = parse(source[wrapper.end():-1], boolean)
        return ('first', inner) if wrapper[1] == 'first_match' else inner
    return ('atom', boolean(source))


def max_span(node):
    kind = node[0]
    if kind == 'atom':
        return 1
    if kind == 'first':
        return max_span(node[1])
    if kind == 'delay':
        left, (_, high), right = node[1:]
        spans = (max_span(left), high, max_span(right))
        return max(0, sum(spans)-1) if all(isinstance(n, int) for n in spans) else None
    if kind == 'repeat':
        body, mode, (_, high) = node[1:]
        span = max_span(body)
        return span * high if mode in ('*', '+') and isinstance(high, int) and span is not None else None
    if kind in ('throughout', 'within'):
        return max_span(node[2])
    if kind in ('and', 'or', 'intersect'):
        spans = [max_span(child) for child in node[1:]]
        if kind == 'intersect':
            return min((span for span in spans if span is not None), default=None)
        return max(spans) if None not in spans else None
    return None


class Matcher:
    """Generate logic for complete matches inside the solver's sampling horizon."""
    def __init__(self, prefix, depth):
        self.prefix = prefix
        self.depth = depth
        self.wires = {}
        self.history = {}
        self.constants = {}
        self.counts = {}
        self.counters = []
        self.visited = 0
        self.matches = lru_cache(maxsize=None)(self.match)
        self.repeats = lru_cache(maxsize=None)(self.repeat)

    def combine(self, conjunction, args):
        neutral, stop = (TRUE, FALSE) if conjunction else (FALSE, TRUE)
        terms = {}
        for term in args:
            if term == stop:
                return stop
            if term != neutral:
                terms[term] = None
        if not terms:
            return neutral
        if len(terms) == 1:
            return next(iter(terms))
        expression = (' && ' if conjunction else ' || ').join('(' + term + ')' for term in terms)
        if expression not in self.wires:
            if len(self.wires) >= 200000:
                raise ValueError('SVA 序列在当前求解深度的展开规模过大')
            self.wires[expression] = '{}_n{}'.format(self.prefix, len(self.wires))
        return self.wires[expression]

    def atom(self, expression, tick):
        age = self.depth - tick
        if age == 0:
            return expression
        if expression not in self.history:
            self.history[expression] = ['{}_h{}'.format(self.prefix, len(self.history)), age]
        name, width = self.history[expression]
        self.history[expression][1] = max(width, age)
        return '{}[{}]'.format(name, age - 1)

    def in_range(self, amount, limits):
        terms = []
        for bound, operator in zip(limits, ('>=', '<=')):
            if bound is None:
                continue
            if isinstance(bound, int):
                if operator == '>=' and amount < bound or operator == '<=' and amount > bound:
                    return FALSE
                continue
            if bound not in self.constants:
                self.constants[bound] = '{}_limit{}'.format(self.prefix, len(self.constants))
            terms.append('({} {} {})'.format(amount, operator, self.constants[bound]))
        return self.combine(True, terms)

    def occurrence_count(self, expression, start, end):
        """Share the cumulative count for all intervals of a sampled condition."""
        if start == end:
            return '0'
        width = (self.depth + 1).bit_length()
        counts = self.counts.setdefault(expression, ["{}'d0".format(width)])
        for tick in range(len(counts)-1, end):
            sample = self.atom(expression, tick)
            name = '{}_count{}'.format(self.prefix, len(self.counters))
            self.counters.append("wire [{}:0] {} = {} + (({}) ? {}'d1 : {}'d0);".format(
                width-1, name, counts[-1], sample, width, width))
            counts.append(name)
        return '({} - {})'.format(counts[end], counts[start])

    def repeat(self, body, count, start, end):
        if not count:
            return TRUE if start == end else FALSE
        return self.combine(False, (self.combine(True, (self.matches(body, start, part),
                                                        self.repeats(body, count-1, part, end)))
                                    for part in range(start, end+1)))

    def match(self, node, start, end):
        """The end is exclusive, so an empty match has end == start."""
        self.visited += 1
        if self.visited > 1000000:
            raise ValueError('SVA 序列在当前求解深度的状态规模过大')
        kind = node[0]
        if kind == 'atom':
            return self.atom(node[1], start) if end == start+1 else FALSE
        if kind == 'delay':
            left, limits, right = node[1:]
            lo, hi = limits
            def candidates():
                for split in range(start, end+1):
                    low = max(start, split-1 + (lo if isinstance(lo, int) else 0))
                    high = min(end, split-1+hi) if isinstance(hi, int) else end
                    for next_start in range(low, high+1):
                        if next_start == split-1 and (split == start or next_start == end):
                            continue
                        distance = next_start - split + 1
                        right_match = self.matches(right, next_start, end)
                        if right_match != FALSE:
                            left_match = self.matches(left, start, split)
                            if left_match != FALSE:
                                yield self.combine(True, (left_match, right_match, self.in_range(distance, limits)))
            return self.combine(False, candidates())
        if kind == 'repeat':
            body, mode, limits = node[1:]
            if mode in ('*', '+') and body[0] == 'atom':
                return self.combine(True, (self.in_range(end-start, limits),
                                           self.combine(True, (self.atom(body[1], k) for k in range(start, end)))))
            if mode in ('=', '->'):
                count = self.occurrence_count(body[1], start, end)
                conditions = []
                for bound, operator in zip(limits, ('>=', '<=')):
                    if bound is not None:
                        conditions.append('({} {} {})'.format(count, operator, bound))
                if mode == '->' and end > start:
                    conditions.append(self.matches(body, end-1, end))
                return self.combine(True, conditions)
            lo, hi = limits
            maximum = end-start
            if isinstance(lo, int):
                maximum += lo if self.matches(body, start, start) != FALSE else 0
            if isinstance(hi, int):
                maximum = min(maximum, hi)
            return self.combine(False, (self.combine(True, (self.in_range(n, limits),
                                                            self.repeats(body, n, start, end)))
                                        for n in range(lo if isinstance(lo, int) else 0, maximum+1)))
        if kind == 'first':
            match = self.matches(node[1], start, end)
            if match == FALSE:
                return FALSE
            earlier = self.combine(False, (self.matches(node[1], start, k) for k in range(start, end)))
            return self.combine(True, (match, self.invert(earlier)))
        left, right = node[1:]
        if kind in ('or', 'intersect'):
            return self.combine(kind == 'intersect', (self.matches(left, start, end),
                                                       self.matches(right, start, end)))
        if kind == 'and':
            return self.combine(False, (
                self.combine(True, (self.matches(a, start, end),
                                    self.combine(False, (self.matches(b, start, k) for k in range(start, end+1)))))
                for a, b in ((left, right), (right, left))))
        if kind == 'throughout':
            return self.combine(True, (self.matches(right, start, end),
                                       self.combine(True, (self.atom(left[1], k) for k in range(start, end)))))
        if kind == 'within':
            outside = self.matches(right, start, end)
            if outside == FALSE:
                return FALSE
            return self.combine(True, (outside,
                                       self.combine(False, (self.matches(left, i, j) for i in range(start, end+1)
                                                            for j in range(i, end+1)))))
        if 'until' in kind and end > start:
            last = end if kind.endswith('_with') else end-1
            return self.combine(True, (self.atom(right[1], end-1),
                                       self.combine(True, (self.atom(left[1], k) for k in range(start, last)))))
        return FALSE

    @staticmethod
    def invert(value):
        return FALSE if value == TRUE else TRUE if value == FALSE else '!({})'.format(value)

    def model(self, node, clock, reset, as_bit):
        checks = []
        valid = self.prefix + '_valid'
        for start in range(self.depth, -1, -1):
            match = self.matches(node, start, self.depth+1)
            if match != FALSE:
                observed = '{}[{}]'.format(valid, self.depth-start-1) if start < self.depth else TRUE
                checks.append(self.combine(True, (observed, match)))
        predicate = self.combine(False, checks)
        lines = ['localparam integer {} = {};'.format(name, expression)
                 for expression, name in self.constants.items()]
        if self.depth:
            lines.append("reg [{}:0] {} = '0;".format(self.depth-1, valid))
            if reset:
                cancel = self.prefix + '_cancel'
                lines.extend(['wire {} = {};'.format(cancel, as_bit(reset)),
                              'always @(posedge {} or posedge {})'.format(clock, cancel),
                              "  if ({}) {} <= '0; else".format(cancel, valid)])
            else:
                lines.append('always @(posedge {})'.format(clock))
            lines.append("  {0} <= ({0} << 1) | 1'b1;".format(valid))
        for expression, (name, width) in self.history.items():
            lines.extend(["reg [{}:0] {} = '0;".format(width-1, name),
                          'always @(posedge {0}) {1} <= ({1} << 1) | {2};'.format(clock, name, as_bit(expression))])
        lines.extend(self.counters)
        lines.extend('wire {} = {};'.format(name, expression) for expression, name in self.wires.items())
        return predicate, lines
