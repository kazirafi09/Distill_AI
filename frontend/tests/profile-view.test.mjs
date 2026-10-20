import assert from 'node:assert/strict'
import { test } from 'node:test'
import { candidateFileLabel, profileItems, profileLink, profileRecord, profileText } from '../src/features/cv/profileView.ts'

test('profile display reads evidence-backed claims and older plain strings without exposing objects', () => {
  assert.equal(profileText({ value: 'Backend Developer', evidence: [{ quote: 'Backend Developer' }] }), 'Backend Developer')
  assert.equal(profileText('  Python  '), 'Python')
  for (const value of [null, undefined, {}, { employer: { value: 'Company' } }, [], { value: null }]) assert.equal(profileText(value), '')
  assert.deepEqual(profileRecord(null), {})
  assert.deepEqual(profileItems([{ value: 'PHP' }, 'Laravel', { value: 'PHP' }, null, {}]), ['PHP', 'Laravel'])
})

test('profile links accept web addresses and leave unsafe or incomplete values as plain text', () => {
  assert.equal(profileLink({ value: 'https://github.com/example' }), 'https://github.com/example')
  for (const value of ['javascript:alert(1)', 'data:text/html,test', 'file:///private', 'github.com/example', null]) assert.equal(profileLink(value), null)
})

test('CV display names use the candidate identity without changing source filenames', () => {
  const original = 'sample_junior_laravel_developer_cv - Kazi Rafi.pdf'
  assert.equal(candidateFileLabel('Arif Rahman', original), 'Arif Rahman — CV.pdf')
  assert.equal(original, 'sample_junior_laravel_developer_cv - Kazi Rafi.pdf')
  assert.equal(candidateFileLabel('Arif Rahman', 'Second - Someone Else.DOCX', 1, 2), 'Arif Rahman — CV 2.docx')
  assert.equal(candidateFileLabel('', null), 'Candidate — CV')
})
