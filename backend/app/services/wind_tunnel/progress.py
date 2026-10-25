"""Progress derived from durable work, independent of model-call duration."""
from datetime import datetime, timezone

from app.services.wind_tunnel.policy import PERSONAS, personas_for, repetitions_for


def progress_for(task, run, documents, assessments):
    resources = run.resources if run else {}
    stage = resources.get('analysis', {}).get('stage', 'load_snapshot')
    count = len(personas_for(run.versions)) if run else len(PERSONAS)
    total = count * repetitions_for(run.versions) if run else count
    completed = sum(a.payload.get('status') == 'completed' for a in assessments)
    matches = sum(len(a.payload.get('matches', [])) for a in assessments)
    criteria = len(run.snapshot['screening_profile']['criteria']) if run else 0
    stages = {
        'load_snapshot': (0, 'Preparing analysis'),
        'generate_suite': (1, 'Creating and validating CVs'),
        'prepare_extraction': (2, 'Preparing evidence'),
        'assess_cohorts': (3, 'Assessing CVs'),
        'evaluate_findings': (4, 'Evaluating results'),
        'analysis_complete': (5, 'Reviewing improvements'),
        'generate_suggestions': (5, 'Drafting improvements'),
        'validate_suggestions': (5, 'Checking proposed improvements'),
        'suggestions_complete': (5, 'Saving reviewed results'),
    }
    step, label = stages.get(stage, (0, 'Preparing analysis'))
    if task.status == 'queued':
        step, label = 0, 'Waiting for the worker'
    elif task.status == 'completed':
        step, label = 6, 'Complete'
    elif task.status != 'running':
        label = {'limited': 'Analysis needs attention', 'failed': 'Analysis failed',
                 'cancelled': 'Cancelled', 'stale': 'Job changed'}.get(task.status, label)
    return {'step': step, 'total_steps': 6, 'label': label,
            'documents_completed': len(documents), 'documents_total': count,
            'assessments_completed': completed, 'assessments_total': total,
            'criteria_completed': matches, 'criteria_total': total * criteria,
            'worker_connected': bool(task.status == 'running' and task.lease_until and task.lease_until > datetime.now(timezone.utc)),
            'updated_at': max([task.updated_at] + ([run.updated_at] if run else []) + [a.updated_at for a in assessments])}
