"""Opt-in real providers/shared pipeline and atomic Apply in a disposable schema.

Run with test_wind_tunnel_duration.py on Python's import path. No public-schema
jobs are loaded. Only the conversation UI response is stubbed after the real save.
"""
import json
import os
import unittest
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import get_settings
from app.db.base import Base
from app.models.user import User
from app.models.jd import JDConversation, JobDescription, DiscordJobPost
from app.models.assistant import AssistantConversation
from app.models.wind_tunnel import WindTunnelRun, WindTunnelDocument, WindTunnelAssessment, WindTunnelSuggestion
from app.services.jd.edit_policy import document_edit_version
from app.services.jd.screening_profiles import public_jd_narrative
from app.services.wind_tunnel import storage, review, suggestions
from app.services.wind_tunnel import generation
from app.services.wind_tunnel.indexing import remove_vectors
from app.workflows.wind_tunnel.runtime import analysis_versions, run_analysis, run_wind_tunnel
from test_wind_tunnel_duration import JD as REQUIREMENTS_JD, PROFILE

JD = REQUIREMENTS_JD + '''

## Role overview
Develop and maintain backend services for an internal reporting platform. The role involves backend service development and maintenance, with developing and maintaining backend services as its primary purpose.

## Responsibilities
Build reporting REST APIs using Python and FastAPI, including developing reporting request endpoints in Python with FastAPI as part of REST API development for reporting requests.
Design PostgreSQL schemas and optimize PostgreSQL queries for reporting data, including PostgreSQL schema design and PostgreSQL query optimization for the reporting data stored in PostgreSQL.
Write unit and integration tests. Package services using Docker and contribute to CI/CD workflows. Use Redis for caching.
'''


@unittest.skipUnless(os.environ.get('RUN_F11_MODEL_TESTS') == '1' and os.environ.get('RUN_F11_DB_TESTS') == '1',
                     'Opt in to invented live pipeline/disposable SQL acceptance')
class DurationPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_suite_assessments_exact_edits_and_reviewed_apply(self):
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        schema = 'f11_duration_' + uuid4().hex
        run_id = owner_id = None
        with TemporaryDirectory(prefix='f11-duration-') as directory:
            root = Path(directory)
            async with engine.begin() as conn:
                await conn.execute(CreateSchema(schema))
            scoped = engine.execution_options(schema_translate_map={None: schema})
            factory = async_sessionmaker(scoped, expire_on_commit=False)
            try:
                tables = [c.__table__ for c in (User, JDConversation, JobDescription, DiscordJobPost,
                    AssistantConversation, WindTunnelRun, WindTunnelDocument, WindTunnelAssessment, WindTunnelSuggestion)]
                async with scoped.begin() as conn:
                    await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
                versions = await analysis_versions()
                async with factory() as db:
                    user = User(email='invented-duration@example.invalid', password_hash='unused', role='recruiter')
                    db.add(user); await db.flush()
                    conversation = JDConversation(recruiter_id=user.id)
                    db.add(conversation); await db.flush()
                    db.add(AssistantConversation(user_id=user.id, jd_conversation_id=conversation.id))
                    job = JobDescription(conversation_id=conversation.id, title='Backend developer', content=JD,
                        requirements='Invented role for F11 acceptance only.', screening_profile=PROFILE, profile_version=1)
                    db.add(job); await db.flush()
                    owner_id, job_id = user.id, job.id
                    version = document_edit_version(job)
                    run = await storage.create_run(db, job_id, owner_id, versions, trigger='more', request_id=str(uuid4()))
                    run_id = run.id
                    await db.commit()
                print(json.dumps(dict(stage='started', schema=schema, run_id=str(run_id), generator=versions['generation']['generator'])), flush=True)
                actual_review = generation.review_document
                async def traced_review(payload, text, criteria, persona, plan, execution, **kwargs):
                    try:
                        return await actual_review(payload, text, criteria, persona, plan, execution, **kwargs)
                    except (generation.CVValidationError, generation.PersonaDifferenceError) as exc:
                        print(json.dumps(dict(stage='invented_cv_rejected', persona=persona, error=str(exc), details=exc.details)), flush=True)
                        if os.environ.get('F11_REJECTED_OUTPUT'):
                            Path(os.environ['F11_REJECTED_OUTPUT']).write_text(json.dumps(dict(persona=persona, error=str(exc), details=exc.details, invented_cv_text=text)), encoding='utf-8')
                        raise
                with patch.object(generation, 'review_document', side_effect=traced_review):
                    analysis = await run_analysis(run_id, owner_id, root=root, session_factory=factory)
                async with factory() as db:
                    snapshot_run = await db.get(WindTunnelRun, run_id)
                    print(json.dumps(dict(stage='analysis_result', coverage=analysis.get('coverage'),
                        generation_status=snapshot_run.resources.get('generation', {}).get('status'),
                        failures=snapshot_run.resources.get('generation', {}).get('failures'),
                        generation_attempts=snapshot_run.resources.get('generation', {}).get('attempts'))), flush=True)
                self.assertEqual(analysis['coverage']['completed_assessments'], 2)
                # Optional invented-only replay packet makes a downstream model
                # failure diagnosable after disposable SQL/vector/file cleanup.
                if os.environ.get('F11_CONTEXT_OUTPUT'):
                    async with factory() as db:
                        run = await db.get(WindTunnelRun, run_id)
                        context = await suggestions.load_context(db, run_id, owner_id, run.resources['analysis']['token'], root=root)
                        packet = {name: getattr(context, name) for name in ('snapshot', 'source_version', 'evaluation_id', 'report', 'findings', 'versions')}
                        packet['evidence'] = list(context.evidence.values())
                        Path(os.environ['F11_CONTEXT_OUTPUT']).write_text(json.dumps(packet), encoding='utf-8')
                result = await run_wind_tunnel(run_id, owner_id, root=root, session_factory=factory)
                async with factory() as db:
                    run = await db.get(WindTunnelRun, run_id)
                    assessments = list(await db.scalars(select(WindTunnelAssessment).where(WindTunnelAssessment.run_id == run_id)))
                    documents = {d.id: d.persona_id for d in await db.scalars(select(WindTunnelDocument).where(WindTunnelDocument.run_id == run_id))}
                    print(json.dumps(dict(stage='pipeline_result', status=result['status'], error=result['error_code'],
                        generation_failures=run.resources['generation'].get('failures'),
                        generation_attempts=run.resources['generation'].get('attempts'),
                        suggestion_state=run.resources.get('suggestions'),
                        assessments=[dict(persona=documents[a.document_id], status=a.payload.get('status'), error=a.payload.get('error_code'),
                            matches=[dict(criterion=m['requirement_id'], decision=m['decision']) for m in a.payload.get('matches', [])],
                            score=a.payload.get('score')) for a in assessments])), flush=True)
                    self.assertEqual(len(assessments), 2)
                    self.assertEqual(set(documents.values()), {'strong_all_rounder', 'keyword_stuffer'})
                    self.assertTrue(all(a.payload['status'] == 'completed' and len(a.payload['matches']) == 6 for a in assessments))
                    self.assertEqual(result['analysis']['coverage']['completed_assessments'], 2)
                    rows = list(await db.scalars(select(WindTunnelSuggestion).where(WindTunnelSuggestion.run_id == run_id)))
                    self.assertEqual(len(rows), 3, 'This deliberately wordy invented role should demonstrate the three-proposal target.')
                    selected_ids = [row.id for row in rows]
                    preview, _ = await review.preview(db, job_id, owner_id, selected_ids, version)
                    self.assertEqual((await db.get(JobDescription, job_id)).content, JD)
                    print(json.dumps(dict(stage='reviewed_preview', selected_count=len(selected_ids), patches=[p for row in rows for p in row.payload['proposal']['patches']])), flush=True)
                    graph = SimpleNamespace(get_state=lambda config: SimpleNamespace(values={}))
                    with patch('app.services.jd.editing.load_jd_conversation', new=AsyncMock(return_value={'status': 'approved'})):
                        await review.apply(db, graph, job_id, owner_id, selected_ids, version, preview['review_token'], True)
                    job = await db.get(JobDescription, job_id, populate_existing=True)
                    # Description-only edits need not increment the screening profile version.
                    self.assertEqual(public_jd_narrative(job.content), public_jd_narrative(preview['edit']['content']))
                    self.assertNotEqual(document_edit_version(job), version)
                    for row in rows:
                        await db.refresh(row)
                        self.assertEqual(row.status, 'applied')
                    print(json.dumps(dict(stage='applied', profile_version=job.profile_version, applied_count=len(rows))), flush=True)
            finally:
                if run_id is not None:
                    await storage.cleanup_run(run_id, owner_id, root=root, session_factory=factory,
                        remove_vectors=partial(remove_vectors, session_factory=factory))
                assert schema.startswith('f11_duration_') and len(schema) == len('f11_duration_') + 32
                async with engine.begin() as conn:
                    await conn.execute(DropSchema(schema, cascade=True))
                await engine.dispose()
                print(json.dumps(dict(stage='cleaned', schema=schema)), flush=True)


if __name__ == '__main__':
    unittest.main()
