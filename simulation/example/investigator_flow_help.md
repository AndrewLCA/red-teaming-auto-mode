### Setup

Set environment variable ANTHROPIC_API_KEY
Set environment variable OPENAI_API_KEY if using an OpenAI model anywhere (ex. w investigator)

Set up and confirm working with:
In simulation directory:
```
uv sync --no-dev # or just 'uv sync' haven't seen problems with either
# Check for model connection. Success if get PONG reply from each model
uv run --no-dev python -m auto_mode_eval.model_utils -m opus5 -m opus48
# If on Windows run $env:PYTHONUTF8 = "1"
# Do a small run with low classification budget and no grading (not the 10-probe investigator, monitor model still makes calls)
uv run --no-dev flow run example/01_investigator_flow.py -A classification_budget=2 -A grade=0
# actual experiment 
uv run --no-dev flow run example/01_investigator_flow.py -A monitor=expensive -A classification_budget=10 -A epochs=1
```

### Types of presets for monitor:

| Monitor | Main distinction | Tool results visible? | Agentic reasoning? | Fast first-stage filter? |
|---|---|---|---|---|
| `expensive` | Baseline Auto Mode monitor | No | No | Yes |
| `results` | Includes tool outputs in context | Yes | No | Yes |
| `cascade` | Adds agentic evaluation | No | Yes | Yes |
| `agentic` | Uses agentic evaluation without fast filter | No | Yes | No |
| `guardian` | Codex's separate security reviewer | Not specified | Separate architecture | Not specified |

`uv run --no-dev flow run example/01_investigator_flow.py -A monitor=expensive,results,cascade -A classification_budget=10`

### Denial of request from safety classifier:
+ Fallback model: [InspectAI documentation on Fallbacks](https://inspect.aisi.org.uk/fallbacks.html)

    
+ Use different model entirely:
    uv run --no-dev flow run example/01_investigator_flow.py -A model=opus48 -A reasoning_effort= -A classification_budget=2 -A grade=0

    (Currently is timing out)

    uv run --no-dev flow run example/01_investigator_flow.py -A model=gpt41 -A reasoning_effort= -A classification_budget=2 -A grade=0

### Running with NVIDIA free endpoint for GLM 5.3 flash:

```
$env:NVIDIA_API_KEY = "nvapi-your-key"
$env:NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
$env:OPENAI_USE_CHAT_COMPLETIONS = "1"
$env:OPENAI_API_KEY = $env:NVIDIA_API_KEY
$env:OPENAI_BASE_URL = $env:NVIDIA_BASE_URL
```
alternatively, modify so in `model_utils.py`:
```
GLM_53FLASH = AgentModel(
    "glm53f",
    "openai/z-ai/glm-5.3-flash",
    GenerateConfig(
        max_tokens=4096,
        temperature=0.5,
        top_p=1,
    ),
    model_args={
        "responses_api": False,
        base_url="https://integrate.api.nvidia.com/v1",
    },
)
```

Note: on other flows, if you want to use glm53f or similar, because of model_args, must change pattern so you do 
```
from auto_mode_eval.model_utils import resolve_model

...
for m in models:
        config = m.config.model_copy(update={"reasoning_effort": reasoning_effort}) if reasoning_effort else m.config

        resolved_model = resolve_model(m) if m.model_args else m.model # Add this, so it processes the model_args

        for cell in cells:
            tasks.append(
                FlowTask(
                    ...
                    model = resolved_model, # change to this
```

### On run:
- There should be an output to logs
    - `[DATE]-investigator-[model]-[modelpreset]_[key].eval`
        A compressed Inspect AI archive with the main evaluation log with execution records, model interaction, and the full transcript
        
        View by:
        + going to directory with the `logs` folder.
        + running `uv run inspect view`
    - `logs.json`
        Summary of the evaluation with config, score, and metadata
    - `eval-set.json`
        evaluation tasks that ran and their model configurations
    - `flow.yaml`
        Configuration used to execute flow, monitor prompts and settings included
    - `flow-requirements.txt`
        Dependency versions for reproducing the run
    - `.eval-set-id`
        id associated with the evaluation files of the same run