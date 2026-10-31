# PROMPT_CONTRACTS

Generated from code by `uv run python -m tests.llm.prompt_contracts` (ТЗ Фазы 3 §7.6,
seam S4). Do not edit by hand. Prompt texts: `quack/prompts/<role>/v<N>.md`.

Models see labels instead of ids: skills `S1…`, topics `T1…`, tasks `K1…`,
misconceptions `M1…` (`quack/llm/labels.py`, WP14.1).

## `teacher@1` → `TeacherOutput`

Output fields:

- `reply_text`: string
- `pedagogical_move`: "explain" | "socratic_question" | "assign_task" | "give_feedback" | "summarize"
- `focus_skill_labels`: list[string]
- `concept_explained`: object | null
- `concept_explained.topic_label`: string
- `concept_explained.explained_skill_labels`: list[string]
- `concept_explained.depth`: number
- `concept_explained.summary`: string
- `assign_task_label`: string | null
- `evaluated_is_correct`: boolean | null
- `evaluated_score`: number | null
- `evaluated_feedback`: string | null

Input blocks: `mode`, `topic_name`, `skills_slice_json`, `misconceptions_json`, `available_tasks_json`, `task_evaluation_json`, `rolling_summary`, `recent_turns_json`, `student_text`, `guard_reason`
Label fields: `focus_skill_labels`, `assign_task_label`, `concept_explained.topic_label`, `concept_explained.explained_skill_labels`
Markers: "memory temporarily unavailable"

## `extractor@2` → `ExtractorOutputV2`

Output fields:

- `entries`: list[object]
- `entries.kind`: "skill_observation" | "misconception_suspicion"
- `entries.skill`: string
- `entries.misconception`: string | null
- `entries.level`: integer | null
- `entries.outcome`: "success" | "failure" | "bypass" | null
- `entries.help`: "none" | "hint_direction" | "hint_key_step" | "hint_near_solution" | "after_example" | "solution_shown" | null
- `entries.gap_type`: "no_idea" | "not_recognizing_when" | "confuses_similar" | "execution_error" | "slip" | "misconception" | null
- `entries.quote`: string
- `entries.events`: list[integer]

Input blocks: `events_before`, `episode`, `candidate_skills`, `known_misconceptions`
Label fields: `entries.skill`, `entries.misconception`

## `evaluator@1` → `EvaluatorOutput`

Output fields:

- `items`: list[object]
- `items.item`: string
- `items.status`: "found" | "missing" | "wrong"
- `items.quote`: string | null
- `verdict`: "correct" | "partial" | "incorrect" | "unclear"
- `confidence`: number
- `misconception`: object | null
- `misconception.label`: string
- `misconception.quote`: string
- `note`: string

Input blocks: `prompt_text`, `rubric`, `reference_solution_json`, `known_misconceptions`, `student_answer`
Label fields: `misconception.label`

## `task_gen@1` → `TaskGenOutput`

Output fields:

- `statement`: string
- `options`: list[object]
- `options.label`: string
- `options.text`: string
- `options.correct`: boolean
- `options.catches`: string | null
- `answer`: object
- `answer.type`: "choice" | "number" | "expression" | "set" | "vector" | "quantity" | "proof" | "free_text"
- `answer.value`: string
- `answer.units`: string | null
- `solution`: string
- `hints`: list[string]
- `tests`: list[object]
- `tests.skill`: string
- `tests.level`: integer
- `method_named`: boolean
- `variant_of`: string | null
- `form`: "multistep" | "explain_why" | "what_breaks" | null
- `pair_task`: object | null
- `pair_task.statement`: string
- `pair_task.answer`: string
- `pair_task.differs_by`: string

Input blocks: `topic_name`, `target_skill`, `target_level`, `kind`, `known_misconceptions`, `excluded_families`
Label fields: `options.catches`, `tests.skill`, `variant_of`

## `structurer@1` → `StructurerOutput`

Output fields:

- `topics`: list[object]
- `topics.slug`: string
- `topics.name`: string
- `topics.description`: string
- `skills`: list[object]
- `skills.slug`: string
- `skills.topic_slug`: string
- `skills.name`: string
- `skills.description`: string
- `prereqs`: list[object]
- `prereqs.from_topic_slug`: string
- `prereqs.to_topic_slug`: string
- `skill_deps`: list[object]
- `skill_deps.skill_slug`: string
- `skill_deps.depends_on_skill_slug`: string
- `misconceptions`: list[object]
- `misconceptions.skill_slug`: string
- `misconceptions.name`: string
- `misconceptions.description`: string

Input blocks: `domain_name`, `goals`, `background`, `focus_areas`

## `curator@1` → `CuratorOutput`

Output fields:

- `promotions`: list[object]
- `promotions.skill_id`: string
- `promotions.topic_id`: string
- `promotions.name`: string
- `promotions.description`: string
- `skill_merges`: list[object]
- `skill_merges.source_id`: string
- `skill_merges.target_id`: string
- `skill_merges.reason`: string
- `topic_merges`: list[object]
- `topic_merges.source_id`: string
- `topic_merges.target_id`: string
- `topic_merges.reason`: string
- `misconception_merges`: list[object]
- `misconception_merges.source_id`: string
- `misconception_merges.target_id`: string
- `misconception_merges.reason`: string

Input blocks: `domain_name`, `topics_json`, `skills_json`, `misconceptions_json`

## `diagnostic@1` → `DiagnosticOutput`

Output fields:

- `prompt_text`: string
- `options`: list[string]
- `correct_answer`: string
- `is_correct`: boolean
- `score`: number
- `tested_depth`: number

Input blocks: `mode`, `topic_name`, `skill_name`, `question_prompt`, `student_answer`

## `artifacts@1` → `ArtifactGenOutput`

Output fields:

- `title`: string
- `text`: string

Input blocks: `mode`, `title_context`, `skills_summary_json`, `misconceptions_summary_json`, `dialogue_excerpt_json`, `previous_content_md`

## Edit points after G1

- teacher tool names, if tools appear (R9);
- names of label fields (`LABEL_FIELDS`);
- `gap_type` values of `extractor@2` (П-6.4);
- the marker of unavailable memory (`memory temporarily unavailable`);
- the reply language of the teacher from the profile.
