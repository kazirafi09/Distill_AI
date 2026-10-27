export const performanceDimensions = ['technical_competency', 'communication', 'response_relevance', 'problem_solving', 'experience_depth', 'job_alignment'] as const
export type PerformanceDimension = typeof performanceDimensions[number]
