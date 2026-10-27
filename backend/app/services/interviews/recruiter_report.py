"""Concise recruiter projection of saved judgments; never invokes a model or rescores."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


def number(value):
    if value is None:
        return "Not assessed"
    try:
        decimal = Decimal(str(value))
        if not decimal.is_finite():
            return "Not assessed"
        return format(decimal.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP), 'f').rstrip('0').rstrip('.')
    except (InvalidOperation, ValueError):
        return "Not assessed"


def concise(text, limit=165):
    """Display an extract, preserving the original wording and marking omissions."""
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences[0]) <= limit:
        return sentences[0]
    return text[:limit - 3].rsplit(' ', 1)[0].rstrip(',;:') + '...'


def assemble(meta, inputs, f5, f6, cv, include_evidence=False):
    from app.services.interviews.full_report import clock, label
    from app.services.interviews.rubric import DIMENSIONS

    criteria = inputs['criteria']
    aliases = {c['id']: f'C{i + 1}' for i, c in enumerate(criteria)}
    turns = {t['id']: t for t in inputs['prepared']['turns']}
    entries, keys, by_criterion = [], {}, {}
    source_refs = {}

    def register(evidence, criterion_ids=()):
        refs = []
        for item in evidence:
            tid = item.get('turn_id')
            turn = turns.get(tid)
            if tid:
                key = ('interview', tid)
                # One complete stored turn replaces repeated, overlapping excerpts.
                quote = turn['text'] if turn else item['quote']
                source = (f"Interview | {label(turn.get('speaker_role', 'Not recorded'))} | "
                    f"{clock(turn.get('session_start_ms'))}-{clock(turn.get('session_end_ms'))}") if turn else 'Interview | source turn not available'
                kind = 'Interview'
            else:
                key = ('cv', item.get('file_version_id'), item.get('parse_id'), item.get('source_id'), item.get('start'), item.get('end'), item['quote'])
                quote = item['quote']
                source = f"CV | {item.get('source_id') or 'Page not recorded'}"
                kind = 'CV'
            if key not in keys:
                keys[key] = f'E{len(entries) + 1}'
                entries.append({'id': keys[key], 'kind': kind, 'source': source, 'quote': quote})
            ref = keys[key]
            if ref not in refs:
                refs.append(ref)
            for cid in criterion_ids:
                by_criterion.setdefault(cid, set()).add(ref)
        return refs

    for match in (cv or {}).get('matches', []):
        register(match.get('evidence', []), [match['requirement_id']])
    for result in f5['results']:
        for evaluation in result['evaluations'].values():
            register(evaluation.get('evidence', []), result['group']['criterion_ids'])
    for source in (f6 or {}).get('source_registry', []):
        source_refs[source['id']] = register(source.get('evidence', []), source.get('criterion_ids', []))

    sections = []

    def section(title, blocks=None, **extra):
        result = {'title': title, 'blocks': blocks or [], **extra}
        sections.append(result)
        return result

    def block(text, kind='text', refs=()):
        result = {'kind': kind, 'text': text}
        if include_evidence and refs:
            result['citation_ids'] = list(dict.fromkeys(refs))
        return result

    findings = (f6 or {}).get('findings')
    overview = section('Assessment summary')
    if inputs['prepared'].get('text_only'):
        overview['blocks'].append(block(inputs['prepared']['timing_warning'], 'warning'))
    if findings:
        position = findings['suitability']
        overview['blocks'].append(block(label(position['position']), 'heading'))
        refs = [ref for sid in position['source_ids'] for ref in source_refs.get(sid, [])]
        overview['blocks'].append(block(concise(position['rationale'], 260), refs=refs))
    else:
        overview['blocks'].append(block('Interview analysis only. A consolidated assessment has not been completed.', 'note'))

    # Surface an explicit source discrepancy without drawing a hiring conclusion.
    cv_quotes = [e['quote'] for m in (cv or {}).get('matches', []) for e in m.get('evidence', [])]
    cv_role = next((q for q in cv_quotes if re.search(r'\b(developer|engineer|manager)\b[^\n]{0,60}\b(since|20\d{2})\b', q, re.I)), None)
    no_experience = next((t for t in turns.values() if t['speaker_role'] == 'candidate' and re.search(r'\bno (?:prior )?professional experience\b', t['text'], re.I)), None)
    if cv_role and no_experience:
        overview['blocks'].append(block('Information to verify: the CV lists a professional role, while an interview answer says there is no professional experience.', 'warning'))

    cv_value = number(cv['score'].get('aggregate')) if cv else 'Not available'
    score_rows = [['CV match', cv_value + '/100' if cv_value not in {'Not available', 'Not assessed'} else cv_value, 'Documented CV evidence']]
    for dimension in DIMENSIONS:
        summary = f5['dimension_summaries'][dimension]
        count = f"{summary['assessed_count']} of {summary['total_groups']} answers"
        if summary['not_assessable_count']:
            count += f"; {summary['not_assessable_count']} not assessed"
        value = number(summary['score_100'])
        score_rows.append([label(dimension), value + '/100' if value != 'Not assessed' else value, count])
    score_notes = [block('Interview scores are AI judgments of the assessed answers. They do not verify professional experience. No combined score or ranking is produced.', 'note')]
    breakdown = (cv or {}).get('score', {}).get('dimensions', [])
    if breakdown:
        score_notes.append(block('CV breakdown: ' + '; '.join(f"{label(d['dimension'])} {number(d.get('score'))}/100 ({number(d.get('approved_percentage'))}% weight)" for d in breakdown), 'note'))
    section('Scores - kept separate', score_notes,
        table={'caption': 'Saved scores', 'columns': ['Measure', 'Score', 'Basis'], 'rows': score_rows, 'widths': [.27, .18, .55]})

    def finding_section(key, title, maximum=3):
        values = (findings or {}).get(key, [])
        if not values:
            return
        blocks = []
        for finding in values[:maximum]:
            refs = [ref for sid in finding['source_ids'] for ref in source_refs.get(sid, [])]
            prefix = aliases.get(finding.get('criterion_id'), '')
            blocks.append(block(f"{prefix}: {concise(finding['text'], 150)}".lstrip(': '), refs=refs))
        if len(values) > maximum:
            blocks.append(block(f"{len(values) - maximum} additional items are available in the full technical report.", 'note'))
        section(title, blocks)

    finding_section('strengths', 'Strengths')
    finding_section('weaknesses', 'Observed limitations', 2)
    gaps = (findings or {}).get('evidence_gaps', [])
    cv_only, other_gaps = [], []
    registry = {s['id']: s for s in (f6 or {}).get('source_registry', [])}
    for gap in gaps:
        if gap['source_ids'] and all(registry.get(sid, {}).get('kind') == 'cv' for sid in gap['source_ids']):
            cv_only.append(aliases.get(gap['criterion_id'], gap['criterion_id']))
        else:
            other_gaps.append(gap)
    gap_blocks = []
    if cv_only:
        gap_blocks.append(block('CV evidence gaps: ' + ', '.join(dict.fromkeys(cv_only)) + '. See the requirement table.'))
    for gap in other_gaps[:2]:
        gap_blocks.append(block(concise(gap['text'], 150), refs=[ref for sid in gap['source_ids'] for ref in source_refs.get(sid, [])]))
    if len(other_gaps) > 2:
        gap_blocks.append(block(f'{len(other_gaps) - 2} additional evidence gaps are available in the full technical report.', 'note'))
    if gap_blocks:
        section('Evidence gaps', gap_blocks)
    finding_section('recommended_areas', 'Topics to verify next')

    coverage = {c['criterion_id']: c for c in f5.get('coverage', [])}
    cv_matches = {c['requirement_id']: c for c in (cv or {}).get('matches', [])}
    coverage_rows = []
    for criterion in criteria:
        cid = criterion['id']
        decision = cv_matches.get(cid, {}).get('decision')
        cv_label = {'met': 'Evidenced', 'evidenced': 'Evidenced', 'insufficient_evidence': 'Not evidenced', 'not_met': 'Not met', 'needs_review': 'Needs review'}.get(decision, label(decision) if decision else 'Not recorded')
        exploration = coverage.get(cid, {}).get('exploration')
        asked = {'explored': 'Asked', 'unasked': 'Not asked', 'partly_explored': 'Partly asked'}.get(exploration, 'Not recorded')
        row = [f"{aliases[cid]} {criterion['text']}", cv_label if cv else 'Not available', asked]
        if include_evidence:
            row.append(', '.join(sorted(by_criterion.get(cid, []), key=lambda ref: int(ref[1:]))) or 'No cited quote')
        coverage_rows.append(row)
    section('Requirement coverage', [block('Asked means discussed, not verified ability. Missing CV evidence and questions not asked are not proof of inability.', 'note')], page_break_before=True,
        table={'caption': 'Every saved requirement', 'columns': ['Requirement', 'CV evidence', 'Interview'] + (['Evidence'] if include_evidence else []),
            'rows': coverage_rows, 'widths': [.49, .18, .16, .17] if include_evidence else [.60, .23, .17]})
    question_rows = []
    for result in f5['results']:
        question_rows.append([result['group']['topic']] + [number(result['evaluations'][dimension]['score_100']) for dimension in DIMENSIONS])
    section('Interview overview', [block('Scores are out of 100. Not assessed is excluded from averages. Detailed questions, answers, and explanations remain in the full technical report.', 'note')],
        table={'caption': 'One row per question group', 'columns': ['Question topic', 'Relevance', 'Accuracy', 'Completeness', 'Articulation'],
            'rows': question_rows, 'widths': [.48, .13, .13, .13, .13]})
    if include_evidence:
        blocks = [block('Exact saved text, printed once per interview turn or CV excerpt. Technical source identifiers are available in the full technical report.', 'note')]
        for entry in entries:
            blocks.extend([{'kind': 'heading', 'text': f"{entry['id']} | {entry['kind']}", 'anchor': entry['id']},
                block(entry['source'], 'note'), block(entry['quote'], 'quote')])
        if not entries:
            blocks.append(block('No quoted evidence was stored for this assessment.', 'note'))
        section('Evidence appendix', blocks, id='evidence-appendix', page_break_before=True)

    versions = {'criteria': inputs['profile_version'], 'transcript': inputs['prepared'].get('source_version') or inputs['prepared'].get('fixture_revision'), 'interview_rubric': f5.get('rubric_version')}
    return {'schema': 'distill-recruiter-report-v1', 'report_type': 'recruiter', 'include_evidence': include_evidence,
        'title': 'Candidate assessment report', 'metadata': {**meta, 'source_versions': versions}, 'sections': sections,
        'filename': f"distill-assessment-{meta['run']['id']}{'-evidence' if include_evidence else ''}.pdf"}
