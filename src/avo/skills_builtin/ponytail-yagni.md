# Build the smallest thing that works

Before writing code, climb down the ladder and stop at the first
rung that solves the actual request:

1. Does it need to exist at all? Speculative flexibility is debt —
   skip it and say so in one line.
2. Stdlib already does it? Use it.
3. One line in the caller? One line.
4. One function, one file, no new class? Do that.
5. Existing project helper or pattern? Reuse it — grep before you
   write.
6. New module, new class, new dependency: last resorts, each needs
   a one-sentence justification.

Rules:

- No abstract base class for one implementation.
- No config knob for a value that has never changed.
- No interface for a single caller.
- No framework where a function fits.
- Write the lazy, correct version — correct is not optional;
  input validation and error handling stay.
- When the real requirement later grows, the small version refacts
  cheaply; the big speculative one rots.
- If a simplification is skipped because the request explicitly
  asked for more, note that once, briefly.
