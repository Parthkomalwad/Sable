"""One module per builtin command family.

Split out of `app/repl.py` in Phase 0.5 step 3 (docs/structure.md §5), which
had grown to just over a thousand lines. Each module owns one `/command`
family and depends only on `ui/console` for output, so none of them import
the REPL back.

Dispatch still lives in `repl._handle_builtin`; these are the handlers it
calls.
"""
