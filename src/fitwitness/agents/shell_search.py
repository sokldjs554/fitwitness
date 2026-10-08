"""Terminal-style search over the workspace's drawing text: grep, find and a few filters, and nothing else.

An agent that searches by typing commands is useful (a regex, a negation, "every revision with no material
on it"), and a shell is the wrong thing to hand a model that reads untrusted documents. So this is not a
shell. There is no shell process: a command line is parsed into at most three stages joined by ``|``, each
stage must be one of ten read-only programs with an explicit list of the flags it may carry, every path
operand must stay inside the directory that was written for this call, and the programs run with a CPU,
memory, file-size and wall-clock limit on a copy of the text that is read-only. ``find`` has no ``-exec``,
``grep`` has no ``-f``, redirection, ``;``, ``&&``, backticks and ``$(...)`` are refused or are just text.

What comes back is never the text. The output is read only for the names of the files it mentions, and a file
is one revision, so the tool returns candidates like every other search tool does. Facts reach the agent only
through ``query_dimensions``, with their source, as before.
"""

from __future__ import annotations

import os
import re
import resource
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

BIN_DIRS = ("/usr/bin", "/bin")
MAX_COMMAND = 400
MAX_STAGES = 3
MAX_OUTPUT = 256_000
TIMEOUT_S = 5.0
ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
HELP = (
    "Terminal-style search. Commands: grep, find, ls, head, tail, wc, sort, uniq, cut, cat, joined by | (at most 3). "
    "The workspace is a directory of text files, one per revision, named <drawing_number>.rev<label>.<revision_id>.txt, "
    "each with lines such as 'drawing_number: ...', 'title: ...', 'family: ...' and 'fact material = SUS304'. "
    "Globs are not expanded: use grep -r PATTERN . with --include=GLOB, or find . -name 'GLOB'. "
    "The result is the list of revisions whose files appear in the output, not the text."
)


class ShellRefused(ValueError):
    """The command line is outside the closed set; the message says which part."""


@dataclass(frozen=True)
class Spec:
    flags: frozenset[str] = frozenset()  # one-letter flags without a value
    valued: dict[str, str] = field(default_factory=dict)  # one-letter flag -> kind of its value
    long: dict[str, str | None] = field(default_factory=dict)  # long flag -> kind of its value, None for none
    paths: bool = True  # takes file or directory operands
    pattern: bool = False  # first operand (when no -e) is a pattern, not a path


SPECS: dict[str, Spec] = {
    "grep": Spec(
        flags=frozenset("rilLcEFwovhHsx") | {"n"},
        valued={"A": "int", "B": "int", "C": "int", "m": "int", "e": "pattern"},
        long={"include": "glob", "exclude": "glob", "max-count": "int", "ignore-case": None, "recursive": None,
              "files-with-matches": None, "files-without-match": None, "count": None, "line-number": None,
              "word-regexp": None, "extended-regexp": None, "fixed-strings": None, "invert-match": None,
              "no-filename": None, "with-filename": None, "only-matching": None},
        pattern=True,
    ),
    "find": Spec(),  # parsed by its own function: path operands, then -name -iname -type -maxdepth -mindepth
    "ls": Spec(flags=frozenset("l1aRhtSr")),
    "head": Spec(valued={"n": "int"}),
    "tail": Spec(valued={"n": "int"}),
    "wc": Spec(flags=frozenset("lwc")),
    "sort": Spec(flags=frozenset("rnuf")),
    "uniq": Spec(flags=frozenset("cdui")),
    "cut": Spec(valued={"d": "char", "f": "fields"}),
    "cat": Spec(flags=frozenset("n")),
}
FIND_VALUED = {"-name": "glob", "-iname": "glob", "-type": "ftype", "-maxdepth": "small", "-mindepth": "small"}


def _binary(name: str) -> str | None:
    for d in BIN_DIRS:
        p = os.path.join(d, name)
        if os.access(p, os.X_OK):
            return p
    return None


def enabled() -> bool:
    """Operator opt-in (``FITWITNESS_SHELL_SEARCH=on``), and only where the programs exist."""
    return os.getenv("FITWITNESS_SHELL_SEARCH", "off").lower() in ("on", "1", "true") and all(_binary(n) for n in SPECS)


def _value(kind: str, text: str, what: str) -> str:
    if kind == "int":
        if not re.fullmatch(r"\d{1,4}", text):
            raise ShellRefused(f"{what} needs a number up to 9999")
    elif kind == "small":
        if not re.fullmatch(r"[0-5]", text):
            raise ShellRefused(f"{what} needs 0 to 5")
    elif kind == "char":
        if len(text) != 1 or text in "\n\0":
            raise ShellRefused(f"{what} needs one character")
    elif kind == "fields":
        if not re.fullmatch(r"[0-9]+(,[0-9]+)*(-[0-9]+)?", text):
            raise ShellRefused(f"{what} needs field numbers like 1 or 1,3")
    elif kind == "ftype":
        if text not in ("f", "d"):
            raise ShellRefused(f"{what} needs f or d")
    elif kind in ("glob", "pattern"):
        if not text or len(text) > 200 or "\0" in text or "\n" in text:
            raise ShellRefused(f"{what} must be 1 to 200 characters on one line")
        if kind == "glob" and "/" in text:
            raise ShellRefused(f"{what} must not contain /")
    return text


def _path(text: str) -> str:
    parts = text.split("/")
    if not text or "\0" in text or "\n" in text or text.startswith(("/", "~")) or ".." in parts or len(text) > 200:
        raise ShellRefused(f"path {text!r} leaves the workspace")
    return text


def _tokens(command: str) -> list[list[str]]:
    if len(command) > MAX_COMMAND or "\0" in command or "\n" in command or "\r" in command:
        raise ShellRefused("command too long or not on one line")
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    stages: list[list[str]] = [[]]
    try:
        for token in lex:
            if set(token) <= set("();<>|&"):
                if token != "|":
                    raise ShellRefused(f"{token!r} is not allowed: only | joins commands")
                stages.append([])
            else:
                stages[-1].append(token)
    except ValueError as error:  # unbalanced quote
        raise ShellRefused(str(error)) from error
    if any(not s for s in stages):
        raise ShellRefused("empty command")
    if len(stages) > MAX_STAGES:
        raise ShellRefused(f"at most {MAX_STAGES} commands")
    return stages


def _check_find(args: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(args) and not args[i].startswith("-"):
        out.append(_path(args[i]))
        i += 1
    while i < len(args):
        flag = args[i]
        if flag not in FIND_VALUED or i + 1 >= len(args):
            raise ShellRefused(f"find: {flag!r} is not allowed (only -name -iname -type -maxdepth -mindepth)")
        out += [flag, _value(FIND_VALUED[flag], args[i + 1], f"find {flag}")]
        i += 2
    return out


def _check(command: str, args: list[str]) -> list[str]:
    if command not in SPECS:
        raise ShellRefused(f"{command!r} is not an allowed command")
    if command == "find":
        return _check_find(args)
    spec, out, operands, i = SPECS[command], [], [], 0
    have_pattern = False
    while i < len(args):
        a = args[i]
        if a == "--":
            operands += args[i + 1:]
            break
        if a.startswith("--"):
            name, eq, val = a[2:].partition("=")
            if name not in spec.long:
                raise ShellRefused(f"{command}: option {a!r} is not allowed")
            kind = spec.long[name]
            if kind is None and eq:
                raise ShellRefused(f"{command}: --{name} takes no value")
            if kind is not None:
                if not eq:
                    raise ShellRefused(f"{command}: --{name} needs =VALUE")
                out.append(f"--{name}=" + _value(kind, val, f"{command} --{name}"))
            else:
                out.append(a)
        elif command in ("head", "tail") and re.fullmatch(r"-\d{1,4}", a):  # head -5, as people type it
            out += ["-n", a[1:]]
        elif a.startswith("-") and len(a) > 1:
            cluster = a[1:]
            first = cluster[0]
            if first in spec.valued:
                kind = spec.valued[first]
                value = cluster[1:] or (args[i + 1] if i + 1 < len(args) else "")
                if not cluster[1:]:
                    i += 1
                out += [f"-{first}", _value(kind, value, f"{command} -{first}")]
                have_pattern = have_pattern or first == "e"
            elif all(c in spec.flags for c in cluster):
                out.append(a)
            else:
                bad = next(c for c in cluster if c not in spec.flags)
                raise ShellRefused(f"{command}: option -{bad} is not allowed")
        else:
            operands.append(a)
        i += 1
    if spec.pattern and not have_pattern:
        if not operands:
            raise ShellRefused(f"{command}: a pattern is needed")
        out += ["-e", _value("pattern", operands.pop(0), f"{command} pattern")]
    if operands and not spec.paths:
        raise ShellRefused(f"{command}: takes no file operands")
    return out + (["--"] + [_path(p) for p in operands] if operands else [])


def parse(command: str) -> list[list[str]]:
    """The command line as validated argv lists, one per stage, or ``ShellRefused`` naming what is not allowed."""
    stages = _tokens(command)
    return [[s[0], *_check(s[0], s[1:])] for s in stages]


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def run(argv: list[list[str]], root: Path) -> str:
    """Run the validated stages on ``root``, each stage's output feeding the next, and return the last output."""
    data: bytes | None = None
    for stage in argv:
        binary = _binary(stage[0])
        if binary is None:
            raise ShellRefused(f"{stage[0]} is not installed here")
        try:
            done = subprocess.run([binary, *stage[1:]], input=data if data is not None else b"", capture_output=True,
                                  cwd=root, env=ENV, timeout=TIMEOUT_S, preexec_fn=_limits, start_new_session=True)
        except subprocess.TimeoutExpired as error:
            raise ShellRefused(f"{stage[0]} took longer than {TIMEOUT_S:g}s") from error
        if done.returncode > (1 if stage[0] == "grep" else 0):
            raise ShellRefused(f"{stage[0]}: {done.stderr.decode(errors='replace').strip()[:200] or 'failed'}")
        data = done.stdout[:MAX_OUTPUT]
    return (data or b"").decode(errors="replace")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9가-힣._-]", "_", text)[:60] or "x"


def _value_text(fact) -> str:
    value = fact.model_dump(mode="json")["value"]
    return f"{value['low']}..{value['high']}" if isinstance(value, dict) else str(value)


class Workspace:
    """A read-only directory with one text file per active revision. Written once, deleted with the object."""

    def __init__(self, revisions, facts: dict[str, list]):
        self._tmp = tempfile.TemporaryDirectory(prefix="fw-shell-")
        self.root = Path(self._tmp.name)
        self.names: dict[str, str] = {}  # file name -> revision id
        for r in revisions:
            name = f"{_slug(r.drawing_number)}.rev{_slug(r.revision_label)}.{_slug(r.id)}.txt"
            lines = [f"drawing_number: {r.drawing_number}", f"revision: {r.revision_label}", f"revision_id: {r.id}",
                     f"title: {r.title}", f"kind: {r.kind}", f"family: {r.family_id}", f"approval: {r.approval}"]
            for f in facts.get(r.id, []):
                mark = "" if f.certainty == "verified" else " [uncertain]"
                lines.append(f"fact {f.field} = {_value_text(f)}{' ' + f.unit if f.unit else ''}{mark}")
            (self.root / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
            (self.root / name).chmod(0o444)
            self.names[name] = r.id
        self.root.chmod(0o555)

    def search(self, command: str, top_k: int = 10) -> list[dict]:
        """Candidates (revision id and how many output lines named its file), best first."""
        output = run(parse(command), self.root)
        counts: dict[str, int] = {}
        for line in output.splitlines():
            if line.endswith(":0"):  # grep -c lists files it found nothing in
                continue
            seen = set()
            for token in [line.split(":", 1)[0], *line.split()]:
                name = token[2:] if token.startswith("./") else token
                if name in self.names and name not in seen:
                    seen.add(name)
                    counts[name] = counts.get(name, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        return [{"revision_id": self.names[n], "scores": {"shell": float(c)}, "facts": []} for n, c in ranked]

    def close(self) -> None:
        if self.root.exists():
            self.root.chmod(0o755)
        self._tmp.cleanup()
