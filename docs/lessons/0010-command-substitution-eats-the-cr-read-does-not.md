# Lesson 0010: `$(...)` eats the carriage return, `read` hands it to you

**Status:** mechanised
**Enforced by:** `tests/test_link_projects.py` — the script is executed, against a real checkout and against a directory with none, and both assertions fail if the CR strip is removed.
**Date:** 2026-07-20
**Trigger:** a shell script is reading values out of a program's stdout, and the program might be
Python, or the machine might be Windows.

## Expected

`link_projects.sh` reads owner, `projects_dir` and the project names from an embedded Python
program, line by line, with `while IFS= read -r line`. `init.sh` reads `projects_dir` from a
similar program with `PROJECTS_DIR="$(python3 -c ...)"`. Same source, same values, same platform —
so either both work or neither does.

## Happened

`init.sh` worked and `link_projects.sh` did not, on the same machine, in the same minute.

Python's `print()` writes `\r\n` on Windows. Command substitution strips the trailing newline
*pair*, so `$( )` produced a clean `projects`. `read` strips only the `\n` and returns the `\r`, so
every value in the other script carried one:

```
+ dest=$'…/cyc/projects\r'
+ '[' -d $'…/cyc/projects\r/api\r' ']'
```

`[ -d ]` never matched, so every project reported `MISSING` however correctly it was checked out —
and the clone path was no better, since it would have run `gh repo clone "alice\r/api\r"`. The
script had never worked on Windows at all.

## Why it got past us

[[0001]] already recorded that `link_projects.sh` had never been executed once. That lesson was
written, filed, and then not acted on — the script still had not been run when this was found,
three releases later. A lesson that names an unexercised path and is left as prose does not
exercise it.

The two call sites also looked interchangeable. Nothing in `read` versus `$( )` suggests they
disagree about line endings, and on POSIX they never do.

## Next time

When a lesson names a specific thing nobody has run, run it — that is the entire content of the
lesson, and it costs a minute. Better, make running it the thing that closes the lesson: this one
is retired because there is now a test that executes the script, not because someone read the
warning.

And treat `read` and `$( )` as different parsers, because they are. Any shell loop consuming a
program's stdout on a platform that might translate newlines wants `line="${line%$'\r'}"`.
