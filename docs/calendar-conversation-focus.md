# Conversation focus repair — not deployed

Based on deployed `b7a38b7`. Milestone 1 only.

The October 18 Telegram report showed a greeting and `new convo` continuing the old
Machine Learning task. ConversationService retains bounded completed turns across
restarts, in chronological order, and appends the latest message last. The native
instruction strongly encouraged acting once workload was known but did not clearly
limit that instruction to a currently requested task. Stored context was consequently
being treated as unfinished work. Replies were model-generated, not fixed reply text.

The native instructions now explicitly prioritize the latest intent, distinguish
greetings/topic switches/fresh starts from continuations, and describe calendar_context
as stored facts rather than an action queue. A fresh conversational topic sets aside
the old task without deleting chat history, pending proposals, settings or events.
No phrase classifier, reset regex, new command requirement or execution tool was added.
Internal slot references should stay out of conversational prose.

`/clearconversation` remains the existing explicit persistent chat-history clearing
operation; it preserves saved settings and Calendar plans. Natural conversational
fresh starts do not claim to erase stored records.

## Evidence

The live IBM harness uses synthetic persisted history, temporary SQLite, mocked Google,
and rejects any model-selected tool before executing it. With deployed instructions,
the model reproduced the stale-task response to `hi` and `new convo`. The baseline
also claimed it would clear a draft in one run without a tool, despite not doing so.

With revised instructions, the final run passed greeting, informal fresh start followed
by a capabilities question, alternate fresh-start wording, and a topic change to a
one-sentence overfitting explanation: five live submissions, no planning tools or writes.
Responses were manually inspected as synthetic output; simple harness text checks are
regression signals, not a guarantee of all natural-language behavior. An early check
incorrectly flagged a harmless Machine Learning example; it was narrowed before the
final comparison.

The existing live synthetic dialogue scenario also passed: broad ML request, frequency
range, short answer, four sessions across two weeks, individual amendment, stale-review
denial, and rejection without writes (seven submissions). This verifies the focus change
does not simply disable normal multi-turn scheduling.

The offline pending-state regression verifies latest-message ordering, preserved
conversation context, unchanged pending proposal/preferences, and no availability reads
or Google writes for natural replies. Run the focused native suite with:

```powershell
..\..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_calendar_native.py
..\..\.venv\Scripts\python.exe scripts/check_conversation_focus.py
# Explicit live IBM opt-in, synthetic data only:
..\..\.venv\Scripts\python.exe scripts/check_conversation_focus.py --verify --env-file ..\..\.env
```

Production records were not inspected or cleared. No live processes, configuration,
Calendar events, Telegram messages or public access policy were changed. Deployment
requires approval after this repair is reviewed. Model adherence remains probabilistic;
real Telegram acceptance is still required after deployment.
