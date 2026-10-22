# AI quality, evaluation and improvement plan

Status: proposal (2026-10-21). Nothing here is built yet. Math-specific correctness checks are on hold until the
evaluation framework exists.

## 1. How large edtech companies keep AI content correct

| Company / source | How they keep AI teaching content correct |
|---|---|
| Khan Academy (Khanmigo) | A background "math agent" checks calculations, and numbers go through a calculator instead of the model. The model is given human-written solutions first. Every change is A/B tested, and math error rate is a metric that must not get worse. They also built their own dataset of tutoring conversations to test against. |
| Duolingo | AI generates the content, automated checks run next, and Learning Designers review it before release. Generation is based on existing curriculum material. Logging and monitoring were essential: an AI-built French course shipped without the words for "and" and "or". |
| Google LearnLM | Teaching quality is scored against a fixed rubric: 29 questions rated 1–7 by experts, plus automatic yes/no checks for each tutoring turn. |
| Research on AI judges (2025–26) | The judge should be a different model from the generator, because a model tends to approve its own output. Use clear rubrics with examples for each score. Run the judge several times and flag only consistent failures. Check the judge against human reviewers before trusting it. |
| Fact-checking research | Split the text into individual claims and mark each one supported, contradicted or not in the source. |
| Math research (SymCode, Prove) | Have the model write SymPy code to recompute each answer and compare. This beats asking the model to "double-check". |

**What they have in common:** base the content on a trusted source, check with tools where possible, use a separate
judge model with a rubric, keep humans reviewing, and track metrics. None of them say fine-tuning is how they get
correctness.

## 2. Fine-tuning: not now

- Fine-tuning needs hundreds of labelled good and bad examples, and we have none.
- It doesn't guarantee correct output. A model can still make mistakes after fine-tuning.
- The errors we found in `greens-theorem` (wrong integrand, a made-up example, a wrong answer key) are caught better by
  checking against the transcript and recomputing the maths.
- **Later:** fine-tune a small, fast model that only routes doubts. This becomes worthwhile once we've logged about 500
  labelled doubts. The evaluation below is what produces those labels.

## 3. Proposed `review` prep stage (between `scenes` and `audio`)

Running it before `audio` saves Gnani TTS time, since Gnani allows only one request at a time. Every scene goes
through these checks:

1. **Rule checks (no LLM)**
   - The scene is valid and complete.
   - It isn't a near-copy of another scene. Scene 2 duplicating scene 1 would fail here.
   - It covers its outline item.
   - The correct answer isn't always in the same position. Shuffle the options.
2. **Faithfulness to the transcript**
   - Split the narration into claims and check each against that scene's part of the transcript: supported,
     contradicted, or not in the source.
   - This catches a scene that teaches a different example from the lecture, like Green's scene 4.
3. **Subject checks (plug-ins, chosen by subject)**
   - **Maths and physics:** extract each formula or result and recompute it with SymPy. This would catch the wrong
     integrand and the 0 vs 3/2 answer. This one is on hold.
   - **Chemistry:** check that equations balance and units are consistent.
   - **Biology, history and other theory subjects:** the faithfulness check is enough.
4. **Check-question checks**
   - A different model answers the question without seeing the key, and its answer must match the key.
   - Exactly one option should be correct.
   - The wrong options should sound plausible but be clearly wrong.
5. **Teaching-quality rubric**
   - Criteria: right level for the class, easy to say aloud in Hinglish, no repetition, one idea at a time.
   - Each criterion is scored 1–5 with a defined meaning for each score.
   - Run the judge 2–3 times and flag only when the failures agree.
6. **Gate**
   - If a scene passes, it goes to audio.
   - If it fails, it's regenerated using the judge's feedback, at most twice.
   - If it still fails, it's marked for human review and the lesson isn't published.

**Report:** each lesson gets `data/lessons/<slug>/review.json` with each scene's scores and flagged claims, so a teacher
can approve it quickly instead of reading everything.

**Testing the judge**
- The `greens-theorem` lesson already has known errors, so it's the first test set: the checks should flag scenes 1–4
  and should not flag the clean `magnetism-01` scenes.
- Also hand-label a few scenes to measure how many real errors the judge catches and how many false alarms it raises.

**Judge model:** something other than gpt-4o, which writes the scenes. For example, a reasoning model on Azure
(o-series) or a model from another provider.

## 4. The live part needs evaluation too

Once doubts are logged (transcript, intent, confidence, answer, timings):

- Measure routing accuracy against a labelled set.
- Run the same faithfulness and rubric judges on a sample of answers each day.
- Track latency per stage.
- Any prompt change should only ship if these numbers don't get worse.

## 5. Other improvements (from the earlier review)

### Smoother interruption

| Problem | Fix |
|---|---|
| The audio stops mid-word, which sounds broken | Fade the volume out over about 150 ms instead of stopping hard. |
| Resuming mid-sentence is confusing | Go back to the start of the sentence (or about 2 s) and fade in. Optionally add a short bridge line like "Chalo, wapas aate hain…". |
| Long silence after release (STT, then LLM, then ack) | Start uploading audio while the student is still speaking. Use a faster model (gpt-4o-mini) just for routing and keep gpt-4o for answers. Send a compact scene summary instead of all the narration. |
| The student has to hold the button | Tap to talk, with voice-activity detection that stops recording when they go quiet (e.g. Silero VAD in the browser). Later, hands-free barge-in with echo cancellation. |
| Accidental or empty presses still go to the server | Drop recordings under ~0.7 s on the client and resume immediately. |
| "Repeat" requests wait until the end of the step | Treat "repeat" as an instant rewind of the current step. No LLM answer is needed. |
| Gnani allows only one TTS request at a time, so answer lines queue behind each other | Ask Gnani for more concurrency, or use a second TTS provider for live answers. This is the biggest latency limit right now. |
| Asking a question while an answer is playing | Let it interrupt the answer cleanly, either by queueing the new doubt or stopping the current answer. |

### Guest progress

- **Lesson versioning:** a re-prep creates a new version instead of deleting the old one. Today it deletes all
  sessions for that lesson.
- **Resume:** save the scene and step after each step, and offer "Continue from Scene 3?" when the student comes back.
- **Per-lesson progress:** % completed, check score, number of doubts asked, time spent.
- **Concept mastery:** a score per concept, updated from check answers and doubts. This replaces the current
  weak-concept list.
- **Review at session start:** one or two quick questions on weak concepts at the beginning of the next lesson.
- **Link guests to qbee users:** the RN app already keys guests by qbee user id, so progress can follow the student
  across devices.
- **History screen:** past lessons, scores and their doubts with answers.

### AI behaviour

- **Doubt routing**
  - New intents:
    - `prerequisite`: a quick basic explanation now. A prerequisite doubt was routed as off_topic.
    - `confused`: re-explain the last step a different way.
  - Give the classifier the previous doubts as context.
  - If the classifier isn't confident, ask back: "Kya tum ye pooch rahe ho…?"
  - Fix STT mistakes on technical words by passing the lesson's vocabulary into the routing prompt.
- **Answers:** base each answer on the lesson and on what this student is weak at. Keep answers short, and end with a
  quick "samajh aaya?" check.
- **Check questions:** write wrong options based on common misconceptions, with a short explanation for each one.
  When a student answers wrong, re-teach the point with a different example.
- **Safety for minors:** moderate off-topic and inappropriate questions, never ask for personal details, and keep the
  tone age-appropriate.

## 6. Suggested order

1. **Quick wins:** shuffled options, fade and sentence rewind, instant "repeat", dropping short recordings, logging.
2. **`review` stage:** steps 1, 2, 4 and 5 of section 3, with the subject plug-in slot left empty for now.
3. **Progress:** lesson versioning, resume, mastery scores.
4. **Routing and speed:** new intents, gpt-4o-mini for routing, streaming upload, VAD.
5. **Personalisation:** review at session start, re-teaching on wrong answers.
6. **Subject plug-ins:** start with maths (SymPy), which is on hold for now.

## Open questions

1. **Judge model:** another Azure deployment (which one do we have access to?), or a different provider?
2. **When a scene fails:** regenerate automatically and then send to a human, or always send to a human?
3. **Scope of the first version:** steps 1, 2, 4 and 5 of section 3, with the subject plug-in slot left empty?

## Sources

- [Khan Academy: Khanmigo math computation and tutoring updates](https://blog.khanacademy.org/khanmigo-math-computation-and-tutoring-updates/)
- [Khan Academy: Why we're invested in AI math tutoring](https://blog.khanacademy.org/why-were-deeply-invested-in-making-ai-better-at-math-tutoring-and-what-weve-been-up-to-lately/)
- [Khan Academy: Building a better AI tutor](https://blog.khanacademy.org/?p=21314)
- [KTVZ: independent Khanmigo test](https://ktvz.com/?p=1203601)
- [ZenML: Duolingo lesson generation](https://www.zenml.io/llmops-database/ai-powered-lesson-generation-system-for-language-learning)
- [Neowin: Duolingo AI course issue](https://www.neowin.net/news/duolingos-ai-content-push-backfires-an-entire-course-loses-key-words/)
- [LearnLM: Improving Gemini for Learning](https://arxiv.org/pdf/2412.16429)
- [LearnLM-Tutor evaluation](https://arxiv.org/pdf/2407.12687)
- [Production LLM-as-judge in educational AI](https://arxiv.org/pdf/2609.28478)
- [Human validation of multi-LLM evaluation for K-12 materials](https://arxiv.org/pdf/2602.13243)
- [Agent-as-a-Judge survey](https://arxiv.org/pdf/2508.02994)
- [LLM-driven lecture video curation](https://arxiv.org/pdf/2608.16131)
- [SymCode: verifiable math via SymPy](https://arxiv.org/html/2510.25975v2)
- [Programs as verifiers for self-consistency](https://arxiv.org/html/2410.12608v2)
- [Hallucination survey](https://arxiv.org/pdf/2311.05232)
- [Traceloop: detecting hallucinations](https://traceloop.com/blog/automatically-detecting-hallucinations-in-rag-applications)
