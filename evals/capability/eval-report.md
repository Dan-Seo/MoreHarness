# eval report

- repeat: 3
- workdir: `C:\Users\emper\AppData\Local\Temp\harness-eval-1jg6zt2q`

| arm | grader_success_rate | hidden_ac_pass_rate | escape_rate | false_block_rate |
|---|---|---|---|---|
| raw | 1.00 | 1.00 | n/a | n/a |
| harness-full | 1.00 | 1.00 | 0.00 | n/a |

## raw

- wall_time_s: 중앙값 271.411 [250.039, 354.84]
- agent_time_s: 중앙값 270.488 [249.246, 353.69]
- tokens_in: 중앙값 1,012 [676, 1,012]
- tokens_out: 중앙값 18,383 [16,781, 24,414]
- cost_usd: 중앙값 2.26 [1.993, 2.67]
- context_tokens: n/a

## harness-full

- wall_time_s: 중앙값 354.901 [324.497, 666.483]
- agent_time_s: 중앙값 352.048 [321.325, 660.523]
- tokens_in: 중앙값 1,156 [900, 2,055]
- tokens_out: 중앙값 23,283 [21,829, 44,366]
- cost_usd: 중앙값 3.184 [2.867, 5.587]
- context_tokens: 중앙값 515 [515, 515]
