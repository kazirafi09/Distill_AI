"""Agreed F5 rubric. Arithmetic is deterministic; judgments are AI assessments."""

from decimal import Decimal


RUBRIC_VERSION = "interview-answer-1"
DIMENSIONS = ("relevance", "accuracy", "completeness", "articulation")
ANCHORS = {
    "relevance": ["Does not address the question", "Mostly unrelated", "Partly addresses the question",
                  "Directly addresses most of the question", "Directly addresses the question throughout"],
    "accuracy": ["Central explanation is incorrect", "Major errors with limited correct content",
                 "Mix of correct and incorrect claims", "Mostly correct with minor errors",
                 "Correct within the question's scope"],
    "completeness": ["No requested content supplied", "Addresses very little", "Covers some requested parts",
                     "Covers most parts with minor omissions", "Covers all requested parts adequately"],
    "articulation": ["Cannot be followed despite usable transcription", "Difficult to follow", "Partly clear",
                     "Mostly clear", "Clear and coherent explanation"],
}
POLICY = """Score 0..4 using the supplied dimension anchors; not assessable is null, not zero.
An explicit I-don't-know answer has completeness 0; relevance, accuracy and articulation are null.
An unasked requirement has no performance score. An aborted prompt is excluded.
An interruption with no usable answer is unassessed. A resumed answer remains in its original group.
Evaluate self-corrections as a whole while mentioning initial errors and whether prompting was needed;
do not invent a fixed prompting deduction. Judge articulation from usable text structure only,
never accent, grammar perfection, personality or transcription damage. An articulate wrong answer can
have strong articulation and weak accuracy. Keep dimensions independent.
Accuracy may use supplied reviewed references and model knowledge. Label model-only judgments
unverified; model knowledge is not an external citation. Self-reported experience is not verified fact.
Do not invent code, unstated requirements, excerpts, technical references or confidence percentages.
"""

# Guidance clarifies the agreed anchors; it does not assign fixture-specific scores.
DIMENSION_GUIDANCE = {
    "relevance": """Judge whether the response addresses the actual question. Missing requested detail
belongs in completeness; an on-topic incorrect claim can still be fully relevant.
For a deduction, identify specific off-topic content or a part of the question not addressed,
without treating an incomplete explanation as automatically unrelated.""",
    "accuracy": """Judge the correctness of technical claims actually made, not how many details are supplied.
Do not deduct merely for missing code, limited recall or incomplete coverage; those concern completeness.
For a deduction, identify a specific incorrect or imprecise claim and its factual basis.
Do not expand an unfinished term into a complete correct assertion. Evaluate corrections as a whole,
explicitly preserving the initial error and whether the interviewer prompted the correction.
Prefer supplied reviewed references when they support the assessed claims; cite exact reference excerpts.
Use model_knowledge or mixed, with the existing warning, for claims not supported by those references.""",
    "completeness": """Identify the parts explicitly requested by the question and assess their coverage across
the whole answer, including follow-ups and revisits. For a deduction, name the requested element missing
or inadequately supplied. Do not invent a fuller feature set, exact code, production deployment or other
requirements that were not asked. Keep missing content distinct from an incorrect claim; do not reduce
completeness solely because accuracy is low. Preserve initial uncertainty and later recovery in the
explanation without inventing a fixed prompting deduction.""",
    "articulation": """Reconstruct each speaker's continued sentences across transcript rows before judging
candidate reasoning and organization. Do not deduct for row splitting, interviewer interruptions,
reconnects, filler alone, limited technical recall or an incorrect but clearly expressed belief.
For a deduction, identify a specific difficulty following the candidate's usable reasoning or structure.
Consider the whole answer, including coherent portions; do not judge only a hesitant ending.
If the reconstructed answer is clear and coherent with no specific structural difficulty, select anchor 4;
do not use anchor 3 as a default for a clear answer containing uncertainty or a corrected technical error.
Saying that a detail cannot be recalled can be clear communication; it is not itself an articulation defect.
Do not infer delivery, fluency or word-level timing from transcript fragments.""",
}


def dimension_summaries(results):
    summaries = {}
    for name in DIMENSIONS:
        scores = [r["evaluations"][name]["score"] for r in results if r["evaluations"][name]["score"] is not None]
        count = len(scores)
        percentage = Decimal(sum(scores)) * 100 / (4 * count) if count else None
        summaries[name] = {
            "score_100": str(percentage.quantize(Decimal("0.01"))) if percentage is not None else None,
            "assessed_count": count, "total_groups": len(results), "not_assessable_count": len(results) - count,
            "formula": "100 * sum(assessed 0..4 scores) / (4 * assessed_count)",
        }
    return summaries
