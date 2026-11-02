# PROMPT_CONTRACTS

Generated from code by `uv run python -m tests.llm.prompt_contracts` (ТЗ Фазы 3 §7.6,
seam S4; ТЗ Ф4 §7.6, seam S8). Do not edit by hand. Prompt texts:
`quack/prompts/<role>/v<N>.md`.

Models see labels instead of ids: skills `S1…`, topics `T1…`, tasks `K1…`,
misconceptions `M1…`; in Phase 4 also hubs `H1…`, material chunks `P1…`, cards `C1…`,
agent requests `R1…`, evidence `E1…`, canvas lines `L1…` (`quack/llm/labels.py`,
WP14.1, D-B-108).

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

## `teacher@2` → `TeacherOutputV2`

Output fields:

- `p1_head`: object
- `p1_head.intent`: "clarify_goal" | "explain" | "worked_example" | "practice" | "retrieval" | "diagnose" | "hint" | "feedback" | "answer_question" | "plan" | "wrap_up"
- `p1_head.targetSkills`: list[string]
- `p1_head.verdict_stance`: "code" | "formative" | null
- `p1_head.branchType`: "corrective" | "curiosity" | "external" | null
- `p1_head.approach`: "example_first" | "attempt_first" | "small_steps" | "prediction_first" | "faded_example" | "interleaving" | "contrast_pair" | "refutation" | "explain_in_own_words" | "visualization" | "none"
- `p2_body`: object
- `p2_body.blocks`: list[object]
- `p2_body.blocks.block_id`: string
- `p2_body.blocks.text`: string | null
- `p2_body.blocks.element_id`: string | null
- `p3_markup`: object
- `p3_markup.tasks`: list[object]
- `p3_markup.tasks.tests`: list[object]
- `p3_markup.tasks.tests.skill`: string | null
- `p3_markup.tasks.tests.new_skill`: object | null
- `p3_markup.tasks.tests.new_skill.name`: string
- `p3_markup.tasks.tests.new_skill.topic_hint`: string
- `p3_markup.tasks.tests.level`: integer
- `p3_markup.tasks.methodNamed`: boolean
- `p3_markup.tasks.variantOf`: string | null
- `p3_markup.tasks.answer`: object
- `p3_markup.tasks.answer.type`: "choice" | "number" | "expression" | "set" | "vector" | "quantity" | "proof" | "free_text" | "drawing" | "short_text"
- `p3_markup.tasks.answer.value`: object | null
- `p3_markup.tasks.answer.value.latex`: string
- `p3_markup.tasks.answer.value.sympy`: string
- `p3_markup.tasks.answer.items`: list[object] | null
- `p3_markup.tasks.answer.items.latex`: string
- `p3_markup.tasks.answer.items.sympy`: string
- `p3_markup.tasks.answer.options`: list[object] | null
- `p3_markup.tasks.answer.options.option_id`: string
- `p3_markup.tasks.answer.options.text`: string
- `p3_markup.tasks.answer.options.correct`: boolean
- `p3_markup.tasks.answer.accepted`: list[string] | null
- `p3_markup.tasks.answer.tol_abs`: number | null
- `p3_markup.tasks.answer.tol_rel`: number | null
- `p3_markup.tasks.answer.sig_digits`: integer | null
- `p3_markup.tasks.answer.unit`: string | null
- `p3_markup.tasks.answer.mode`: "equal" | "up_to_constant" | "solution_set" | "interval" | "form" | null
- `p3_markup.tasks.answer.form_target`: "factored" | "simplified" | null
- `p3_markup.tasks.answer.variables`: list[object] | null
- `p3_markup.tasks.answer.variables.name`: string
- `p3_markup.tasks.answer.variables.assumptions`: list["real" | "positive" | "nonnegative" | "integer" | "nonzero"]
- `p3_markup.tasks.answer.key_ideas`: list[string] | null
- `p3_markup.tasks.options`: list[object] | null
- `p3_markup.tasks.options.option_id`: string
- `p3_markup.tasks.options.misconception`: string | null
- `p3_markup.tasks.options.hypothesis`: string | null
- `p3_markup.tasks.options.neighbor_idea`: boolean
- `p3_markup.tasks.pairTaskId`: string | null
- `p3_markup.tasks.form`: "multistep" | "explain_why" | "what_breaks" | null
- `p3_markup.tasks.parts`: list[object] | null
- `p3_markup.tasks.parts.part`: string
- `p3_markup.tasks.parts.answer`: object
- `p3_markup.tasks.parts.answer.type`: "choice" | "number" | "expression" | "set" | "vector" | "quantity" | "proof" | "free_text" | "drawing" | "short_text"
- `p3_markup.tasks.parts.answer.value`: object | null
- `p3_markup.tasks.parts.answer.value.latex`: string
- `p3_markup.tasks.parts.answer.value.sympy`: string
- `p3_markup.tasks.parts.answer.items`: list[object] | null
- `p3_markup.tasks.parts.answer.items.latex`: string
- `p3_markup.tasks.parts.answer.items.sympy`: string
- `p3_markup.tasks.parts.answer.options`: list[object] | null
- `p3_markup.tasks.parts.answer.options.option_id`: string
- `p3_markup.tasks.parts.answer.options.text`: string
- `p3_markup.tasks.parts.answer.options.correct`: boolean
- `p3_markup.tasks.parts.answer.accepted`: list[string] | null
- `p3_markup.tasks.parts.answer.tol_abs`: number | null
- `p3_markup.tasks.parts.answer.tol_rel`: number | null
- `p3_markup.tasks.parts.answer.sig_digits`: integer | null
- `p3_markup.tasks.parts.answer.unit`: string | null
- `p3_markup.tasks.parts.answer.mode`: "equal" | "up_to_constant" | "solution_set" | "interval" | "form" | null
- `p3_markup.tasks.parts.answer.form_target`: "factored" | "simplified" | null
- `p3_markup.tasks.parts.answer.variables`: list[object] | null
- `p3_markup.tasks.parts.answer.variables.name`: string
- `p3_markup.tasks.parts.answer.variables.assumptions`: list["real" | "positive" | "nonnegative" | "integer" | "nonzero"]
- `p3_markup.tasks.parts.answer.key_ideas`: list[string] | null
- `p3_markup.tasks.parts.tests`: list[object] | null
- `p3_markup.tasks.parts.tests.skill`: string | null
- `p3_markup.tasks.parts.tests.new_skill`: object | null
- `p3_markup.tasks.parts.tests.new_skill.name`: string
- `p3_markup.tasks.parts.tests.new_skill.topic_hint`: string
- `p3_markup.tasks.parts.tests.level`: integer
- `p3_markup.tasks.element_id`: string
- `p3_markup.elements`: list[any]
- `p3_markup.exposures`: list[object]
- `p3_markup.exposures.skill`: string
- `p3_markup.exposures.kind`: "explained" | "example" | "method_named" | "solution_shown"
- `p3_markup.exposures.approach`: "example_first" | "attempt_first" | "small_steps" | "prediction_first" | "faded_example" | "interleaving" | "contrast_pair" | "refutation" | "explain_in_own_words" | "visualization" | "none"
- `p3_markup.targetsMisconception`: string | null
- `p3_markup.planUpdate`: object
- `p3_markup.planUpdate.completed_steps`: list[string]
- `p3_markup.planUpdate.current_step`: string
- `p3_markup.planUpdate.next_steps`: list[string]
- `p3_markup.planUpdate.branches`: list[object]
- `p3_markup.planUpdate.branches.description`: string
- `p3_markup.planUpdate.branches.type`: "corrective" | "curiosity" | "external"
- `p3_markup.planUpdate.cancel_requests`: list[string]
- `p3_markup.agentRequests`: list[object]
- `p3_markup.agentRequests.kind`: "viz" | "illustration" | "task_generation" | "solution_space" | "artifact" | "known_check"
- `p3_markup.agentRequests.params`: object
- `p3_markup.agentRequests.params.skill`: string | null
- `p3_markup.agentRequests.params.level`: integer | null
- `p3_markup.agentRequests.params.topic`: string | null
- `p3_markup.agentRequests.params.task`: string | null
- `p3_markup.agentRequests.params.misconception`: string | null
- `p3_markup.agentRequests.params.description`: string | null
- `p3_markup.agentRequests.needed_in_session`: boolean
- `p3_markup.correctionsAcknowledged`: list[integer]
- `p3_markup.decisionReason`: string
- `p3_markup.deadline`: object | null
- `p3_markup.deadline.hub_label`: string | null
- `p3_markup.deadline.new_hub_name`: string | null
- `p3_markup.deadline.deadline_at`: string
- `p3_markup.deadline.coverage`: list[string]

Input blocks: `tools_and_elements`, `profile`, `domain_and_goals`, `conversation`, `session_plan`, `memory_picture`, `materials`, `labels`, `tail`, `guard_feedback`
Label fields: `p1_head.targetSkills`, `p3_markup.tasks.tests.skill`, `p3_markup.tasks.variantOf`, `p3_markup.tasks.options.misconception`, `p3_markup.tasks.pairTaskId`, `p3_markup.exposures.skill`, `p3_markup.targetsMisconception`, `p3_markup.planUpdate.cancel_requests`, `p3_markup.agentRequests.params.skill`, `p3_markup.agentRequests.params.topic`, `p3_markup.agentRequests.params.task`, `p3_markup.agentRequests.params.misconception`, `p3_markup.deadline.hub_label`, `p3_markup.deadline.coverage`
Markers: "memory temporarily unavailable"

## `extractor@3` → `ExtractorOutputV3`

Output fields:

- `elements`: list[object]
- `elements.kind`: "evidence" | "misconception_hypothesis"
- `elements.skill`: string | null
- `elements.new_skill`: object | null
- `elements.new_skill.name`: string
- `elements.new_skill.topic_hint`: string
- `elements.skill_unclear`: boolean
- `elements.level`: integer | null
- `elements.outcome`: "correct" | "incorrect" | "partial" | null
- `elements.help`: "none" | "hint" | "shown_solution"
- `elements.hint_step`: integer | null
- `elements.gap_type`: "no_idea" | "when_to_apply" | "confusion" | "execution" | "slip" | "misconception" | "none"
- `elements.misconception`: string | null
- `elements.new_misconception`: string | null
- `elements.root_cause`: object | null
- `elements.root_cause.skill`: string
- `elements.root_cause.confidence`: "low" | "medium" | "high"
- `elements.quote`: string
- `elements.events`: list[integer]

Input blocks: `events_before`, `episode`, `candidate_skills`, `known_misconceptions`, `gap_types`
Label fields: `elements.skill`, `elements.misconception`, `elements.root_cause.skill`

## `verdict_free@1` → `ModelVerdictOutput`

Output fields:

- `items`: list[object]
- `items.item`: string
- `items.status`: "found" | "missing" | "wrong"
- `items.quote`: string | null
- `outcome`: "correct" | "incorrect" | "partial" | "unclear"
- `confidence`: number
- `misconception`: string | null
- `misconception_hypothesis`: string | null

Input blocks: `problem`, `rubric`, `answer_spec`, `known_misconceptions`, `learner_answer`
Label fields: `misconception`

## `verdict_proof@1` → `ModelVerdictOutput`

Output fields:

- `items`: list[object]
- `items.item`: string
- `items.status`: "found" | "missing" | "wrong"
- `items.quote`: string | null
- `outcome`: "correct" | "incorrect" | "partial" | "unclear"
- `confidence`: number
- `misconception`: string | null
- `misconception_hypothesis`: string | null

Input blocks: `problem`, `rubric`, `answer_spec`, `known_misconceptions`, `learner_answer`
Label fields: `misconception`

## `second_opinion@1` → `ModelVerdictOutput`

Output fields:

- `items`: list[object]
- `items.item`: string
- `items.status`: "found" | "missing" | "wrong"
- `items.quote`: string | null
- `outcome`: "correct" | "incorrect" | "partial" | "unclear"
- `confidence`: number
- `misconception`: string | null
- `misconception_hypothesis`: string | null

Input blocks: `problem`, `rubric`, `answer_spec`, `known_misconceptions`, `learner_answer`
Label fields: `misconception`

## `task_generator@1` → `TaskGeneratorOutput`

Output fields:

- `tasks`: list[object]
- `tasks.condition`: object
- `tasks.condition.text`: string
- `tasks.condition.latex`: string | null
- `tasks.markup`: object
- `tasks.markup.tests`: list[object]
- `tasks.markup.tests.skill`: string | null
- `tasks.markup.tests.new_skill`: object | null
- `tasks.markup.tests.new_skill.name`: string
- `tasks.markup.tests.new_skill.topic_hint`: string
- `tasks.markup.tests.level`: integer
- `tasks.markup.methodNamed`: boolean
- `tasks.markup.variantOf`: string | null
- `tasks.markup.answer`: object
- `tasks.markup.answer.type`: "choice" | "number" | "expression" | "set" | "vector" | "quantity" | "proof" | "free_text" | "drawing" | "short_text"
- `tasks.markup.answer.value`: object | null
- `tasks.markup.answer.value.latex`: string
- `tasks.markup.answer.value.sympy`: string
- `tasks.markup.answer.items`: list[object] | null
- `tasks.markup.answer.items.latex`: string
- `tasks.markup.answer.items.sympy`: string
- `tasks.markup.answer.options`: list[object] | null
- `tasks.markup.answer.options.option_id`: string
- `tasks.markup.answer.options.text`: string
- `tasks.markup.answer.options.correct`: boolean
- `tasks.markup.answer.accepted`: list[string] | null
- `tasks.markup.answer.tol_abs`: number | null
- `tasks.markup.answer.tol_rel`: number | null
- `tasks.markup.answer.sig_digits`: integer | null
- `tasks.markup.answer.unit`: string | null
- `tasks.markup.answer.mode`: "equal" | "up_to_constant" | "solution_set" | "interval" | "form" | null
- `tasks.markup.answer.form_target`: "factored" | "simplified" | null
- `tasks.markup.answer.variables`: list[object] | null
- `tasks.markup.answer.variables.name`: string
- `tasks.markup.answer.variables.assumptions`: list["real" | "positive" | "nonnegative" | "integer" | "nonzero"]
- `tasks.markup.answer.key_ideas`: list[string] | null
- `tasks.markup.options`: list[object] | null
- `tasks.markup.options.option_id`: string
- `tasks.markup.options.misconception`: string | null
- `tasks.markup.options.hypothesis`: string | null
- `tasks.markup.options.neighbor_idea`: boolean
- `tasks.markup.pairTaskId`: string | null
- `tasks.markup.form`: "multistep" | "explain_why" | "what_breaks" | null
- `tasks.markup.parts`: list[object] | null
- `tasks.markup.parts.part`: string
- `tasks.markup.parts.answer`: object
- `tasks.markup.parts.answer.type`: "choice" | "number" | "expression" | "set" | "vector" | "quantity" | "proof" | "free_text" | "drawing" | "short_text"
- `tasks.markup.parts.answer.value`: object | null
- `tasks.markup.parts.answer.value.latex`: string
- `tasks.markup.parts.answer.value.sympy`: string
- `tasks.markup.parts.answer.items`: list[object] | null
- `tasks.markup.parts.answer.items.latex`: string
- `tasks.markup.parts.answer.items.sympy`: string
- `tasks.markup.parts.answer.options`: list[object] | null
- `tasks.markup.parts.answer.options.option_id`: string
- `tasks.markup.parts.answer.options.text`: string
- `tasks.markup.parts.answer.options.correct`: boolean
- `tasks.markup.parts.answer.accepted`: list[string] | null
- `tasks.markup.parts.answer.tol_abs`: number | null
- `tasks.markup.parts.answer.tol_rel`: number | null
- `tasks.markup.parts.answer.sig_digits`: integer | null
- `tasks.markup.parts.answer.unit`: string | null
- `tasks.markup.parts.answer.mode`: "equal" | "up_to_constant" | "solution_set" | "interval" | "form" | null
- `tasks.markup.parts.answer.form_target`: "factored" | "simplified" | null
- `tasks.markup.parts.answer.variables`: list[object] | null
- `tasks.markup.parts.answer.variables.name`: string
- `tasks.markup.parts.answer.variables.assumptions`: list["real" | "positive" | "nonnegative" | "integer" | "nonzero"]
- `tasks.markup.parts.answer.key_ideas`: list[string] | null
- `tasks.markup.parts.tests`: list[object] | null
- `tasks.markup.parts.tests.skill`: string | null
- `tasks.markup.parts.tests.new_skill`: object | null
- `tasks.markup.parts.tests.new_skill.name`: string
- `tasks.markup.parts.tests.new_skill.topic_hint`: string
- `tasks.markup.parts.tests.level`: integer
- `tasks.solution`: string

Input blocks: `topic_name`, `target_skill`, `target_level`, `kind`, `purpose`, `known_misconceptions`, `course_context`, `excluded_families`, `source_problem`
Label fields: `tasks.markup.tests.skill`, `tasks.markup.variantOf`, `tasks.markup.options.misconception`

## `task_checker@1` → `TaskCheckOutput`

Output fields:

- `answer_text`: string
- `answer_math`: object | null
- `answer_math.latex`: string
- `answer_math.sympy`: string
- `methodNamed`: boolean
- `is_variant`: boolean | null
- `levels_fit`: list[boolean]
- `note`: string

Input blocks: `problem`, `options`, `claimed_markup`, `source_problem`
Layer 1: stub, owner's doctrine pending (BLOCKED-B-101)

## `task_arbiter@1` → `TaskCheckOutput`

Output fields:

- `answer_text`: string
- `answer_math`: object | null
- `answer_math.latex`: string
- `answer_math.sympy`: string
- `methodNamed`: boolean
- `is_variant`: boolean | null
- `levels_fit`: list[boolean]
- `note`: string

Input blocks: `problem`, `claimed_markup`, `source_problem`, `generator_solution`, `checker_solution`, `disagreement`
Layer 1: stub, owner's doctrine pending (BLOCKED-B-102)

## `solution_space@1` → `SolutionSpace`

Output fields:

- `key_steps`: list[object]
- `key_steps.statement`: string
- `key_steps.expression`: object | null
- `key_steps.expression.latex`: string
- `key_steps.expression.sympy`: string
- `wrong_branches`: list[object]
- `wrong_branches.sign`: string
- `wrong_branches.gap_type`: "no_idea" | "when_to_apply" | "confusion" | "execution" | "slip" | "misconception" | "none"
- `wrong_branches.misconception`: string | null
- `hints`: list[string]

Input blocks: `problem`, `answer_spec`, `reference_solution`, `known_misconceptions`
Label fields: `wrong_branches.misconception`
Layer 1: stub, owner's doctrine pending (BLOCKED-B-103)

## `analyst@1` → `AnalystOutput`

Output fields:

- `frontier`: list[object]
- `frontier.skill`: string
- `frontier.level`: integer
- `frontier.state`: string
- `gap_hypotheses`: list[object]
- `gap_hypotheses.skill`: string
- `gap_hypotheses.gap_type`: "no_idea" | "when_to_apply" | "confusion" | "execution" | "slip" | "misconception" | "none"
- `gap_hypotheses.reason`: string
- `uncertainty`: object | null
- `uncertainty.question`: string
- `uncertainty.resolving_task`: string
- `plan_divergence`: boolean
- `moves`: list[object]
- `moves.move`: string
- `moves.reason`: string
- `forecast`: list[object]
- `forecast.kind`: "viz" | "illustration" | "task_generation" | "solution_space" | "artifact"
- `forecast.skill`: string | null
- `forecast.level`: integer | null
- `forecast.misconception`: string | null
- `forecast.description`: string
- `manner`: string
- `text`: string

Input blocks: `skills_state`, `misconceptions`, `evidence`, `session_plan`, `goals`, `profile`
Label fields: `frontier.skill`, `gap_hypotheses.skill`, `forecast.skill`, `forecast.misconception`

## `builder@1` → `BuilderOutput`, `HygieneOutput`

Output fields of `BuilderOutput`:

- `items`: list[object]
- `items.name`: string
- `items.kind`: "topic" | "skill"
- `items.decision`: "link" | "create" | "suggest_merge"
- `items.target`: string | null
- `items.topic_hint`: string | null
- `items.same_idea`: string | null
- `items.same_idea_confidence`: "low" | "medium" | "high" | null

Output fields of `HygieneOutput`:

- `pairs`: list[object]
- `pairs.first`: string
- `pairs.second`: string
- `pairs.duplicate`: boolean

Input blocks: `mode`, `subjects`, `candidates`
Label fields: `items.target`, `items.same_idea`, `pairs.first`, `pairs.second`

## `misconception_describer@1` → `MisconceptionDescription`

Output fields:

- `statement`: string
- `triggers`: list[string]

Input blocks: `skill`, `manifestations`
Layer 1: stub, owner's doctrine pending (BLOCKED-B-104)

## `today_planner@1` → `TodayPlannerOutput`

Output fields:

- `items`: list[object]
- `items.recommendation`: string
- `items.tasks`: list[string]
- `items.generate`: boolean
- `items.plan_steps`: list[string]

Input blocks: `recommendations`, `candidate_tasks`, `cards`
Label fields: `items.tasks`
Layer 1: stub, owner's doctrine pending (BLOCKED-B-105)

## `domain_classifier@1` → `DomainClassifierOutput`

Output fields:

- `decision`: "existing" | "new" | "unclear"
- `domain`: string | null
- `new_name`: string | null
- `confidence`: number

Input blocks: `domains`, `text`
Layer 1: stub, owner's doctrine pending (BLOCKED-B-106)

## `viz_builder@1` → `VizBuilderOutput`

Output fields:

- `format`: "html" | "svg"
- `content`: string
- `parameter_values`: list[string]
- `answer_action`: string | null

Input blocks: `description`, `libraries`, `previous_errors`
Layer 1: stub, owner's doctrine pending (BLOCKED-B-107)

## `release_judge@1` → `ReleaseJudgeOutput`

Output fields:

- `winner`: "a" | "b" | "tie"
- `reason`: string

Input blocks: `situation`, `turn_a`, `turn_b`, `criteria`
Layer 1: stub, owner's doctrine pending (BLOCKED-B-108)

## Edit points after G1

- teacher tool names, if tools appear (R9);
- names of label fields (`LABEL_FIELDS`);
- `gap_type` values of `extractor@2` (П-6.4);
- the marker of unavailable memory (`memory temporarily unavailable`);
- the reply language of the teacher from the profile.
- Phase 4: `intent` and `approach` lists of `teacher@2` (D-B-104, owner's doctrine);
- Phase 4: names of the input blocks of the Phase 4 roles, until their context builders (WP11.6, WP12.4–12.6, WP13–WP16) are written (D-B-125);
- Phase 4: the domain label of `domain_classifier@1` (no `LabelMap` kind yet, WP16.4);
- Phase 4: layer 1 of the roles in `DOCTRINE_PENDING` (owner's doctrine).
