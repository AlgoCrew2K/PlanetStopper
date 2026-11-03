Part 1, operator directive (standing, verbatim): "the hygiene work also isn't trivial. if this code is spaghetti like having multiple codepaths to the same outcomes or variables/classes/functions that do the same thing elsewhere or anything like that, or we have old files lying around like screenshots or feature-plans then we need to clean up after ourselves."
Part 2, the follow-ups section of the PM's 2026-11-03 operator report (verbatim):
"## Follow-ups (not blocking)
- `templates/table_partial.html` is dead. It is still rendered into every `/api/state` response that nothing reads, and removing it would shrink the payload; this is an `app.py` change.
- The shared header styles (`.section-header`, `.mini-stat`) are refined per page in three places; they could be folded into `layout.css`."
