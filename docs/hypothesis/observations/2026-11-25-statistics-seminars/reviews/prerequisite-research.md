# Исследование: насколько LLM точны в связях «нужно до»

> **Вывод модели, не факт.** Отчёт субагента (Claude Opus, общий агент), 2026-11-25 19:35 UTC. Сохранён без правок; оценка его выводов — в карточках гипотез.

## Задание, которое получил агент

```text
Research task (web), read-only, no repo edits. I need evidence, not opinions, for a design decision in an AI tutor: an LLM proposes "prerequisite" (A must be learned before B) relations between topics/concepts of a learner's subject, from topic names + short descriptions (no course data), and the system shows them as a topic graph and uses them for study order.

Find and summarize, with links and the key numbers:
1. Benchmarks and results for concept prerequisite relation extraction/learning: classic (e.g. RefD — Liang et al. 2015; Pan et al. 2017 MOOC prerequisite; LectureBank — Li et al. 2019; datasets AL-CPL, University Course dataset) — typical F1/accuracy.
2. Studies of LLMs (GPT-3.5/4/4o, Llama etc.) on prerequisite relation prediction or knowledge-graph/concept-map construction for education (2023–2026): zero-shot vs fine-tuned, precision vs recall, typical failure modes (over-linking, transitive edges, direction errors), whether giving the model short definitions helps.
3. Evidence on automatically built concept maps / knowledge graphs from course materials or textbooks with LLMs and how they compare to expert maps.
4. Any evidence on whether showing prerequisite structure / ordering by prerequisites improves learning (e.g. ALEKS/knowledge space theory outcomes, Math Academy, prerequisite-aware sequencing studies), and how sensitive learners are to wrong prerequisite edges.
5. Practical recommendations from these papers: thresholds, verification steps, human-in-the-loop, using direction checks.

Be strict about what each source actually shows; mark anything you could not verify. Report in English, under 1000 words, with a list of sources (URL + one-line description).
```

## Отчёт

## Prerequisite edges proposed by an LLM: what the research shows

Each number is tagged by where I saw it: [V] in the paper's own text or tables, [A] from the abstract or a summary page only, [S] from a search snippet only (unverified).

The main caveat applies to every benchmark below: they classify concept pairs someone already chose, usually with a balanced or oversampled set of negatives. Your system instead proposes edges freely across all topic pairs, and most of those pairs are not prerequisites. Precision there will be much lower than these F1 scores suggest. Only InstructKG tests an open-generation setting, and its scores are far lower.

### 1. Classic benchmarks (supervised, mostly Wikipedia features)
- **RefD, Liang et al. 2015.**
  - CrowdComp, testing on a domain not seen in training: accuracy about 60–61%, roughly equal to the MaxEnt classifier [V].
  - Course dataset (oversampled to balance classes): CS accuracy 77.1, P 82.3, R 69.1, F1 75.1; Math F1 71.9 [V].
- **Pan et al. 2017 (MOOC).**
  - Random-forest F1 69–73% across Machine Learning, Data Structures and Algorithms, and Calculus [V].
  - Three annotators' agreement was only kappa 0.59–0.65 [V]. Experts themselves agree only moderately on prerequisite edges.
- **AL-CPL, Liang et al. 2018.** Random-forest F1 by domain: Data Mining 76.7, Geometry 89.5, Physics 69.9, Precalculus 88.6. Uses 5-fold cross-validation within each domain [V].
- **Recent graph models (ProPRL 2026 preprint).** F1 0.83–0.92 on the University Course Dataset, LectureBank and MOOC. In the same table RefD gets F1 0.66–0.68 [V], but those baseline numbers are the preprint's own re-implementations.

### 2. LLMs predicting prerequisites
- **Yang et al. 2024 (LectureBankCD; the same group's Graphusion reuses this table, so it is one result, not two).** [V]
  - Zero-shot GPT-4: accuracy 0.746, F1 0.777. The best supervised baseline (Node2vec) gets F1 0.747. A Llama fine-tuned with the graph is best at F1 0.812.
  - Chain-of-thought hurt: computer-vision F1 fell from 0.76 to 0.42.
  - Retrieval (RAG) did not help GPT-4.
  - Fine-tuning Llama2-70B on other data hurt.
  - The authors report a "pronounced" bias toward answering "yes, prerequisite", even for GPT-4. They also suspect GPT had seen the data during training.
- **InstructKG 2026 (open-ended graph built from lectures, Qwen2.5-14B).** This is the result closest to your setting. [V]
  - On Pan's MOOC labels: P 0.43 / R 0.29 / F1 0.35 (Machine Learning) and P 0.35 / R 0.24 / F1 0.29 (Data Structures and Algorithms). Direction accuracy was only 0.62–0.63.
  - The 1,591 wrong or partial edges broke down as:
    - 49% plausible but not supported by evidence
    - 28% reversed direction
    - 22% wrong relation type
    - 1% linking unrelated concepts
  - Keeping only edges with 2 or more supporting chunks cut the graph in half without improving accuracy (0.567 vs 0.562).
  - Human judges rated 58–85% of edges correct, depending on the course.
- **Educational knowledge-graph benchmark, 2024 (project page only, not peer-reviewed; column meanings undefined)** [A].
  - Models swing to extremes. On one-hop checks GPT-4 accepted only 28.6% of true edges but rejected 96.8% of false ones (DBE-KT22). Moonshot did the opposite: 92.4% on true edges, 24.6% on false ones.
  - On three-hop questions GPT-4 got 0% of false chains right, so it over-accepts transitive chains.
  - Accuracy falls as the subgraph grows from 5 to 15 nodes.
- **Kyushu 2025 (GPT-3.5 / 4o / 4o-mini, MOOCCube).** Concept-generation numbers are in tables [V], but relation results appear only in figures. Adding more context sometimes "introduces noise" [V].
- **ESCO-PrereqSkill 2025 (13 LLMs, names plus descriptions).** BERTScore F1 of 0.81–0.83 [V]. This measures text similarity between lists of prerequisites, not edge accuracy.
- **Does precision or recall suffer more?** The evidence conflicts: Yang et al. and Moonshot lean toward too many edges, GPT-4 on the benchmark and Gemini toward too few. An MDPI 2025 paper reportedly finds high precision and lower recall [S, the page returned 403].
- **Do short definitions help?** Weak evidence only:
  - In Yang et al., Wikipedia intros improved Llama while lecture documents hurt it. This is shown in a figure without numbers [V].
  - ESCO used descriptions but ran no comparison without them.

### 3. LLM concept maps compared with expert maps
- **Szydlak et al., Medical Teacher 2026 (10 virtual-patient cases)** [A]. ChatGPT maps had significantly more concepts and links (p<0.001). Experts rated their clinical validity no differently from expert maps. Educators found weak abstraction and prioritisation and possible cognitive overload, and the authors say expert refinement is needed.
- **Graphusion 2024, expert ratings on a 1–3 scale** [V]. Relations scored 2.08–2.37, entities 2.83–2.92. Relations are consistently the weaker part.
- **Han and Choi, BEA 2025, GPT-4o-mini maps from texts** [V, abstract]. In 14 users, perceived cognitive load fell 31.5% with similar comprehension accuracy. There was no expert comparison.
- **Zhai 2025 systematic review (28 studies)** [A]. It reports no direct comparison with expert maps and lists validity as an open problem.

### 4. Does prerequisite ordering improve learning?
- **ALEKS (knowledge space theory) meta-analysis, Fang et al. 2019** (15 studies, 24 samples) [A]. ALEKS was "as good as, but not better than" traditional teaching. I could not verify effect sizes.
- **Mu et al., ITS 2021** (Korean-vocabulary web game, 117 adaptive vs 89 fixed learners) [V]. Adaptive progression over an automatically built prerequisite graph gave higher efficiency (1.7 vs 1.2 nodes per minute) but no significant difference in total learning. It was compared with an expert-designed fixed sequence, not with "no graph".
- **Concept maps in general (Schroeder et al. 2018).** Studying maps g=0.43, constructing them g=0.72 [S]. This is about maps, not prerequisite ordering.
- **Math Academy.** I found no independent outcome study. Its graph is hand-built, per the company's blog [S].
- **Sensitivity to wrong edges.** I found no direct evidence. Adjei's WPI dissertation (2018) uses randomized experiments to refine prerequisite graphs, but I could not read its results.

### 5. Recommendations
**Done in the papers:**
- Score each pair from −1 to 1 and retry if both directions come back positive; have the model return the numbers of listed candidates rather than free text (Kyushu).
- Store the evidence and reason for each edge (InstructKG). Note that filtering by amount of evidence did not help.
- Use a fusion step that merges the graph across pieces of material (Graphusion), which raised relation ratings from 2.08 to 2.37.
- Avoid chain-of-thought for yes/no edge decisions (Yang et al.).
- Use scores instead of yes/no answers: on the benchmark page, AUROC rose from 70.5 to 89.3 for GPT-4.

**My synthesis (not tested in these papers):**
- Ask about both directions separately and drop contradictory pairs.
- Reduce the graph to direct edges only (transitive reduction) and check for cycles.
- Show edges as suggestions the learner or teacher can correct, not as hard locks on study order.
- Expect around 30–40% wrong or reversed edges in open generation.

### Sources
- https://aclanthology.org/D15-1193/ — RefD, Liang et al. EMNLP 2015
- https://keg.cs.tsinghua.edu.cn/persons/jietang/publications/ACL17-Pan-et-al-Prerequisite-Relationship-MOOCs.pdf — Pan et al. ACL 2017, MOOC datasets and annotator kappa
- https://clgiles.ist.psu.edu/pubs/eaai18.pdf — AL-CPL, Liang et al. AAAI 2018
- https://arxiv.org/abs/1811.12181 — LectureBank, Li et al. AAAI 2019
- https://arxiv.org/html/2608.03006 — ProPRL 2026 preprint, results on University Course Dataset, LectureBank and MOOC
- https://arxiv.org/html/2402.14293 — Yang et al. 2024, GPT-4 zero-shot on LectureBankCD, ablations
- https://arxiv.org/html/2410.17600 — Graphusion, expert ratings of the knowledge graph
- https://arxiv.org/html/2602.17111 — InstructKG 2026, open graph construction and error breakdown
- https://ai-for-edu.github.io/Evaluating-Large-Language-Models-with-Educational-Knowledge-Graphs-on-Prerequisite-Relationships/benchmark — LLM prerequisite benchmark tables (project page)
- https://arxiv.org/html/2504.08856v1 — Kyushu GPT study on course concepts and relations
- https://arxiv.org/html/2507.18479v1 — ESCO-PrereqSkill, 13 LLMs zero-shot
- https://www.mdpi.com/2504-4990/7/3/103 — LLM concept and relation extraction (unverified, 403)
- https://avesis.gazi.edu.tr/yayin/a31b5012-f36d-4422-afe9-2696b275a31e — ChatGPT vs expert concept maps, Medical Teacher
- https://aclanthology.org/2025.bea-1.58.pdf — LLM concept maps and cognitive load, BEA 2025
- https://scale.stanford.edu/ai/repository/generative-large-language-models-knowledge-representation-systematic-review-concept — systematic review of LLM concept maps
- https://digitalcommons.memphis.edu/facpubs/7254 — ALEKS meta-analysis, Fang et al. 2019
- https://cs.stanford.edu/~ebrun/mu_its_2021.pdf — Mu et al. ITS 2021, adaptive curriculum-graph experiment
- https://link.springer.com/doi/10.1007/s10648-017-9403-9 — concept-map meta-analysis, Schroeder et al. 2018
- https://digital.wpi.edu/show/fb494858j — Adjei dissertation on refining prerequisite graphs with randomized trials (results not read)
- https://justinmath.com/how-math-academy-creates-its-knowledge-graph/ — Math Academy hand-built graph (company blog)
