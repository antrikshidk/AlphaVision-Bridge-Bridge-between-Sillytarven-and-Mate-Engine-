## Summary

<!-- What does this PR do? One or two sentences. -->

## Changes

- 
- 

## Motivation / Context

<!-- Why is this change needed? Link to any related issue: Closes #123 -->

## Test Results

<!-- Run the full test suite with the venv Python and paste the final line below. -->
<!-- Command: alpha_env\Scripts\python.exe test_awareness.py -->

```
Ran XX tests in X.XXXs

OK
```

All 78 existing tests pass: ✅

## Checklist

- [ ] All 78 existing tests pass (`alpha_env\Scripts\python.exe test_awareness.py`)
- [ ] New functionality has corresponding new tests (if applicable)
- [ ] No `.env` or secrets staged (`git diff --cached` shows no API keys)
- [ ] Conventional commit message used (`feat:`, `fix:`, `docs:`, etc.)
- [ ] Both `index.js` copies are identical (if the SillyTavern extension was modified):
  - `extensions/Extension-MateEngine/index.js`
  - `SillyTavern\public\scripts\extensions\third-party\Extension-MateEngine\index.js`
- [ ] No placeholder code or truncated functions in the diff
- [ ] `AnimeVision - Copy.py` and `AnimeVision_2.py` are untouched
