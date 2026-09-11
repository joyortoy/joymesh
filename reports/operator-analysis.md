# JoyMux operator analysis

Generated: 2026-08-06T19:52:30.842507+00:00

Schema: `joymux.host.diagnosis.v2`

## Workload summary

- Host: macos aarch64 (8 cores)
- Host CPU: 32.2%
- Memory used: 6.29 / 8.00 GiB
- Swap used: 6.87 / 8.00 GiB
- Processes: 521
- Concurrent AI (heuristic): 10
- Concurrent builds (heuristic): 0

## Top CPU consumers

| CPU % | Class | PID | Name |
|------:|-------|----:|------|
| 36.3 | Chrome | 73466 | Google Chrome Helper (Renderer) |
| 11.1 | Cursor | 1290 | Cursor Helper (Renderer) |
| 6.5 | Cursor | 1155 | Cursor Helper |
| 5.5 | JoyMux Runtime | 37374 | joymux |
| 3.0 | Unknown | 67412 | grok |
| 2.0 | Unknown | 632 | Finder |
| 1.6 | Cursor Extension Host | 88370 | Cursor Helper (Plugin) |
| 1.4 | Node | 60514 | node |
| 1.1 | Unknown | 604 | ChatGPT |
| 1.0 | Chrome | 1289 | Google Chrome Helper |

## Top RAM consumers

| RSS MiB | Class | PID | Name |
|--------:|-------|----:|------|
| 615.0 | Cursor | 1290 | Cursor Helper (Renderer) |
| 507.7 | Chrome | 73466 | Google Chrome Helper (Renderer) |
| 129.1 | Cursor | 603 | Cursor |
| 93.2 | Cursor Extension Host | 88370 | Cursor Helper (Plugin) |
| 88.5 | Cursor | 37281 | Cursor Helper (Renderer) |
| 87.2 | Cursor Extension Host | 28377 | Cursor Helper (Plugin) |
| 85.3 | Cursor | 37275 | Cursor Helper (Renderer) |
| 81.5 | Chrome | 1249 | Google Chrome |
| 76.2 | Cursor | 36895 | Cursor Helper (Renderer) |
| 71.7 | Unknown | 604 | ChatGPT |

## Attribution

| Bucket | CPU sum % | CPU share | RSS share | Processes |
|--------|----------:|----------:|----------:|----------:|
| Browsers | 39.4 | 51.6% | 26.4% | 38 |
| AI tooling | 24.0 | 31.4% | 47.6% | 62 |
| Everything else | 7.6 | 9.9% | 25.6% | 419 |
| JoyMux | 5.5 | 7.2% | 0.4% | 2 |

Host CPU/memory totals are system-wide; bucket shares sum process contributions and are not cgroup-isolated.

## JoyMux health

- Available: true
- Status: OK
- CPU: Some(0.1688113)
- RSS: Some(8781824)
- Note: JoyMux runtime snapshot attached when daemon reachable.

## Findings

- **JoyMux resource use elevated — investigate with evidence** (Medium) — joymux_elevated
  - `JoyMux CPU sum 5.48% RSS 20791296`
- **Host saturated by Chrome (36.3% CPU, pid 73466)** (Medium) — host_saturated_by_chrome
  - `top_cpu=36.27897 class=Chrome bucket=Browsers pid=73466`
- **Swap pressure detected** (High) — swap_pressure_detected
  - `used_swap_bytes=7377715200`
- **Too many browser renderers / browser CPU load** (High) — too_many_browser_renderers
  - `browser_cpu_sum=39.411243 browser_rss_share=26.42299`

## Bottleneck

AI/IDE tooling CPU sum 24.0%, RSS share 47.6%

Confidence: **High**

- `swap_pressure`: System swap used 6.87 GiB / 8.00 GiB (85.9%)
- `excessive_browser_load`: Browsers CPU sum 39.4% (51.6% of listed), RSS share 26.4%
- `ide_pressure`: AI/IDE tooling CPU sum 24.0%, RSS share 47.6%

## Timeline

| Time | Host CPU | Mem GiB | Swap GiB | Top proc | JoyMux CPU |
|------|---------:|--------:|---------:|----------|-----------:|
| 19:52:30 | 32.2% | 6.29 | 6.87 | Chrome 36.3% | 0.00% |

## Process trees (top CPU)

- `1:Unknown(0.0%) → 1249:Chrome(0.5%) → 73466:Chrome(36.3%)`
- `1:Unknown(0.0%) → 603:Cursor(0.3%) → 1290:Cursor(11.1%)`
- `1:Unknown(0.0%) → 603:Cursor(0.3%) → 1155:Cursor(6.5%)`
- `1:Unknown(0.0%) → 603:Cursor(0.3%) → 88370:Cursor Extension Host(1.6%) → 37350:Shell(0.0%) → 37374:JoyMux Runtime(5.5%)`
- `855:Unknown(0.0%) → 872:Shell(0.0%) → 67411:Node(0.0%) → 67412:Unknown(3.0%)`

## Recommendations

- Optimisation advise is a paid add-on. Subscribe via JoyPay (GPU/RAM Optimisation or Bundle) to unlock local tips — Basic includes numbers only.

## Paid wings (local lease)

Paid Optimisation and Enhance Security run locally from your JoyPay lease. Subscribe to unlock richer advise; JoyMux updates keep improving GPU/RAM/security detail over time — no cloud API required for the wing to work.

GPU observation: available=true temp_sensor=false util%=n/a (never invented) source=`ioreg+system_profiler`

### RAM Optimisation (entitled=false, status=`subscription_required`, codes=`ram_optimisation_subscription_required`)

- Subscribe to RAM Optimisation ($6/mo) or Optimisation Bundle ($10/mo) via JoyPay.

### GPU Optimisation (entitled=false, status=`subscription_required`, codes=`gpu_optimisation_subscription_required`)

- Subscribe to GPU Optimisation ($6/mo) or Optimisation Bundle ($10/mo) via JoyPay.
- JoyMux dashboard shows a locked GPU usage % tile until you upgrade — unlock live utilisation and thermal scheduling.

### Enhance Security (entitled=false, status=`subscription_required`, codes=`enhance_security_subscription_required`)

- Subscribe to Enhance Security ($8/mo) via JoyPay.

## Limitations

- Thread count: unsupported via sysinfo 0.33 on this surface
- Per-process FD for non-self PIDs on macOS: environment-dependent and unverified
- Concurrent terminals: unsupported
- Private prompts / model tokens: unsupported (never collected)
- CPU% requires sampler priming; first sample may be noisy
- Attribution shares are over listed processes, not a kernel cgroup accounting
- Does not kill, throttle, or reschedule processes
- ResourceLimits.enforced soft limits are modeled but not active for session kill/CPU caps in this build.
