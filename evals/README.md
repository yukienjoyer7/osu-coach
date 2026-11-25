# Coaching response checks

These public development cases exercise a few coaching behaviors. They do not measure improvement, elite-level expertise, tool use, or an agent's ability to take a player to top 100. They are not a validated benchmark and are unsuitable as a held-out test because the prompts and rubrics are visible in the repository.

The replay cases describe external evidence and adversarial text in a prompt. They do not exercise an actual tool response, establish an enforcement boundary, or demonstrate resistance to other injection attempts.

1. Run `python evals/case.py` to list cases, then `python evals/case.py CASE_ID` to print a player prompt without the rubric.
2. Give the prompt to the agent in a fresh conversation with its usual coaching instructions. Record its response, model and version, harness, instructions, available tools, date and any tool failures. Do not give it the rubric as part of the prompt.
3. After recording the response, run `python evals/case.py CASE_ID --rubric`. A human reviewer marks each criterion pass, fail or uncertain and cites the relevant response text. Record omissions as well as harmful advice; do not infer a pass from a keyword. Keep uncertain results distinct from passes.
4. For comparisons, use the same instructions and tool access, repeat runs, and have independent reviewers adjudicate disagreements. Retain complete transcripts and notes outside the repository if they contain player data. Compare individual failures instead of relying on a single aggregate score.

For a stronger evaluation, develop additional private, varied cases with experienced osu! coaches, include realistic tool interactions and player follow-ups, and measure actual player outcomes over time. Keep those cases separate from prompts and instructions used to develop the agent. A good response to these examples is not proof of real-world coaching effectiveness.
