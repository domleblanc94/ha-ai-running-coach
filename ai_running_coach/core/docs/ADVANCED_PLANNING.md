# Advanced planning

Economical mode remains the default. Advanced is an explicit choice to give the coach more activity context and a larger answer budget. It does not change your model, training availability, goals, heart-rate zones, normal daily request allowance or validation rules.

In Home Assistant, open Connections, choose **Advanced** planning and save. In standalone `.env`, set `COACH_PLANNING_MODE=advanced`. Saving this setting makes no AI call. The next successful review uses it. Use Generate new plan when you want a replacement block; normal reviews adapt the existing plan. Switching mode does not retroactively expand a saved review.

| Setting | Economical default | Advanced default |
| --- | --- | --- |
| Maximum output tokens per request | 4,096 | 16,384 |
| Maximum input characters | 60,000 | 120,000 |
| New-plan horizon | 7 days | Up to 28 days |
| Individually reviewable activity window | 7 days | 14 days, latest 40 activities |
| Additional activity context | Eight-week aggregates | Previous 14 days, latest 40 activities, plus eight-week aggregates |
| Provider read timeout | 60 seconds | 180 seconds |

Advanced asks for a coherent progression with week-by-week purpose, evidence-based rationale, recovery or missed-session alternatives and review triggers. The saved **Coaching plan overview** appears on the training dashboard and in `training_plan.md`. The coach still chooses frequency, volume and intensity. A shorter block can be appropriate when evidence is limited, a race is near or uncertainty is high. Structured workout exports remain optional; every quality session must still have executable instructions.

The athlete's **Review detail** setting remains independent: concise keeps the review summary short even in Advanced, while the separate planning overview and race-section guidance provide the extra planning detail. Standard and detailed permit longer activity comments and summaries in Advanced.

Race planning uses the same mode. Advanced supplies more running observations and allows fuller elevation-aware pacing explanations, section-specific decisions and conditional fallback guidance. It retains the two strategies, conservative opening, second-half pickup rules and uncertainty checks. Regenerate a race plan to obtain a new explanation; a mode change invalidates its cached AI result.

## Cost and limits

Advanced can consume substantially more paid tokens or free-tier quota and take longer. Output limits are ceilings, not a promise of answer length. Providers may count reasoning tokens inside the output allowance; a long reasoning response can still be truncated. Model-specific limits and account availability apply. Choose an accessible model explicitly: the app never silently selects a more expensive one. Advanced leaves Gemini's model-default thinking enabled instead of forcing economical minimal/no-thinking settings. OpenAI uses `max_completion_tokens` on its official endpoint and leaves model-dependent sampling/reasoning settings at their defaults.

The normal daily allowance is shared by training and race reviews. Advanced adds no automatic calls, fallback attempts or request-limit override. Invalid or truncated output preserves the last successful plan and dashboard. Review the saved error before retrying; any manual additional request remains an explicit action.

Economical mode continues to use `COACH_MAX_OUTPUT_TOKENS` (256–8192), `COACH_MAX_INPUT_CHARS` (8000–120000) and `COACH_HTTP_TIMEOUT_SECONDS` (5–120). Advanced uses **separate** keys so existing economical settings remain available when you switch back:

```dotenv
COACH_PLANNING_MODE=advanced
COACH_ADVANCED_MAX_OUTPUT_TOKENS=16384
COACH_ADVANCED_MAX_INPUT_CHARS=120000
COACH_ADVANCED_HTTP_TIMEOUT_SECONDS=180
```

Advanced bounds are 256–32768 output tokens, 8000–240000 input characters and 5–300 seconds per provider read. Values outside a bound are clamped; non-integer settings are rejected. `python setup_coach.py --doctor --offline` prints the effective mode and budgets without contacting a provider. Home Assistant shows the same settings. Its worker deadline is bounded at 20 minutes for Advanced (15 minutes for Economical).

Mode, effective budgets and prompt version are part of request identity. A saved invalid response can only be replayed under the original, unchanged configuration and evidence. Changing a budget does not erase used requests.
