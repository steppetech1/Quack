-- 01-topics-and-skills
with ev as (select * from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and domain_id='8c604bb9-7c1c-5cbc-8a45-95d388ae97fc'), tp as (select payload->>'topic_id' id, payload->>'name' n, seq from ev where type='topic.created'), sk as (select payload->>'skill_id' id, payload->>'topic_id' t, payload->>'name' n from ev where type='skill.created') select tp.n topic, count(sk.id) skills, string_agg(sk.n, ' | ' order by sk.n) skill_names from tp left join sk on sk.t=tp.id group by tp.n, tp.seq order by tp.seq

-- 02-parser-teaches-vs-uses
select j.payload->>'title' title, count(*) chunks, sum(jsonb_array_length(c->'teaches')) teaches, sum(jsonb_array_length(c->'uses')) uses from ops.jobs j, jsonb_array_elements(j.payload->'chunks') c where j.learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and j.payload->>'step'='structure' group by 1 order by 1

-- 03-graph-totals
with ev as (select * from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and domain_id='8c604bb9-7c1c-5cbc-8a45-95d388ae97fc') select (select count(*) from ev where type='topic.created') topics, (select count(*) from ev where type='skill.created') skills_from_material, (select count(*) from ev where type='skill.placed') provisional_skills_placed, (select count(*) from ev where type='edge.added' and payload->>'kind'='requires') requires_edges, (select count(*) from ev where type='hub.created') hubs

-- 04-event-types
select type, count(*) from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' group by type order by 2 desc

-- 05-hubs
select payload->>'name' hub, payload->>'kind' kind, domain_id from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and type='hub.created' order by seq

-- 06-task-markup-new-skill
select count(*) filter (where (payload->'markup')::text like '%"new_skill": {%') tasks_with_new_skill, count(*) tasks from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and type='task.created'

-- 07-same-files-two-domains
select domain_id, payload->>'name' skill from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and type='skill.created' and payload->>'name' ilike any(array['%independ%','%contingency%','%replacement%','%mutually%','%sample space%']) order by 2, 1

-- 08-llm-usage-by-role
select role, count(*) calls, round(avg(input_tokens)) avg_in, round(avg(cached_input_tokens)) avg_cached, round(avg(output_tokens)) avg_out, round(avg(reasoning_tokens)) avg_reasoning from ops.llm_usage group by role order by calls desc

-- 09-structure-jobs
select payload->>'step' step, kind, status, attempts, left(coalesce(last_error,''),160) last_error, created_at from ops.jobs where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and kind like 'material.%' order by created_at


-- 11-llm-usage-this-learner
select role, model, prompt_version, status, count(*) calls, sum(input_tokens) in_tok, sum(cached_input_tokens) cached, sum(output_tokens) out_tok, round(sum(cost_usd),4) cost_usd, round(avg(latency_ms)) avg_latency_ms from ops.llm_usage where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' group by 1,2,3,4 order by calls desc

-- 12-materials
select title, domain_id, mime, pages_count, status, created_at from content.materials where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' order by created_at

-- 13-llm-calls-by-hour
select date_trunc('hour', created_at) hour_utc, role, count(*) calls, count(*) filter (where status<>'ok') not_ok from ops.llm_usage where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' group by 1,2 order by 1,2

-- 14-structure-events-by-hour
select date_trunc('hour', occurred_at) hour_utc, type, count(*) from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and domain_id='8c604bb9-7c1c-5cbc-8a45-95d388ae97fc' and type in ('topic.created','skill.created','skill.placed','hub.created','edge.added','task.created','skill.merged','topic.merged','material.uploaded','hub.deleted') group by 1,2 order by 1,2

-- 15-second-upload-context
select date_trunc('minute', occurred_at) minute_utc, type, actor, session_id, count(*) from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and occurred_at between '2026-11-25 18:00' and '2026-11-25 18:10' group by 1,2,3,4 order by 1

-- 16-baseline-first-upload
-- domain «statistics», only events before the second upload (2026-11-25 18:05 UTC)
with ev as (select * from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and domain_id='8c604bb9-7c1c-5cbc-8a45-95d388ae97fc' and occurred_at < '2026-11-25 18:05')
select (select count(*) from ev where type='topic.created') topics_created,
       (select count(*) from ev where type='topic.merged') topics_merged,
       (select count(*) from ev where type='topic.created') - (select count(*) from ev where type='topic.merged') topics_alive,
       (select count(*) from ev where type='skill.created') skills_created,
       (select count(*) from ev where type='skill.placed') skill_placed_events,
       (select count(distinct payload->>'skill_id') from ev where type='skill.placed') skills_placed_distinct,
       (select count(*) from ev where type='hub.created') hubs,
       (select count(*) from ev where type='task.created') tasks,
       (select count(*) from ev where type='task.created' and (payload->'markup')::text like '%"new_skill": {%') tasks_with_new_skill,
       (select count(*) from ev where type='edge.added' and payload->>'kind'='requires') requires_edges;

-- 17-placed-vs-created
-- are skill.placed ids skills from material (skill.created) or provisional skills (projector-only, no skill.created)?
with c as (select payload->>'skill_id' id from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and type='skill.created'),
     p as (select payload->>'skill_id' id, payload->>'topic_id' t, domain_id, occurred_at from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and type='skill.placed')
select p.domain_id, (p.occurred_at < '2026-11-25 18:05') first_upload, count(*) placed_events, count(distinct p.id) distinct_skills,
       count(distinct p.id) filter (where p.id in (select id from c)) placed_ids_that_are_material_skills
from p group by 1,2 order by 1,2;

-- 18-topics-final-counts
-- domain «statistics»: per topic, material skills (skill.created) + placed skills (skill.placed), merged topics marked
with ev as (select * from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and domain_id='8c604bb9-7c1c-5cbc-8a45-95d388ae97fc'),
tp as (select payload->>'topic_id' id, payload->>'name' n, seq, occurred_at from ev where type='topic.created'),
mg as (select payload->>'from_id' f, payload->>'into_id' i from ev where type='topic.merged'),
cr as (select payload->>'topic_id' t, count(*) n from ev where type='skill.created' group by 1),
pl as (select payload->>'topic_id' t, count(distinct payload->>'skill_id') n from ev where type='skill.placed' group by 1)
select tp.n topic, to_char(tp.occurred_at,'MM-DD HH24:MI') created_utc, coalesce(cr.n,0) material_skills, coalesce(pl.n,0) placed_skills,
       case when tp.id in (select f from mg) then 'merged into ' || (select t2.n from tp t2 where t2.id=(select i from mg where f=tp.id)) else '' end merged
from tp left join cr on cr.t=tp.id left join pl on pl.t=tp.id order by tp.seq;

-- 19-hub-topic-counts
-- topics per hub: last hub.topics_changed per hub
with h as (select payload->>'hub_id' id, payload->>'name' n, domain_id, occurred_at from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and type='hub.created'),
tc as (select distinct on (payload->>'hub_id') payload->>'hub_id' id, jsonb_array_length(payload->'topics') k from core.events where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and type='hub.topics_changed' order by payload->>'hub_id', seq desc)
select h.n hub, h.domain_id, to_char(h.occurred_at,'MM-DD HH24:MI') created_utc, tc.k topics_in_hub from h left join tc on tc.id=h.id order by h.occurred_at;

-- 20-new-skill-names
-- names the task markup invented via new_skill, per domain and upload (tasks whose markup has parts[].tests[].new_skill)
with e as (select domain_id, occurred_at, actor, payload->'markup'->'parts' parts from core.events
           where learner_id='62afff2d-048d-4966-b5b5-81bb1ae79484' and type='task.created' and jsonb_typeof(payload->'markup'->'parts')='array'),
pt as (select e.*, p from e, jsonb_array_elements(e.parts) p where jsonb_typeof(p->'tests')='array')
select pt.domain_id, (pt.occurred_at < '2026-11-25 18:05') first_upload, pt.actor, t->'new_skill'->>'name' new_skill_name
from pt, jsonb_array_elements(pt.p->'tests') t
where jsonb_typeof(t->'new_skill')='object'
order by 1,2 desc,4;
